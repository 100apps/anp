"""私有配置可重复初始化；不能重置操作者的存储路径或已有密码。"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import manage


def test_init_preserves_explicit_root_and_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(manage,'ROOT',tmp_path)
    (tmp_path/'business').mkdir()
    (tmp_path/'.env').write_text('ANP_DATA_ROOT="/srv/anp3/owned-data"\nANP3_SERVICES=redis\nPOSTGRES_PASSWORD=existing-secret-for-test\n',encoding='utf-8')
    manage.init()
    first=manage.read_env(tmp_path/'.env')
    manage.init()
    second=manage.read_env(tmp_path/'.env')
    assert first==second
    assert second['ANP_DATA_ROOT']=='/srv/anp3/owned-data'
    assert second['POSTGRES_PASSWORD']=='existing-secret-for-test'
    assert second['ANP3_SERVICES']=='redis'
