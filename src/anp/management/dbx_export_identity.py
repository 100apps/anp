"""迁移后显式核对 DBX 内部临时导出目录；不自动接受任意备份盘变化。

先 inspect 保存计划，再 apply 原计划。只允许平台命名卷内固定临时目录，
所有原记录与连接均备份；不改变浏览器、认证、上游文件系统保护或运行代码。
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from anp import ROOT

MOUNT='/opt/anp/roots/dbx/state'
KEY='database_export_destination:/state/dbx/tmp'

# 在容器的真实挂载上检查，不能用宿主机 Windows 的 st_dev 替代。
PROBE=r'''
import hashlib,json,os,sqlite3,sys
from pathlib import Path
p=json.load(sys.stdin);root=Path('/opt/anp/roots/dbx/state');directory=root/'dbx/tmp';db=root/'dbx/dbx.db'
assert root.is_mount(),'DBX 状态目录不是实际挂载点'
assert not any(x.is_symlink() for x in (root,root/'dbx',directory,db)),'拒绝符号链接路径'
assert db.is_file() and directory.is_dir(),'DBX 状态库或临时目录不存在'
assert root.stat().st_dev==directory.stat().st_dev==db.stat().st_dev,'状态库与临时目录不在同一挂载'
key='database_export_destination:/state/dbx/tmp'
digest=lambda x:hashlib.sha256(x).hexdigest()
def other_rows(c):
 h=hashlib.sha256()
 for row in c.execute('SELECT key,value,content_type,version,payload FROM state_store WHERE key<>? ORDER BY key',(key,)):
  h.update(repr(row).encode())
 return h.hexdigest()
c=sqlite3.connect('file:'+str(db)+'?mode=rw',uri=True,timeout=20)
c.execute('PRAGMA busy_timeout=20000')
assert not c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='state_store'").fetchone(),'未知状态存储触发器，拒绝修复'
row=c.execute('SELECT value,content_type,version,payload FROM state_store WHERE key=?',(key,)).fetchone()
assert row is not None,'临时导出目录没有旧身份；不创建或修改无关记录'
old=bytes(row[0]);assert row[1]=='application/octet-stream' and bytes(row[3])==b'','未知身份记录类型'
assert len(old)==15 and old[:7]==b'DBXEI\x01\x01','只支持上游 Linux device v1 身份'
actual=b'DBXEI\x01\x01'+directory.stat().st_dev.to_bytes(8,'little')
result={'key':key,'old_sha256':digest(old),'old_version':row[2],'old_device':int.from_bytes(old[7:],'little'),
 'actual_device':directory.stat().st_dev,'actual_sha256':digest(actual),'needs_reconfirmation':old!=actual}
if p['mode']=='apply':
 expected=p['probe'];assert result==expected,'计划后身份或目录变化，拒绝写入'
 assert old!=actual,'身份已一致，无需改写'
 archive=root/'dbx/identity-backups'/p['backup_id'];archive.mkdir(parents=True,exist_ok=False)
 saved=archive/'dbx-before.sqlite';backup=sqlite3.connect(saved)
 c.backup(backup);assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok';backup.close()
 c.execute('BEGIN IMMEDIATE')
 try:
  current=c.execute('SELECT value,content_type,version,payload FROM state_store WHERE key=?',(key,)).fetchone()
  assert current==row,'并发修改了旧身份；备份保留，拒绝覆盖'
  before=other_rows(c)
  changed=c.execute('UPDATE state_store SET value=?,version=version+1 WHERE key=? AND version=? AND value=?',(actual,key,row[2],old)).rowcount
  assert changed==1 and before==other_rows(c),'目标之外的身份记录变化'
  assert c.execute('SELECT value,version FROM state_store WHERE key=?',(key,)).fetchone()==(actual,row[2]+1)
  c.commit()
 except BaseException:
  c.rollback();raise
 result.update({'new_version':row[2]+1,'backup':str(saved),'backup_sha256':digest(saved.read_bytes()),
  'other_state_store_rows_sha256':before,'only_internal_temp_identity_reconfirmed':True})
c.close();print(json.dumps(result))
'''


def run(command,**kwargs):
    result=subprocess.run(command,check=True,capture_output=True,**kwargs)
    return result.stdout


def inspect_mount(container,volume):
    info=json.loads(run(['docker','inspect',container],text=True))[0]
    if info['State']['Status']!='running':raise ValueError('平台容器未运行')
    matches=[m for m in info['Mounts'] if m['Destination']==MOUNT]
    if len(matches)!=1 or matches[0].get('Type')!='volume' or matches[0].get('Name')!=volume or not matches[0].get('RW'):
        raise ValueError('DBX 状态卷与显式指定名称不一致')
    return {'container_id':info['Id'],'image_id':info['Image'],'mount':matches[0]}


def probe(container,parameters):
    return json.loads(run(['docker','exec','-i',container,'python','-c',PROBE],input=json.dumps(parameters).encode()))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('inspect','apply'))
    parser.add_argument('--container',default='anp-platform-platform-1')
    parser.add_argument('--volume',required=True)
    parser.add_argument('--plan',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.mode=='apply' and (not (ROOT/'AGENTS.md').is_file() or not (ROOT/'apps').is_dir()):
        raise ValueError('请将 ANP_ROOT 设置为实际工作区；禁止把备份写入 SDK 安装目录')
    binding=inspect_mount(args.container,args.volume)
    if args.mode=='inspect':
        record={'binding':binding,'probe':probe(args.container,{'mode':'inspect'}),'scope':'仅DBX内部临时导出目录；不改变任意用户备份目标'}
        args.plan.parent.mkdir(parents=True,exist_ok=True)
        with args.plan.open('xb') as output:output.write((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
        print(json.dumps(record,ensure_ascii=False));return
    record=json.loads(args.plan.read_bytes())
    if record['binding']!=binding:raise ValueError('容器、镜像或命名卷在计划后变化，请重新检查')
    backup_id='dbx-export-identity-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    local=ROOT/'.local/backups'/backup_id;local.mkdir(parents=True,exist_ok=False)
    result=probe(args.container,{'mode':'apply','probe':record['probe'],'backup_id':backup_id})
    target=local/'dbx-before.sqlite'
    run(['docker','cp',args.container+':'+result['backup'],str(target)])
    if sha256(target.read_bytes()).hexdigest()!=result['backup_sha256']:raise ValueError('导出备份摘要不符；容器备份仍保留')
    receipt={'plan_sha256':sha256(args.plan.read_bytes()).hexdigest(),'binding':binding,'result':result,'local_backup':str(target)}
    (local/'receipt.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({'receipt':str(local/'receipt.json'),'old_device':result['old_device'],'actual_device':result['actual_device'],
        'backup_sha256':result['backup_sha256'],'new_version':result['new_version']},ensure_ascii=False))


if __name__=='__main__':main()
