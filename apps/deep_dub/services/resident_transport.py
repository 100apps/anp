"""已有信任 SSH 上传不可变业务节点、参考和作业；不操作模型生命周期。"""
import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import shlex
import subprocess
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator

from anp.config import value
from anp.errors import BusinessError
from apps.deep_dub.nodes import resident_job
from apps.deep_dub.services.blocking import await_blocking


class ResidentConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    transport:Literal['resident_ssh']='resident_ssh'
    host:str=Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
    user:str=Field(pattern=r'^[a-z_][a-z0-9_-]*$')
    ssh_config:str='NUL'
    port:int=Field(ge=1024,le=65535)
    model_id:str=Field(min_length=1,max_length=100)
    family:Literal['index_tts2']='index_tts2'
    gpu_uuid:str=Field(pattern=r'^GPU-[a-zA-Z0-9-]+$')
    service_identity:dict
    root:Literal['/opt/anp/workspaces/e-anp/deep_dub/resident']='/opt/anp/workspaces/e-anp/deep_dub/resident'

    @model_validator(mode='after')
    def identity(self):
        from ipaddress import IPv4Address
        host=IPv4Address(self.host)
        if not host.is_private or host.is_unspecified or host.is_multicast:raise ValueError('只允许明确的已有内网服务')
        i=self.service_identity
        if set(i)!={'pid','start_ticks','boot_id','executable_sha256','weights_sha256'}:raise ValueError('缺少冻结服务身份')
        import re
        if (type(i['pid']) is not int or i['pid']<=1 or type(i['start_ticks']) is not int or i['start_ticks']<=0
            or not isinstance(i['boot_id'],str) or len(i['boot_id'])>200
            or any(not re.fullmatch('[0-9a-f]{64}',i[k]) for k in ('executable_sha256','weights_sha256'))):raise ValueError('服务身份格式不符')
        return self

    @property
    def weights_sha256(self):return self.service_identity['weights_sha256']
    @property
    def executable_sha256(self):return self.service_identity['executable_sha256']


def configuration():
    file=value('deep_dub','resident_tts_config','')
    if not file:raise BusinessError('尚未配置可信远端常驻配音服务',503,'RESIDENT_UNCONFIGURED')
    return ResidentConfig.model_validate_json(Path(file).read_bytes())


