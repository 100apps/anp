"""把持久化适配合入已验收完整平台；固定基底和源码摘要，不修改运行容器。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'auth','storage','postgrest','meta','studio','dbx','langfuse-web','langfuse-worker','lens','litellm','agent'}


def build(base,tag):
    inspected=json.loads(subprocess.check_output(['docker','image','inspect',base],text=True))[0]
    image=inspected['Id']
    metadata=json.loads(subprocess.check_output(['docker','run','--rm','--entrypoint','/bin/cat',image,'/opt/anp/components.json'],text=True))
    if metadata.get('partial') or set(metadata['components'])!=EXPECTED:
        raise RuntimeError('持久化补丁必须以包含 Agent 的完整十一组件制品为基底')
    source={name:(ROOT/'platform/runtime/anp_platform'/name).read_bytes() for name in ('agent.py','agent_persistence.py')}
    digest=sha256(b''.join(name.encode()+b'\0'+data for name,data in sorted(source.items()))).hexdigest()
    fixed='anp/platform-base:'+image.removeprefix('sha256:')
    subprocess.run(['docker','tag',image,fixed],check=True)
    with TemporaryDirectory(prefix='anp-agent-persistence-') as directory:
        for name,data in source.items():(Path(directory)/name).write_bytes(data)
        subprocess.run(['docker','build','--progress','plain','--build-arg','ANP_PLATFORM_BASE='+fixed,
            '--build-arg','ANP_PATCH_SHA256='+digest,'-f',str(ROOT/'deploy/build/agent-persistence.Dockerfile'),'-t',tag,directory],check=True)
    result=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
    assert result['Config']['Labels']['dev.anp.agent_persistence_patch']==digest
    return {'tag':tag,'id':result['Id'],'base_id':image,'patch_sha256':digest,
        'files':{name:sha256(data).hexdigest() for name,data in source.items()},
        'upstreams':{name:item['revision'] for name,item in metadata['components'].items()},
        'upstreams_updated':False,'published_to_ghcr':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',required=True);parser.add_argument('--tag',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();receipt=build(args.base,args.tag)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({'tag':receipt['tag'],'id':receipt['id'],'patch_sha256':receipt['patch_sha256']}))
