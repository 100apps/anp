"""CI 主机资源清理必须拒绝开发机，并在分配交换空间前保障磁盘余量。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_ci_disk as ci


@pytest.mark.parametrize('platform,actions,runner',[
    ('win32','true','github-hosted'),('linux','false','github-hosted'),('linux','true','self-hosted')])
def test_non_ephemeral_hosts_rejected_before_filesystem(monkeypatch,platform,actions,runner):
    monkeypatch.setattr(sys,'argv',['prepare_ci_disk.py','--extra-swap-gb','8'])
    monkeypatch.setattr(sys,'platform',platform)
    monkeypatch.setenv('GITHUB_ACTIONS',actions)
    monkeypatch.setenv('RUNNER_ENVIRONMENT',runner)
    monkeypatch.setattr(ci.shutil,'disk_usage',lambda _:pytest.fail('不应访问磁盘'))
    with pytest.raises(SystemExit,match='拒绝清理'):ci.main()


@pytest.mark.parametrize('free,allowed',[(37.39,False),(43.1,True)])
def test_swap_requires_additional_headroom(monkeypatch,free,allowed):
    monkeypatch.setattr(sys,'argv',['prepare_ci_disk.py','--minimum-gb','35','--extra-swap-gb','8'])
    monkeypatch.setattr(sys,'platform','linux')
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_ENVIRONMENT','github-hosted')
    monkeypatch.setattr(ci,'TARGETS',())
    monkeypatch.setattr(ci.shutil,'disk_usage',lambda _:SimpleNamespace(free=free*2**30))
    allocated=[]
    monkeypatch.setattr(ci,'add_swap',allocated.append)
    if allowed:
        ci.main()
        assert allocated==[8]
    else:
        with pytest.raises(SystemExit,match='剩余空间不足'):ci.main()
        assert allocated==[]
