"""无需模型密钥的业务规则评分器；新业务可在 app.toml 注册自己的评分器。"""
import hashlib
import re
from collections import Counter
from anp.sdk.storage import read_json


async def score_node(name,state,output,context):
    if name=='normalize':
        expected=' '.join(state['input_data']['text'].split())
        return {'normalization_valid':bool(expected) and output=={
            'text':expected,'sha256':hashlib.sha256(expected.encode()).hexdigest()}}
    if name=='analyze':
        text=state['normalized']['text']
        terms=re.findall(r'[a-zA-Z0-9_]+|[\u4e00-\u9fff]',text.lower())
        valid=(output.get('character_count')==len(text)
            and output.get('token_count')==len(terms)
            and output.get('sentence_count')==len([s for s in re.split(r'[。！？.!?]+',text) if s.strip()])
            and output.get('top_terms')==[{'term':w,'count':n} for w,n in Counter(terms).most_common(8)]
            and output.get('preview')==text[:160])
        return {'analysis_consistency':valid}
    if name=='save':
        rows=await context.objects.in_schema('app_'+context.task['app_id']).select('results',task_id='eq.'+context.task['id'])
        valid=False
        if rows and output.get('saved') and rows[0]['object_path']==output.get('object_path'):
            saved=await read_json(context.objects.token,output['object_path'])
            valid=saved==rows[0]['report'] and saved['analysis']==state['analysis'] and saved['normalized']==state['normalized']
        return {'artifact_saved':valid}
    return {}