CONTROL=r'''
import base64,fcntl,hashlib,json,os,subprocess,sys
from pathlib import Path
from uuid import UUID
p=json.load(sys.stdin);os.umask(0o077)
ns={'__name__':'anp_resident_control'};exec(p['source'],ns)
require=ns['require'];save=ns['save'];root=Path(p['root'])
require(str(root)=='/opt/anp/workspaces/e-anp/deep_dub/resident','远端业务根目录不符')
for part in (root,*root.parents):require(not part.is_symlink(),'业务目录不能使用符号链接')
def valid_root():
 if not root.exists():return False
 require((root/'.workspace-marker').read_text().strip()==ns['MARKER'],'既有目录不属于当前业务')
 require(root.stat().st_mode&0o077==0,'业务目录权限过宽')
 return True
def alive(launch):
 try:
  line=Path('/proc',str(launch['pid']),'stat').read_text();tail=line[line.rfind(')')+2:].split()
  return int(tail[19])==launch['start_ticks'] and tail[0]!='Z'
 except FileNotFoundError:return False
def state(directory):
 launch=json.loads((directory/'launch.json').read_bytes()) if (directory/'launch.json').exists() else None
 data=json.loads((directory/'state.json').read_bytes()) if (directory/'state.json').exists() else {'status':'launch_unknown','operation_stopped':False}
 data={**data,'worker_running':alive(launch) if launch else False}
 if launch and 'worker_pid' in data:require(data['worker_pid']==launch['pid'],'回执进程身份不符')
 return data
if p['mode']=='inspect':
 identity=ns['describe'](p['service']);pending=[]
 if valid_root() and (root/'jobs').exists():
  directories=[d for d in (root/'jobs').iterdir() if d.is_dir()]
  require(len(directories)<=5000,'远端历史作业需归档后再分配')
  for directory in directories:
   require(not directory.is_symlink() and str(UUID(directory.name))==directory.name,'未知业务作业目录')
   request=json.loads((directory/'request.json').read_bytes())
   if request['port']!=p['service']['port']:continue
   s=state(directory)
   if not s.get('operation_stopped') or s['worker_running']:pending.append(directory.name)
 print(json.dumps({'service_identity':identity,'pending_job_ids':pending,'all_owned_operations_stopped':not pending}))
else:
 require(p['mode'] in ('launch','read','output'),'未知业务操作')
 require(str(UUID(p['job_id']))==p['job_id'],'作业ID无效')
 if p['mode']=='launch':
  if not valid_root():
   root.mkdir(parents=True,exist_ok=False);(root/'.workspace-marker').write_text(ns['MARKER'])
  jobs=root/'jobs';jobs.mkdir(exist_ok=True)
  marker=jobs/'.workspace-marker'
  if not marker.exists():marker.write_text(ns['MARKER'])
  require(marker.read_text().strip()==ns['MARKER'],'作业根标记不符')
  with ns['lock'](root/'controller.lock'):
   release=root/'releases'/p['source_sha256'];release.mkdir(parents=True,exist_ok=True)
   script=release/'resident_job.py';raw=p['source'].encode()
   require(hashlib.sha256(raw).hexdigest()==p['source_sha256'],'节点制品摘要不符')
   if script.exists():require(script.read_bytes()==raw,'不可变节点制品已变化')
   else:
    with script.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
   directory=jobs/p['job_id'];directory.mkdir(exist_ok=True)
   request=p['request'];expected=ns['canonical'](request)
   if (directory/'request.json').exists():require((directory/'request.json').read_bytes()==expected,'作业已绑定另一份输入')
   else:
    require(set(p['files'])=={'voice.wav','emotion.wav'},'仅允许两个本作业参考')
    for name,encoded in p['files'].items():
     data=base64.b64decode(encoded,validate=True);require(44<len(data)<=8*1024**2,'参考音频超限')
     require(hashlib.sha256(data).hexdigest()==request['input_files'][name],'参考摘要不符')
     with (directory/name).open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    save(directory/'request.json',request)
   ns['checked_input'](directory,request)
   if not (directory/'launch-intent.json').exists():
    save(directory/'launch-intent.json',{'job_id':p['job_id'],'input_sha256':hashlib.sha256(expected).hexdigest()})
    with (directory/'worker.log').open('xb') as log:
     child=subprocess.Popen([sys.executable,str(script),str(directory)],cwd=directory,stdin=subprocess.DEVNULL,
       stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    line=Path('/proc',str(child.pid),'stat').read_text();tail=line[line.rfind(')')+2:].split()
    save(directory/'launch.json',{'pid':child.pid,'start_ticks':int(tail[19]),'source_sha256':p['source_sha256']})
 else:require(valid_root(),'远端业务目录不存在')
 directory=root/'jobs'/p['job_id'];require(not directory.is_symlink(),'作业路径不能是链接')
 data=state(directory)
 if p['mode']=='output':
  require(data['status']=='succeeded' and data['operation_stopped'] and not data['worker_running'],'远端作业未完成')
  file=directory/'result.wav';require(not file.is_symlink() and file.stat().st_size==data['output_bytes'],'产物不完整')
  content=file.read_bytes();require(hashlib.sha256(content).hexdigest()==data['output_sha256'],'产物摘要不符')
  data['audio']=base64.b64encode(content).decode()
 print(json.dumps(data))
'''


class Transport:
    def __init__(self,config):
        self.config=config
        self.source=Path(resident_job.__file__).read_bytes().decode('utf-8')
        self.source_sha256=sha256(self.source.encode()).hexdigest()

    def call(self,mode,**parameters):
        c=self.config
        payload={'mode':mode,'root':c.root,'source':self.source,'source_sha256':self.source_sha256,
            'service':{'service_pid':c.service_identity['pid'],'service_host':c.host,'port':c.port,'model_id':c.model_id,
                'service_identity':c.service_identity},**parameters}
        command=['ssh','-F',c.ssh_config,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=6',
            c.user+'@'+c.host,'python3','-c',shlex.quote(CONTROL)]
        try:response=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=120)
        except (OSError,subprocess.TimeoutExpired):
            raise BusinessError('远端观察暂未确认；仅可继续查询原作业',503,'RESIDENT_TRANSPORT_FAILED') from None
        if response.returncode:raise BusinessError('远端业务操作未确认，未自动重试或停止服务',503,'RESIDENT_TRANSPORT_FAILED')
        if len(response.stdout)>48*1024**2:raise BusinessError('远端业务响应超限',502,'RESIDENT_RESPONSE_INVALID')
        return json.loads(response.stdout)

    async def request(self,mode,**parameters):
        # SSH过程不承载Supabase授权，当前Actor仍由调用服务逐次核验。
        return await await_blocking(lambda:self.call(mode,**parameters))
