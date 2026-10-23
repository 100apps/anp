"""流式导出本项目工具镜像为zstd包；不包含容器数据，不生成未压缩中间文件。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import zstandard
from manage import ROOT, read_env


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image')
    parser.add_argument('--level',type=int,default=10)
    parser.add_argument('--platform',choices=['linux/amd64','linux/arm64'])
    args=parser.parse_args()
    args.image=args.image or os.getenv('ANP_IMAGE') or read_env(ROOT/'.env').get('ANP_IMAGE','ghcr.io/100apps/anp:latest')
    details=json.loads(subprocess.check_output(['docker','image','inspect',args.image],text=True))[0]
    target_platform=args.platform or details['Os']+'/'+details['Architecture']
    identity=details['Id'].split(':')[-1]
    output=(ROOT/'var/exports'/f'anp-toolbox-{identity[:12]}-{target_platform.replace("/","-")}.tar.zst').resolve()
    assert output.is_relative_to((ROOT/'var').resolve())
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix(output.suffix+'.part')
    process=subprocess.Popen(['docker','image','save','--platform',target_platform,args.image],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with temporary.open('wb') as file:
            zstandard.ZstdCompressor(level=args.level,threads=4).copy_stream(process.stdout,file)
        error=process.stderr.read().decode(errors='replace')
        assert process.wait()==0, '镜像导出失败：'+error
        current=json.loads(subprocess.check_output(['docker','image','inspect',args.image],text=True))[0]
        assert current['Id']==details['Id'], '导出期间镜像标签变化，拒绝生成混合回执'
        temporary.replace(output)
    except BaseException:
        if process.poll() is None: process.kill(); process.wait()
        temporary.unlink(missing_ok=True)
        raise
    with output.open('rb') as file: digest=hashlib.file_digest(file,'sha256').hexdigest()
    record={'time':datetime.now(timezone.utc).isoformat(),'image':args.image,'image_id':details['Id'],'platform':target_platform,
            'archive':str(output),'bytes':output.stat().st_size,'sha256':digest,
            'note':'这是分发包大小；导入后展开占用更大。包含镜像，不包含/data持久化数据。'}
    output.with_suffix(output.suffix+'.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__': main()
