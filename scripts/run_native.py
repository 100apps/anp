"""通过 WSL 运行工作区中的平台源码，标准输入输出保留给开发日志。"""
import argparse
import json
from pathlib import Path
import subprocess
from anp import ROOT


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debug-port',type=int);parser.add_argument('--wait-for-client',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--reload',action='store_true')
    args=parser.parse_args()
    configuration=json.loads((ROOT/'.local/generated/platform-v3/native-launch.json').read_text(encoding='utf8'))
    command=['wsl','-d',configuration['distro'],'-u','root','--exec','env',
        *(key+'='+value for key,value in configuration['environment'].items()),configuration['python'],'-m','anp_platform.native']
    if args.debug_port:command+=['--debug-port',str(args.debug_port)]
    if args.wait_for_client:command+=['--wait-for-client']
    if args.check:command+=['--check']
    if args.reload:command+=['--reload']
    raise SystemExit(subprocess.call(command))
