"""远端常驻模型的单次业务作业。仅由受权业务运行器部署，不是平台服务。

同步接口没有查询/取消协议：一旦保存 submit_intent，就禁止自动重发。
客户端退出不杀常驻模型；未得到完整响应时保留 uncertain，不能释放设备。
"""
from contextlib import contextmanager
from hashlib import sha256
import base64
import io
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.request import Request,build_opener,ProxyHandler
from uuid import UUID
import wave

RULE='resident-index-tts-durable-job-v1'
MARKER='anp-e-anp-deep-dub-resident-jobs-v1'
MAX_RESPONSE=48*1024**2


def require(condition,message):
    if not condition:raise ValueError(message)


def digest(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def save(path,value):
    target=path.with_suffix('.pending')
    with target.open('wb') as output:
        output.write(canonical(value));output.flush();os.fsync(output.fileno())
    os.replace(target,path)
    if sys.platform=='linux':
        fd=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


@contextmanager
def lock(path):
    with path.open('a+b') as file:
        file.seek(0,2)
        if not file.tell():file.write(b'0');file.flush()
        file.seek(0)
        if sys.platform=='win32':
            import msvcrt
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def model_catalog(host,port):
    opener=build_opener(ProxyHandler({}))
    with opener.open(f'http://{host}:{port}/v1/models',timeout=5) as response:
        raw=response.read(65537);require(len(raw)<=65536,'模型目录响应超限')
    return json.loads(raw)['data']


def snapshot(spec):
    """每次核对 Linux 实际 PID/启动时钟、监听 inode、执行器与权重。"""
    require(sys.platform=='linux','远端常驻服务身份检查仅支持Linux')
    pid=spec['service_pid'];port=spec['port'];host=spec['service_host'];proc=Path('/proc')/str(pid)
    stat=(proc/'stat').read_text();tail=stat[stat.rfind(')')+2:].split()
    identity={'pid':pid,'start_ticks':int(tail[19]),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'executable_sha256':digest(proc/'exe')}
    inodes=set()
    for name in ('tcp',):
        for row in Path('/proc/net',name).read_text().splitlines()[1:]:
            columns=row.split()
            address,encoded_port=columns[1].split(':')
            bound=str(IPv4Address(bytes.fromhex(address)[::-1]))
            if columns[3]=='0A' and int(encoded_port,16)==port and (bound==host or (bound=='0.0.0.0' and host=='127.0.0.1')):inodes.add(columns[9])
    owned=set()
    for fd in (proc/'fd').iterdir():
        try:match=re.fullmatch(r'socket:\[(\d+)\]',os.readlink(fd))
        except FileNotFoundError:continue
        if match:owned.add(match[1])
    require(len(inodes)==1 and inodes<=owned,'端口不属于指定的常驻服务进程')
    models=model_catalog(host,port)
    require(len(models)==1 and models[0]['id']==spec['model_id'] and models[0]['family']=='index_tts2'
        and models[0]['task']=='tts' and models[0]['loaded'] is True,'模型不是指定的已加载TTS服务')
    identity['weights_sha256']=digest(Path(models[0]['path']))
    return identity


def describe(spec):
    identity=snapshot(spec)
    require(identity==spec['service_identity'],'常驻服务身份或模型文件变化')
    return identity


def checked_input(directory,spec):
    require(set(spec)=={'rule_revision','job_id','service_host','port','service_pid','model_id','service_identity','request','input_files','timeout_seconds'},'作业清单字段不符')
    address=IPv4Address(spec['service_host'])
    require(str(address)==spec['service_host'] and (address.is_private or address.is_loopback) and not address.is_unspecified and not address.is_multicast,'服务必须为明确的本机IPv4地址')
    require(spec['rule_revision']==RULE and str(UUID(spec['job_id']))==directory.name,'作业身份或协议不符')
    require(type(spec['port']) is int and 1024<=spec['port']<=65535 and type(spec['service_pid']) is int and spec['service_pid']>1,'服务端口或PID不合法')
    request=spec['request']
    require(set(request)=={'text','language','voice_ref','audio','seed','max_tokens','do_sample','num_beams','options'},'请求字段不符')
    require(isinstance(request['text'],str) and 0<len(request['text'].strip())<=2000 and request['language']=='zh','配音文本不合法')
    require(type(request['seed']) is int and 0<=request['seed']<=2**31-1 and request['max_tokens']==1500
        and request['do_sample'] is False and request['num_beams']==3,'生成参数不符合冻结契约')
    options=request['options'];require(set(options)=={'duration_factor','emotion_alpha'},'生成选项不符')
    require(type(options['duration_factor']) in (int,float) and 1/1.35<=options['duration_factor']<=1/.9
        and type(options['emotion_alpha']) in (int,float) and options['emotion_alpha']==1,'倍率或情绪参数不合法')
    require(set(spec['input_files'])=={'voice.wav','emotion.wav'},'输入文件集合不符')
    for field,name in (('voice_ref','voice.wav'),('audio','emotion.wav')):
        file=directory/name
        require(request[field]==str(file) and not file.is_symlink() and file.is_file(),'音频路径超出本作业')
        require(digest(file)==spec['input_files'][name],'输入音频摘要变化')
    require(type(spec['timeout_seconds']) is int and 5<=spec['timeout_seconds']<=900,'请求超时不合法')


def execute(directory,*,identity_probe=describe,opener=None):
    directory=Path(directory).resolve(strict=True)
    if sys.platform=='linux':
        require(directory.is_relative_to(Path('/opt/anp')),'Linux业务作业必须位于带标记的/opt/anp')
        require(directory.parent.stat().st_mode&0o077==0,'业务目录必须仅当前用户可访问')
    marker=directory.parent/'.workspace-marker'
    require(not marker.is_symlink() and marker.read_text().strip()==MARKER,'缺少业务工作区标记')
    require(not (directory/'request.json').is_symlink(),'拒绝链接请求')
    raw=(directory/'request.json').read_bytes();require(0<len(raw)<=32768,'作业请求过大')
    spec=json.loads(raw);checked_input(directory,spec)
    fingerprint=sha256(raw).hexdigest();state_path=directory/'state.json'
    with lock(directory/'execution.lock'):
        if state_path.exists():
            previous=json.loads(state_path.read_bytes())
            require(previous['input_sha256']==fingerprint,'作业ID已绑定另一份输入')
            if previous['status']=='succeeded':
                output=directory/'result.wav'
                require(not output.is_symlink() and output.is_file() and output.stat().st_size==previous['output_bytes']
                    and digest(output)==previous['output_sha256'],'已完成产物摘要不符，禁止重发')
            # 即使上次在发送之前退出，也不能猜测未发送，不能再次POST。
            return previous
        started=time.monotonic();identity=identity_probe(spec);checked_input(directory,spec)
        state={'rule_revision':RULE,'job_id':spec['job_id'],'input_sha256':fingerprint,'status':'submit_intent',
            'worker_sha256':digest(Path(__file__)),
            'worker_pid':os.getpid(),'submitted_at_unix':time.time(),'service_identity':identity,
            'operation_stopped':False,'resident_service_stopped':False,'quality':'not_measured'}
        save(state_path,state)
        try:
            body=canonical({'model':spec['model_id'],'busy_timeout_ms':1000,'request':spec['request']})
            http=opener or build_opener(ProxyHandler({}))
            request=Request(f"http://{spec['service_host']}:{spec['port']}/v1/tasks/run",data=body,headers={'Content-Type':'application/json'})
            with http.open(request,timeout=spec['timeout_seconds']) as response:
                result=response.read(MAX_RESPONSE+1)
                require(response.status==200 and len(result)<=MAX_RESPONSE,'远端响应错误或超限')
            # 完整同步响应证明此请求已返回；后续格式错误不等于模型仍在运行。
            identity_probe(spec);checked_input(directory,spec)
            state['operation_stopped']=True
            reply=json.loads(result);audio=base64.b64decode(reply['audio'],validate=True)
            require(44<len(audio)<=32*1024**2,'WAV为空或超限')
            with wave.open(io.BytesIO(audio),'rb') as wav:
                rate=wav.getframerate();frames=wav.getnframes()
                require(wav.getnchannels()==reply['channels']==1 and wav.getsampwidth()==2
                    and rate==reply['sample_rate'] and rate in (22050,24000) and 0<frames<=rate*120,'WAV参数不符')
                pcm=wav.readframes(frames);require(len(pcm)==frames*2 and any(pcm),'WAV样本不足或静音')
            with (directory/'result.wav').open('xb') as file:
                file.write(audio);file.flush();os.fsync(file.fileno())
            state.update(status='succeeded',output_sha256=sha256(audio).hexdigest(),output_bytes=len(audio),
                sample_rate=rate,sample_count=frames,reported_timing=reply.get('timing'))
        except BaseException as error:
            # 不保存可能带请求正文/路径的上游异常；不根据超时猜测服务已停止。
            state.update(status='failed' if state['operation_stopped'] else 'uncertain',error_type=type(error).__name__)
        state.update(finished_at_unix=time.time(),elapsed_seconds=time.monotonic()-started)
        save(state_path,state)
        return state


if __name__=='__main__':
    require(len(sys.argv)==2,'需要唯一作业目录')
    print(json.dumps(execute(sys.argv[1]),ensure_ascii=False))
