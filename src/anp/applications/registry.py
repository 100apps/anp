"""根据声明发现模块，禁止目录穿越和重复应用标识。"""
from functools import lru_cache
from importlib import import_module
import re
import tomllib
from anp import ROOT


def entrypoint(path):
    module, attr = path.split(':', 1)
    return getattr(import_module(module), attr)


@lru_cache
def applications():
    result = {}
    for file in sorted((ROOT / 'apps').glob('*/app.toml')):
        manifest = tomllib.loads(file.read_text(encoding='utf-8'))
        app_id = manifest['id']
        if not re.fullmatch(r'[a-z][a-z0-9_]*', app_id) or app_id != file.parent.name:
            raise ValueError(f'应用 ID 必须与目录相同，并使用小写标识：{file}')
        if app_id in result:
            raise ValueError(f'重复应用 ID：{app_id}')
        if manifest.get('enabled', True):
            result[app_id] = manifest
    return result


def application(app_id):
    if app_id not in applications():
        raise ValueError(f'应用不存在：{app_id}')
    return applications()[app_id]
