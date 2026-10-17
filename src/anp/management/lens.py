"""幂等配置本机 Lens Worker 与手动诊断，密钥仅保存到私有 env。"""
import hashlib
import httpx
from dotenv import dotenv_values
from anp import ROOT
from anp.config import secret,value
from anp.management.platform import envfile


def provision():
    path=ROOT/'config/secrets.local.env'
    secrets=dict(dotenv_values(path))
    base=value('litellm','url').removesuffix('/v1')
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+secret('LITELLM_KEY')},trust_env=False,timeout=30) as client:
        def request(method,url,**kwargs):
            response=client.request(method,url,**kwargs)
            response.raise_for_status()
            return response.json()
        if not secrets.get('LENS_ANALYSIS_KEY_ID'):
            key=request('POST','/key/generate',json={'key_alias':'anp-lens-analysis','models':['auto'],
                'max_budget':1,'budget_duration':'30d','rpm_limit':10,'tpm_limit':20000,
                'metadata':{'purpose':'ANP Lens 手动诊断，仅允许 auto'}})
            secrets['LENS_ANALYSIS_KEY_ID']=hashlib.sha256(key['key'].encode()).hexdigest()
            envfile(path,secrets)
        listing=request('GET','/lens')
        worker=next((w for w in listing['workers'] if w['id']==secrets.get('LENS_WORKER_ID') and not w['revoked']),None)
        if not worker or not secrets.get('LENS_WORKER_TOKEN'):
            created=request('POST','/lens/workers/register',json={'name':'ANP Lens Worker',
                'analysis_key_id':secrets['LENS_ANALYSIS_KEY_ID']})
            worker=created['worker']
            secrets['LENS_WORKER_ID']=worker['id']
            secrets['LENS_WORKER_TOKEN']=created['token']
            envfile(path,secrets)
        settings={'name':'ANP 手动诊断','model':'auto','source':'traces',
            'context':'检查 Agent 是否根据用户请求完成任务；结论必须引用实际输入、输出或工具调用证据。数据不足时明确说明，不编造问题。',
            'enabled':False,'sample_size':1,'concurrency':1,'monthly_budget':1,'interval_minutes':1440,
            'service':'litellm-sample-agent'}
        lens=next((x for x in listing['lenses'] if x['settings']['name']==settings['name']),None)
        if lens:
            lens=request('PUT','/lens/'+lens['id'],json=settings)
        else:
            lens=request('POST','/lens',json=settings)
        return {'worker_id':worker['id'],'lens_id':lens['id'],'model':'auto','scheduled':False}


if __name__=='__main__':
    print(provision())
