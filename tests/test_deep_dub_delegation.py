"""作业令牌文件与MAC边界；使用临时目录和假的会话文本，不调用平台。"""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from anp.errors import BusinessError
from apps.deep_dub.services.delegation import JobVault


@pytest.fixture
def grant(tmp_path):
    vault=JobVault(tmp_path)
    run,attempt=str(uuid4()),str(uuid4())
    record={'version':1,'scope':'deep_dub.full_decode','run_id':run,'attempt_id':attempt,
            'actor_token':'fixture-only-no-real-session','expires_at':time.time()+30}
    return vault,record,run,attempt


def test_grant_contains_no_original_user_session_and_detects_tamper(grant):
    vault,record,run,attempt=grant;token=vault.save(record)
    assert vault.load(token,run,attempt)==record
    leaked=record['actor_token'] in token
    assert not leaked
    changed=token[:-1]+('0' if token[-1]!='0' else '1')
    with pytest.raises(BusinessError) as error:vault.load(changed,run,attempt)
    assert error.value.code=='INVALID_JOB_GRANT'


def test_grant_scope_and_expiry_cannot_be_changed(grant):
    vault,record,run,attempt=grant;token=vault.save(record)
    with pytest.raises(BusinessError) as error:vault.load(token,run,str(uuid4()))
    assert error.value.code=='JOB_SCOPE_MISMATCH'
    expired=vault.save({**record,'expires_at':time.time()-1})
    with pytest.raises(BusinessError) as error:vault.load(expired,run,attempt)
    assert error.value.code=='JOB_GRANT_EXPIRED'
    path=vault.directory/(token.split('.')[0]+'.json')
    path.write_text(json.dumps({**record,'expires_at':time.time()+10000}),encoding='utf8')
    with pytest.raises(BusinessError) as error:vault.load(token,run,attempt)
    assert error.value.code=='INVALID_JOB_GRANT'


def test_concurrent_first_key_initialization_and_grants(grant):
    vault,record,run,attempt=grant
    def issue(_):
        current=JobVault(vault.directory);token=current.save(record)
        return current.load(token,run,attempt)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(issue,range(24)))
    assert all(result==record for result in results)
    assert len((vault.directory/'signing.key').read_bytes())==32
    assert not list(vault.directory.glob('.signing-*'))


def test_private_directory_removes_broad_inheritance_for_existing_files(grant):
    vault,record,run,attempt=grant
    existing=vault.directory/'existing-fixture.json'
    existing.write_text('not-a-real-session',encoding='utf8')
    token=vault.save(record)
    assert vault.load(token,run,attempt)==record
    if sys.platform!='win32':
        assert vault.directory.stat().st_mode & 0o777 == 0o700
        return
    script="""
$ErrorActionPreference='Stop'
$allowed=@([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18')
$directory=$env:ANP_DELEGATION_PRIVATE_DIRECTORY
$acl=[System.IO.Directory]::GetAccessControl($directory)
if(-not $acl.AreAccessRulesProtected) { exit 11 }
$items=@($acl)
foreach($file in [System.IO.Directory]::GetFiles($directory)) {
 $items+= [System.IO.File]::GetAccessControl($file)
}
foreach($item in $items) {
 foreach($rule in $item.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
  if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $allowed) { exit 12 }
 }
}
"""
    environment=os.environ.copy();environment['ANP_DELEGATION_PRIVATE_DIRECTORY']=str(vault.directory)
    check=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],
        env=environment,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    assert check.returncode==0, '目录或已有文件仍向其他账号授予读取权限'
