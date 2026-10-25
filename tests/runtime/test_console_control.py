"""原生管理桥只作用于自有服务及非秘密配置。"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def bridge(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'native/runtime'))
    spec=importlib.util.spec_from_file_location('console_control_test',ROOT/'native/runtime/console_control.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    config=json.loads((ROOT/'native/defaults/platform.json').read_text())
    (tmp_path/'config').mkdir();(tmp_path/'config/anp.json').write_text(json.dumps(config))
    monkeypatch.setattr(value.c,'ROOT',tmp_path);monkeypatch.setattr(value.c,'VAR',tmp_path/'var')
    monkeypatch.setattr(value.c,'ORDER',value.c.platforms.ORDER)
    monkeypatch.setattr(value.c,'PLATFORM',True)
    monkeypatch.setattr(value.c,'configuration',lambda:config)
    monkeypatch.setattr(value.c,'owned',lambda name:None)
    monkeypatch.setattr(value.c,'port_open',lambda port:False)
    return value,config


def test_console_inventory_marks_optional_missing_and_protected(bridge):
    value,config=bridge
    status=value.inventory(config)
    assert all(not item['can_stop'] for item in status['services'] if item['id'] in value.PROTECTED)
    assert next(item for item in status['services'] if item['id']=='grafana')['state']=='not-installed'
    assert set(status['configuration'])=={'bind','ports','observability'}


def test_console_refuses_stopping_auth_and_does_not_kill_foreign(bridge,monkeypatch):
    value,_=bridge
    monkeypatch.setattr(sys,'argv',['console_control.py','stop'])
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'service':'auth'})))
    monkeypatch.setattr(value.c,'stop_one',lambda name:pytest.fail('不得执行停止'))
    with pytest.raises(ValueError,match='终端维护'):value.main()


def test_console_refuses_stopping_dependency_in_use(bridge,monkeypatch):
    value,_=bridge
    monkeypatch.setattr(sys,'argv',['console_control.py','stop'])
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'service':'redis'})))
    monkeypatch.setattr(value,'running',lambda name: {'pid':1} if name=='langfuse-web' else None)
    monkeypatch.setattr(value.c,'stop_one',lambda name:pytest.fail('不得停止依赖'))
    with pytest.raises(ValueError,match='依赖服务'):value.main()
