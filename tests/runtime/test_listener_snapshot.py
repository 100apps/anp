import importlib.util
import os
from pathlib import Path


def test_batch_listener_snapshot_distinguishes_shared_and_unrelated_ports(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'native/runtime/linux_process.py'
    spec = importlib.util.spec_from_file_location('listener_snapshot_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    net = tmp_path / 'net'
    net.mkdir()
    lines = ['header\n']
    for port, inode, state in [(58000, 111, '0A'), (53100, 222, '0A'), (58000, 333, '01')]:
        lines.append(f'0: 0100007F:{port:04X} 00000000:0000 {state} 0 0 0 0 0 {inode}\n')
    (net / 'tcp').write_text(''.join(lines))
    for pid, inode in [(100, 111), (101, 111), (102, 222), (103, 333)]:
        folder = tmp_path / str(pid) / 'fd'
        folder.mkdir(parents=True)
        os.symlink(f'socket:[{inode}]', folder / '1')
    result = module.listener_snapshot([58000, 53100, 59000], proc_root=tmp_path)
    assert result == {58000: {100, 101}, 53100: {102}, 59000: set()}
