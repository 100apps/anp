"""检查点失败必须保持现有最终标签，且不能清理其他镜像。"""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bundle_platform as bundle


def test_failed_checkpoint_never_builds_final_tag(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if '--target' in command and command[command.index('--target') + 1] == 'platform_root_1':
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bundle.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        bundle.build_image(tmp_path / 'Dockerfile', 'anp/platform:existing',
                           ('platform_root_0', 'platform_root_1'), context=tmp_path)
    assert all('anp/platform:existing' not in command for command in calls)
    removals = [command for command in calls if command[:3] == ['docker', 'image', 'rm']]
    assert len(removals) == 1
    assert removals[0][3].startswith('anp-build/platform-checkpoint:')
    assert removals[0][3] == calls[0][calls[0].index('-t') + 1]


def test_foreign_stage_rejected_before_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(bundle.subprocess, 'run', lambda *a, **k: pytest.fail('不应调用 Docker'))
    with pytest.raises(ValueError, match='检查点'):
        bundle.build_image(tmp_path / 'Dockerfile', 'anp/platform:existing', ('unrelated',))
