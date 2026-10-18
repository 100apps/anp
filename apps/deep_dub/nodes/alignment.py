"""词时间戳和PCM能量代理；不确认给定文本真的被说出，不代替独立ASR。"""
from array import array
from math import isfinite,sqrt
import sys
import wave

from anp.errors import BusinessError
from apps.deep_dub.nodes.text_compare import normalize

RULE='word-and-rms-timing-proxy-v1'


def summarize_words(words,sample_count,text):
    if not isinstance(words,list) or isinstance(sample_count,bool) or not isinstance(sample_count,int) or not 0<sample_count<=16000*120 or len(words)>4096:
        raise BusinessError('词对齐输出不是有界列表',502,'ALIGN_RESPONSE_INVALID')
    rows=[];zero=0;outside=0;out_of_order=0
    for row in words:
        if not isinstance(row,dict) or not isinstance(row.get('word'),str) or not 0<len(row['word'])<=2000:
            raise BusinessError('词对齐缺少合法文本',502,'ALIGN_RESPONSE_INVALID')
        start,end=row.get('start_sample'),row.get('end_sample')
        if any(isinstance(n,bool) or not isinstance(n,int) or abs(n)>2**31 for n in (start,end)):
            raise BusinessError('词边界不是有界整数样本位置',502,'ALIGN_RESPONSE_INVALID')
        confidence=row.get('confidence')
        if confidence is not None and (isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not isfinite(confidence)):
            raise BusinessError('模型报告的词置信度不是有限数',502,'ALIGN_RESPONSE_INVALID')
        outside+=not 0<=start<=end<=sample_count
        if rows:out_of_order+=start<rows[-1]['start_sample']
        if end<=start and normalize(row['word']):zero+=1
        rows.append({'word':row['word'],'start_sample':start,'end_sample':end,'reported_confidence':confidence})
    overlap=0;covered_end=0
    for start,end in sorted((r['start_sample'],r['end_sample']) for r in rows if 0<=r['start_sample']<=r['end_sample']<=sample_count):
        overlap+=max(0,min(end,covered_end)-start);covered_end=max(covered_end,end)
    covered=bool(rows) and normalize(''.join(r['word'] for r in rows))==normalize(text)
    valid=bool(rows) and not outside and not zero and not overlap and not out_of_order
    return {'rule_revision':RULE,'sample_rate':16000,'sample_count':sample_count,'word_count':len(rows),'words':rows,
        'timestamp_bounds_valid':not outside and bool(rows),'outside_or_reversed_words':outside,
        'zero_length_content_words':zero,'out_of_order_words':out_of_order,'internal_overlap_samples':overlap,'transcript_token_coverage':covered,
        'valid_timestamps':valid,'token_onset_ms':min(r['start_sample'] for r in rows)/16 if rows else None,
        'token_offset_ms':max(r['end_sample'] for r in rows)/16 if rows else None,
        'scope':'强制给定文本的模型词时间戳；置信度未校准，不作为内容/事实或听感通过'}


def signal_profile(path):
    frames=[];total=0;clipped=0
    with wave.open(str(path),'rb') as source:
        if source.getframerate()!=16000 or source.getnchannels()!=1 or source.getsampwidth()!=2:
            raise BusinessError('能量分析需16kHz单声道PCM16',422,'SIGNAL_INPUT_INVALID')
        expected=source.getnframes()
        while block:=source.readframes(160):
            samples=array('h');samples.frombytes(block)
            if sys.byteorder!='little':samples.byteswap()
            frames.append((total,len(samples),sqrt(sum(n*n for n in samples)/len(samples))/32768))
            total+=len(samples);clipped+=sum(abs(n)>=32760 for n in samples)
            if total>16000*120:raise BusinessError('能量分析超过120秒',422,'SIGNAL_INPUT_INVALID')
    if not total or total!=expected:raise BusinessError('能量分析输入为空或截断',422,'SIGNAL_INPUT_INVALID')
    peak=max(r[2] for r in frames);threshold=max(10**(-50/20),peak*10**(-35/20))
    active=[r for r in frames if r[2]>=threshold]
    return {'rule_revision':RULE,'sample_rate':16000,'sample_count':total,'frame_samples':160,
        'threshold_full_scale':threshold,'peak_frame_rms':peak,'active_frame_count':len(active),
        'active_samples':sum(r[1] for r in active),'onset_ms':active[0][0]/16 if active else None,
        'offset_ms':(active[-1][0]+active[-1][1])/16 if active else None,'clipped_sample_ratio':clipped/total,
        'scope':'10ms帧RMS能量代理，阈值=max(-50dBFS,峰值帧RMS-35dB)；不是语音活动、身份或听感审核'}


def placement_metrics(words,signal,*,start_ms,source_start_ms,source_end_ms,video_duration_ms,max_advance_ms):
    duration=signal['sample_count']/16
    return {'rule_revision':RULE,'playback_start_ms':start_ms,'placement_kind':'analysis_only',
        'source_window_ms':[source_start_ms,source_end_ms],'audio_duration_ms':duration,
        'source_window_duration_ratio':duration/(source_end_ms-source_start_ms),
        'token_onset_delta_ms':None if words['token_onset_ms'] is None else start_ms+words['token_onset_ms']-source_start_ms,
        'token_offset_delta_ms':None if words['token_offset_ms'] is None else start_ms+words['token_offset_ms']-source_end_ms,
        'energy_onset_delta_ms':None if signal['onset_ms'] is None else start_ms+signal['onset_ms']-source_start_ms,
        'energy_offset_delta_ms':None if signal['offset_ms'] is None else start_ms+signal['offset_ms']-source_end_ms,
        'maximum_advance_ms':max_advance_ms,'start_within_advance_bound':start_ms>=max(0,source_start_ms-max_advance_ms),
        'video_bounds_ok':0<=start_ms and start_ms+duration<=video_duration_ms,
        'scope':'仅分析指定落点，不保存落点/选版/时间线；跨段碰撞和时序可接受性尚未审核'}
