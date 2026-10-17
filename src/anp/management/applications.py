"""业务自己的迁移由宿主执行，平台镜像不包含业务表定义。"""
from pathlib import Path
import json
import os
import psycopg
from anp import ROOT
from anp.applications.registry import applications
from anp.config import value,secret


def migrate_applications():
    environment=value('postgres','migration_dsn_env')
    if not environment:raise ValueError('业务迁移需要显式配置 postgres.migration_dsn_env')
    directory=Path(os.environ.get('ANP_APPLICATIONS_ROOT',ROOT/'apps'))
    applied=[]
    with psycopg.connect(secret(environment)) as connection:
        for app_id,manifest in applications().items():
            for file in sorted((directory/app_id/'migrations').glob('*.sql')):
                connection.execute(file.read_text(encoding='utf8'),prepare=False)
                name=f'apps/{app_id}/migrations/{file.name}'
                connection.execute('INSERT INTO platform.migrations(name) VALUES(%s) ON CONFLICT(name) DO UPDATE SET applied_at=now()',(name,))
                applied.append(name)
            for action in manifest['permissions']['actions']:
                for role in ['admin','developer']+(['viewer'] if action=='task.read' else []):
                    connection.execute('INSERT INTO platform.role_permissions(role,permission) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                        (role,app_id+'.'+action))
        connection.execute("NOTIFY pgrst,'reload schema'")
    return {'business_migrations':applied}


if __name__=='__main__':print(json.dumps(migrate_applications(),ensure_ascii=False))
