"""事务外投递 Langfuse；失败留在 outbox，未知质量不转换成通过分数。"""
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe
from uuid import NAMESPACE_URL, UUID, uuid5
import json

import httpx
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue, InstrumentationScope
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import Span, ResourceSpans, ScopeSpans

from anp.config import secret, value
from anp.sdk.http import async_client


def ingestion_batch(item):
    """身份和输入只来自经 RLS/RPC 授权的冻结记录。"""
    run, stage, attempt, event = (item[k] for k in ('run', 'stage', 'attempt', 'event'))
    trace = UUID(run['id']).hex
    span = sha256(attempt['id'].encode()).hexdigest()[:16]
    timestamp = datetime.now(timezone.utc).isoformat()
    metadata = {'app_id': 'deep_dub', 'workspace_id': run['workspace_id'],
        'project_id': run['project_id'], 'run_id': run['id'], 'purpose': run['purpose'],
        'flow_revision': run['flow_revision']}

    def envelope(kind, body):
        return {'id': str(uuid5(NAMESPACE_URL, 'anp:deep_dub:'+event['id']+':'+kind)),
                'timestamp': timestamp, 'type': kind, 'body': body}

    batch = [envelope('span-create', {'id': span, 'traceId': trace,
        'name': 'deep_dub/'+stage['node'],
        'startTime': attempt['started_at'] or attempt['queued_at'],
        'endTime': attempt['finished_at'],
        'level': 'ERROR' if attempt['status'] == 'failed' else 'DEFAULT',
        'metadata': {**metadata, 'stage_id': stage['id'], 'attempt_id': attempt['id'],
                     'status': attempt['status'], 'partition': stage['partition_key']},
        'input': {'sha256': stage['input_hash']}, 'output': {'artifact_ids': attempt['outputs']}})]
    score = item.get('score')
    if score is not None:
        measured = score['measurement_status'] == 'measured'
        batch.append(envelope('score-create', {'id': score['id'], 'traceId': trace,
            'environment':run['purpose'],
            'observationId': span, 'name': score['metric'] if measured else score['metric']+':status',
            'dataType': 'NUMERIC' if measured else 'TEXT',
            'value': score['value'] if measured else 'not_measured',
            'metadata': {'rule_revision': score['rule_revision'], 'subject_hash': score['subject_hash'],
                         'evidence_artifact_id': score['evidence_artifact_id'],
                         'measurement_status': score['measurement_status']}}))
    return batch


def otlp_payload(item, body):
    def nanos(stamp):
        # 避免浮点 timestamp 丢失微秒精度。
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days*86400+delta.seconds)*1_000_000_000+delta.microseconds*1000

    attributes = {'user.id':item['user_id'], 'session.id':item['session_id'],
        'langfuse.environment':item['run']['purpose'],
        'langfuse.trace.name':'deep_dub/验收运行' if item['run']['purpose']=='acceptance' else 'deep_dub/整片运行',
        'langfuse.observation.type':'span',
        'langfuse.observation.level':body['level'],
        'langfuse.observation.input':json.dumps(body['input']),
        'langfuse.observation.output':json.dumps(body['output']),
        **{'langfuse.observation.metadata.'+k:str(v) for k,v in body['metadata'].items()}}
    span = Span(trace_id=bytes.fromhex(body['traceId']), span_id=bytes.fromhex(body['id']),
        name=body['name'], kind=Span.SPAN_KIND_INTERNAL, flags=1,
        start_time_unix_nano=nanos(body['startTime']), end_time_unix_nano=nanos(body['endTime']),
        attributes=[KeyValue(key=k,value=AnyValue(string_value=v)) for k,v in attributes.items()])
    return ExportTraceServiceRequest(resource_spans=[ResourceSpans(
        resource=Resource(attributes=[KeyValue(key='service.name',value=AnyValue(string_value='anp-deep-dub'))]),
        scope_spans=[ScopeSpans(scope=InstrumentationScope(name='anp.deep_dub',version='1'),spans=[span])])]).SerializeToString()


async def deliver_one(actor):
    """每次只领取一项；网络调用不持有数据库事务，回执受 token/TTL 保护。"""
    await actor.require('deep_dub.task.run')
    token = token_urlsafe(48)
    item = await actor.objects.rpc('deep_dub_claim_observation', p_workspace=actor.workspace_id,
                                    p_token=token, p_seconds=60)
    if not item['claimed']:
        return {'claimed': False}
    code, success = None, False
    try:
        batch = ingestion_batch(item)
        async with async_client(timeout=20, trust_env=False) as client:
            auth = httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'), secret('LANGFUSE_SECRET_KEY'))
            response = await client.post(value('langfuse', 'url')+'/api/public/otel/v1/traces',
                auth=auth, content=otlp_payload(item,batch[0]['body']),
                headers={'Content-Type':'application/x-protobuf',
                         'x-langfuse-sdk-name':'anp-deep-dub', 'x-langfuse-sdk-version':'1',
                         'x-langfuse-ingestion-version':'4'})
            if response.is_success:
                if 'json' in response.headers.get('content-type',''):
                    result = response.json()
                    success = (not result.get('error') and
                               result.get('partialSuccess',{}).get('rejectedSpans',0) in (0,'0'))
                else:
                    exported = ExportTraceServiceResponse.FromString(response.content)
                    success = exported.partial_success.rejected_spans == 0
                if not success:
                    code = 'OTLP_PARTIALLY_REJECTED'
            else:
                code = 'HTTP_'+str(response.status_code)
            scores = batch[1:]
            if success and scores:
                response = await client.post(value('langfuse', 'url')+'/api/public/ingestion',
                    auth=auth, json={'batch': scores})
                if response.is_success:
                    data = response.json()
                    success = not data.get('errors') and {x['id'] for x in data.get('successes', [])} == {x['id'] for x in scores}
                    if not success:
                        code = 'SCORE_INGESTION_REJECTED'
                else:
                    success, code = False, 'HTTP_'+str(response.status_code)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, DecodeError):
        # 原始响应/异常可能带身份或产物内容，不持久化也不输出。
        code = 'OBSERVATION_DELIVERY_FAILED'
    result = await actor.objects.rpc('deep_dub_ack_observation', p_workspace=actor.workspace_id,
        p_event=item['event']['id'], p_token=token, p_success=success, p_error_code=code)
    return {'claimed': True, 'event_id': item['event']['id'], **result}
