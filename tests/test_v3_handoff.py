"""真实文件往返与恶意归档拒绝，保护开发模式切换时的 Agent 状态。"""
import io
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'platform/runtime'))
from anp_platform.handoff import restore,snapshot


def test_agent_state_roundtrip_and_retained_backup(tmp_path):
    docker=tmp_path/'docker';native=tmp_path/'native';archive=tmp_path/'handoff/agent.tar'
    state=docker/'.langgraph_api';state.mkdir(parents=True)
    (state/'checkpoint.bin').write_bytes(b'\x00docker-thread\xff')
    assert snapshot(docker,archive)
    assert restore(native,archive)
    assert (native/'.langgraph_api/checkpoint.bin').read_bytes()==b'\x00docker-thread\xff'
    assert not restore(native,archive)
    (native/'.langgraph_api/checkpoint.bin').write_bytes(b'native-thread-progress')
    assert snapshot(native,archive)
    assert restore(docker,archive)
    assert (state/'checkpoint.bin').read_bytes()==b'native-thread-progress'
    backups=list((docker/'.backups').glob('*/checkpoint.bin'))
    assert len(backups)==1 and backups[0].read_bytes()==b'\x00docker-thread\xff'


@pytest.mark.parametrize('name,kind',[
    ('../outside','file'),('/outside','file'),('.langgraph_api/../../outside','file'),
    ('.langgraph_api/link','symlink'),('.langgraph_api/link','hardlink'),
])
def test_unsafe_archive_preserves_existing_state(tmp_path,name,kind):
    root=tmp_path/'state';current=root/'.langgraph_api';current.mkdir(parents=True)
    (current/'checkpoint.bin').write_bytes(b'keep')
    archive=tmp_path/'malicious.tar'
    with tarfile.open(archive,'w') as bundle:
        member=tarfile.TarInfo(name)
        if kind!='file':
            member.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE
            member.linkname='../outside';bundle.addfile(member)
        else:
            member.size=4;bundle.addfile(member,io.BytesIO(b'evil'))
    with pytest.raises(ValueError):restore(root,archive)
    assert (current/'checkpoint.bin').read_bytes()==b'keep'
    assert not (tmp_path/'outside').exists()
