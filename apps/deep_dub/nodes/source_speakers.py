"""完整PCM的声学角色时间候选；区间、重叠和原始越界均保留，不判人物身份。"""
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import time

import httpx

from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.nodes.source_asr import MAX_SAMPLES, checked_coverage, pcm_info
from apps.deep_dub.nodes.transcribe import AudioModelSession
from apps.deep_dub.services.blocking import await_blocking, file_identity

RULE = 'source-nemotron-speaker-timing-v1'
OPTIONS = {'speaker_threshold': '0.5', 'speaker_min_frames': '0', 'speaker_pad_frames': '0',
           'return_frame_probabilities': 'false'}
SESSION_OPTIONS = {'nemotron_3_diar.latency_profile': 'very_high'}


def summarize(turns, pcm):
    if pcm.get('sample_rate')!=16000 or type(pcm.get('sample_count')) is not int or not 0<pcm['sample_count']<=MAX_SAMPLES:
        raise BusinessError('角色时间需要有效的完整16kHz样本坐标',502,'SOURCE_SPEAKER_RESPONSE_INVALID')
    if not isinstance(turns, list) or len(turns) > 50000:
        raise BusinessError('角色响应区间数量无效', 502, 'SOURCE_SPEAKER_RESPONSE_INVALID')
    invalid = []; events = []; speakers = set()
    for index, turn in enumerate(turns):
        if (not isinstance(turn, dict) or set(turn) != {'start_sample','end_sample','speaker_id','confidence'}
            or any(type(turn.get(k)) is not int for k in ('start_sample','end_sample'))
            or not isinstance(turn.get('speaker_id'), str) or not re.fullmatch(r'speaker_[0-7]',turn['speaker_id'])
            or type(turn.get('confidence')) not in (float,int) or not math.isfinite(turn['confidence'])
            or not 0 <= turn['confidence'] <= 1):
            raise BusinessError('角色响应类型、模型角色标签或置信值无效', 502, 'SOURCE_SPEAKER_RESPONSE_INVALID')
        speakers.add(turn['speaker_id'])
        start, end = turn['start_sample'], turn['end_sample']
        if not 0 <= start < end <= pcm['sample_count']:
            invalid.append(index); continue
        events.extend([(start,turn['speaker_id'],1),(end,turn['speaker_id'],-1)])
    # 活动并集与多角色重叠分开；同一角色的重复区间不会伪造成多角色重叠。
    active = Counter(); previous = 0; union = overlap = 0
    for sample, speaker, delta in sorted(events):
        count = sum(v > 0 for v in active.values())
        if count: union += sample-previous
        if count > 1: overlap += sample-previous
        active[speaker] += delta; previous = sample
    return {'turn_count':len(turns),'speakers':sorted(speakers),'speaker_count':len(speakers),
        'invalid_turn_indexes':invalid,'valid_timestamps':not invalid,'active_samples':union,
        'multi_speaker_overlap_samples':overlap,'active_fraction':union/pcm['sample_count'],
        'overlap_fraction':overlap/pcm['sample_count'],
        'scope':'有效区间的活动并集/多角色重叠；原始异常不裁改。角色标签非人物身份，置信值未校准，非内容或字幕审核'}


def verify(measurement, primary):
    try:
        assert measurement['rule_revision'] == RULE and measurement['pcm'] == primary['pcm']
        assert measurement['input'] == primary['normalized_input']
        assert measurement['model']['family'] == 'nemotron_3_diar'
        assert measurement['request_options'] == OPTIONS and measurement['session_options'] == SESSION_OPTIONS
        assert measurement['text_prompt'] == ''
        assert measurement['speaker_identity'] == measurement['turn_boundary_review'] == measurement['subtitle_timing_review'] == 'not_measured'
        session = measurement['session']
        assert session['operation_stopped'] is True and session['initially_unloaded'] is True
        assert type(session['requests']) is int and session['requests'] == 1 and session['warm_cache_checks'] == 0
        assert type(measurement['submitted_samples']) is int and measurement['submitted_samples'] == primary['pcm']['sample_count']
        return summarize(measurement['turns'], measurement['pcm'])
    except (KeyError,TypeError,ValueError,AssertionError):
        raise BusinessError('角色时间证据与原始PCM、模型或执行会话不符',409,'SOURCE_SPEAKER_EVIDENCE_INVALID') from None


async def analyze(config, pool, folder, pcm_file, primary, timeout):
    checked_coverage(primary)
    if config.family != 'nemotron_3_diar':
        raise BusinessError('角色分析必须配置说话人模型',422,'MODEL_FAMILY_MISMATCH')
    pcm_file = Path(pcm_file).resolve(strict=True)
    original = await await_blocking(file_identity,pcm_file)
    pcm = await await_blocking(pcm_info,pcm_file)
    if original != primary['normalized_input'] or pcm != primary['pcm']:
        raise BusinessError('角色分析需要同一完整原始PCM',409,'SOURCE_SPEAKER_INPUT_CHANGED')
    started = time.monotonic()
    async with AudioModelSession(config,pool,folder) as session:
        async with async_client(timeout=httpx.Timeout(timeout,connect=5),trust_env=False) as client:
            if not await await_blocking(session.server.owns_listener):
                raise BusinessError('角色模型端口不属于本次进程',409,'MODEL_LISTENER_MISMATCH')
            async with client.stream('POST',session.server.endpoint+'/v1/tasks/run',json={
                'model':config.model_id,'busy_timeout_ms':1000,'request':{'audio':str(pcm_file),'options':OPTIONS}}) as reply:
                raw = bytearray()
                async for block in reply.aiter_bytes():
                    raw.extend(block)
                    if len(raw)>4*1024**2:raise BusinessError('角色响应超过4MiB',502,'SOURCE_SPEAKER_RESPONSE_INVALID')
                if reply.status_code!=200:
                    (pool.directory/'model-logs'/(pool.history_name+'.upstream-error')).write_bytes(raw[:16384])
                    raise BusinessError('角色模型未完成计算',502,'SOURCE_SPEAKER_UPSTREAM_FAILED')
            try:
                data = json.loads(raw)
                if not isinstance(data,dict) or not set(data) <= {'speaker_turns','timing'} or not isinstance(data.get('timing'),dict):raise ValueError()
                turns = data.get('speaker_turns',[])
                summarize(turns,pcm)
            except (ValueError,TypeError,BusinessError):
                (pool.directory/'model-logs'/(pool.history_name+'.invalid-response')).write_bytes(raw[:16384])
                raise BusinessError('角色模型响应格式无效',502,'SOURCE_SPEAKER_RESPONSE_INVALID') from None
            session.requests = 1
    if original != await await_blocking(file_identity,pcm_file):
        raise BusinessError('角色分析期间PCM变化',409,'SOURCE_SPEAKER_INPUT_CHANGED')
    result = {'rule_revision':RULE,'input':original,'pcm':pcm,'submitted_samples':pcm['sample_count'],
        'model':{'family':config.family,'model_id':config.model_id,'weights_sha256':config.weights_sha256,
                 'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision},
        'request_options':OPTIONS,'session_options':SESSION_OPTIONS,'text_prompt':'','turns':turns,
        'reported_timing':data['timing'],'response_sha256':sha256(raw).hexdigest(),
        'session':session.receipt(),'elapsed_seconds':time.monotonic()-started,
        'speaker_identity':'not_measured','turn_boundary_review':'not_measured','subtitle_timing_review':'not_measured'}
    result['summary'] = verify(result,primary)
    return result
