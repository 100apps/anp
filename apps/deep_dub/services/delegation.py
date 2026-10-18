"""单尝试委派；原用户JWT只在业务主机私有目录，外部仅拿到有时限的作业能力令牌。"""
from hashlib import sha256
import hmac
import json
import os
import asyncio
import tempfile
import subprocess
import sys
from functools import lru_cache
from threading import RLock
from pathlib import Path
import re
from secrets import token_bytes
import time
from uuid import uuid4

import jwt

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.errors import BusinessError
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.processing import describe_decode,decode_artifact
from apps.deep_dub.services.versions import identifier,operation_key


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


_directory_lock=RLock()


@lru_cache(maxsize=64)
def _protect_directory(path):
    """Windows chmod不控制读取ACL；凭据写入前禁用共享磁盘的宽泛继承。"""
    directory=Path(path)
    if sys.platform=='win32':
        script="""
$ErrorActionPreference='Stop'
$acl=New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$prop=[System.Security.AccessControl.PropagationFlags]::None
$allow=[System.Security.AccessControl.AccessControlType]::Allow
$current=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system=New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
foreach($sid in @($current,$system)) {
  $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl',$inherit,$prop,$allow)
  $acl.AddAccessRule($rule)
}
[System.IO.Directory]::SetAccessControl($env:ANP_DELEGATION_PRIVATE_DIRECTORY,$acl)
"""
        environment=os.environ.copy();environment['ANP_DELEGATION_PRIVATE_DIRECTORY']=str(directory)
        try:
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],
                env=environment,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise OSError('ACL_FAILED')
        except (OSError,subprocess.TimeoutExpired):
            raise BusinessError('无法保护本机作业会话目录，拒绝保存或使用凭据',503,'DELEGATION_DIRECTORY_UNSAFE') from None
    else:
        directory.chmod(0o700)


class JobVault:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory is not None else ROOT/'.local/deep_dub/delegations'

    def key(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        # 线程并发首次使用也必须先完成ACL，再创建任何凭据文件。
        with _directory_lock:_protect_directory(str(self.directory.resolve()))
        path=self.directory/'signing.key'
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=self.directory,delete=False,prefix='.signing-') as output:
                temporary=Path(output.name);os.chmod(temporary,0o600)
                output.write(token_bytes(32));output.flush();os.fsync(output.fileno())
            try:
                try:os.link(temporary,path)
                except FileExistsError:pass
            finally:temporary.unlink(missing_ok=True)
        key=path.read_bytes()
        if len(key)!=32:raise BusinessError('作业签名配置不可用',503,'DELEGATION_KEY_INVALID')
        return key

    def save(self,record):
        key=self.key();grant_id=uuid4().hex
        path=self.directory/(grant_id+'.json')
        encoded=canonical(record)
        with path.open('xb') as output:
            os.chmod(path,0o600);output.write(encoded);output.flush();os.fsync(output.fileno())
        signature=hmac.new(key,grant_id.encode()+b'.'+encoded,sha256).hexdigest()
        return grant_id+'.'+signature

    def load(self,token,run_id,attempt_id):
        if not isinstance(token,str) or not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}',token):
            raise BusinessError('作业访问凭据不正确',401,'INVALID_JOB_GRANT')
        grant_id,signature=token.split('.')
        key=self.key()
        try:
            encoded=(self.directory/(grant_id+'.json')).read_bytes()
            record=json.loads(encoded)
        except (OSError,ValueError):
            raise BusinessError('作业访问凭据不正确',401,'INVALID_JOB_GRANT') from None
        expected=hmac.new(key,grant_id.encode()+b'.'+encoded,sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise BusinessError('作业访问凭据不正确',401,'INVALID_JOB_GRANT')
        if (record.get('scope')!='deep_dub.full_decode' or record.get('version')!=1
            or record.get('run_id')!=identifier(run_id) or record.get('attempt_id')!=identifier(attempt_id)):
            raise BusinessError('作业授权不属于本次执行',403,'JOB_SCOPE_MISMATCH')
        if record.get('expires_at',0)<=time.time():
            raise BusinessError('作业授权已过期，请重新授权',401,'JOB_GRANT_EXPIRED')
        return record


vault=JobVault()


async def issue_decode_job(actor,run_id,payload,request_key=None,ttl_seconds=300):
    if not isinstance(ttl_seconds,int) or isinstance(ttl_seconds,bool) or not 1<=ttl_seconds<=600:
        raise BusinessError('作业授权有效期须为1–600秒',422,'INVALID_GRANT_TTL')
    # 签发前核验实际会话；JWT的exp仅用于收紧有效期，不将未验签解码当作身份校验。
    actor=await actor_from_token(actor.token,actor.workspace_id)
    body,item,fingerprint=await describe_decode(actor,run_id,payload)
    token_exp=jwt.decode(actor.token,options={'verify_signature':False})['exp']
    expires=min(int(token_exp),int(time.time())+ttl_seconds)
    if expires<=time.time():raise BusinessError('登录即将失效，请重新登录',401,'UNAUTHENTICATED')
    key=operation_key(request_key)
    selected=await execution.begin_stage(actor,run_id,{'node':'full_decode','partition':item['id'],
        'input_hash':fingerprint,'device_kind':'cpu'},key)
    attempt=selected['attempt']
    if attempt['created_by']!=actor.user['id']:
        raise BusinessError('不能替其他执行者签发作业权限',403,'JOB_OWNER_MISMATCH')
    if attempt['status'] not in ('queued','succeeded'):
        raise BusinessError('本次尝试不可签发新的执行授权',409,'JOB_NOT_ISSUABLE')
    record={'version':1,'scope':'deep_dub.full_decode','workspace_id':actor.workspace_id,
        'user_id':actor.user['id'],'actor_token':actor.token,'run_id':identifier(run_id),
        'attempt_id':attempt['id'],'stage_id':attempt['stage_id'],'input_hash':fingerprint,
        'parameters':body.model_dump(mode='json'),'request_key':key,'expires_at':expires}
    return {'run_id':identifier(run_id),'attempt_id':attempt['id'],'expires_at':expires,'job_token':vault.save(record)}


async def dispatch_decode_job(token,run_id,attempt_id):
    record=vault.load(token,run_id,attempt_id)
    actor=await actor_from_token(record['actor_token'],record['workspace_id'])
    if actor.user['id']!=record['user_id']:
        raise BusinessError('作业身份不匹配',403,'JOB_OWNER_MISMATCH')
    # 每次重放重新检查权限、取消及当前尝试，文件内容不是授权判断缓存。
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    runs=await actor.objects.select('deep_dub_runs',id='eq.'+record['run_id'],workspace_id='eq.'+actor.workspace_id)
    stages=await actor.objects.select('deep_dub_stages',id='eq.'+record['stage_id'],workspace_id='eq.'+actor.workspace_id)
    if not runs or runs[0]['status']!='running' or not stages or stages[0]['current_attempt_id']!=record['attempt_id'] or stages[0]['input_hash']!=record['input_hash']:
        raise BusinessError('作业已停止、取消或被新尝试替代',409,'STALE_JOB_GRANT')
    try:
        result=await asyncio.wait_for(decode_artifact(actor,record['run_id'],record['parameters'],record['request_key']),
                                      max(.001,record['expires_at']-time.time()))
    except asyncio.TimeoutError:
        raise BusinessError('作业授权已到期，执行已停止',401,'JOB_GRANT_EXPIRED') from None
    if result['attempt']['id']!=record['attempt_id']:
        raise BusinessError('作业回执不属于授权尝试',409,'JOB_SCOPE_MISMATCH')
    return result
