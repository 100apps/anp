"""控制台的本目录管理桥；不接受命令、文件路径或外来 PID。"""
import fcntl
import hashlib
import json
import sys
import control as c

PROTECTED = {'business', 'auth', 'postgrest', 'storage-api', 'postgres', 'rustfs'}
STORAGE = {'postgres', 'redis', 'clickhouse', 'rustfs'}


def revision(path):
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def running(name):
    try:
        return c.owned(name)
    except (RuntimeError, OSError, ValueError):
        return None


def installed(name):
    paths = {'grafana': 'platform/otel/grafana', 'loki': 'platform/otel/loki',
             'tempo': 'platform/otel/tempo', 'prometheus': 'platform/otel/prometheus', 'otelcol': 'platform/otel/otelcol'}
    # 观测组件未安装时不把启动失败伪装成正常停止。
    return name not in paths or (c.ROOT / paths[name]).exists()


def inventory(config):
    external = bool(config.get('external_resources_file'))
    items = []
    for name in c.ORDER:
        remote = external and name in STORAGE
        record = running(name)
        state = 'external' if remote else 'not-installed' if not installed(name) else 'ready' if record and c.healthy(name, config) else 'unhealthy' if record else 'conflict' if c.port_open(config['ports'][name]) else 'stopped'
        items.append({'id': name, 'group': 'storage' if name in STORAGE else 'observability' if name in c.platforms.observability.SERVICES else 'platform',
                      'port': config['ports'][name], 'state': state, 'pid': record['pid'] if record else None,
                      'can_start': not remote and installed(name) and state == 'stopped',
                      'can_stop': bool(record) and name not in PROTECTED,
                      'note': '使用外部存储，不启动本机进程' if remote else '控制台运行依赖；请使用 ./anp stop 在终端维护' if name in PROTECTED else '未打包此可选组件' if not installed(name) else ''})
    path = c.ROOT / 'config/anp.json'
    return {'services': items, 'configuration': {'bind': config['bind'], 'ports': config['ports'], 'observability': config.get('observability', {})}, 'revision': revision(path)}


def main():
    action = sys.argv[1]
    initial_revision = revision(c.ROOT / 'config/anp.json')
    config = c.configuration()
    if action == 'status':
        value = inventory(config)
        if initial_revision != value['revision']:
            raise ValueError('配置已变化，请刷新后重试')
        print(json.dumps(value))
        return
    data = json.load(sys.stdin)
    (c.VAR / 'run').mkdir(parents=True, exist_ok=True)
    with (c.VAR / 'run/control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'configure':
            path = c.ROOT / 'config/anp.json'
            if data.get('revision') != revision(path):
                raise ValueError('配置已变化，请刷新后重试')
            values = data['configuration']
            if set(values) != {'bind', 'ports', 'observability'} or values['bind'] != '127.0.0.1':
                raise ValueError('底层服务必须监听 127.0.0.1')
            ports = values['ports']
            if set(ports) != set(config['ports']) or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values()) or len(set(ports.values())) != len(ports):
                raise ValueError('端口须唯一且介于 1024 与 65535 之间')
            for name in c.ORDER:
                associated = [name] + [extra for extra, owner in c.platforms.EXTRA_PORTS.items() if owner == name]
                if running(name) and any(ports[k] != config['ports'][k] for k in associated):
                    raise ValueError('修改端口前请先停止对应服务：' + name)
            candidate = {**config, **values}
            c.platforms.observability.settings(candidate)
            c.write(path, json.dumps(candidate, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'saved': True, 'detail': '配置已保存；启动服务时生效'}))
            return
        name = data.get('service')
        if name not in c.ORDER or action not in ('start', 'stop'):
            raise ValueError('无效服务或操作')
        if config.get('external_resources_file') and name in STORAGE:
            raise ValueError('外部存储不能在本机启停')
        if not installed(name):
            raise ValueError('组件尚未安装')
        if action == 'start':
            c.start(config, c.selected(config, name), verbose=False)
        else:
            if name in PROTECTED:
                raise ValueError('控制台依赖服务请在终端维护')
            dependents = [service for service in c.ORDER if name in c.selected(config, service) and service != name and running(service)]
            if dependents:
                raise ValueError('请先停止依赖服务：' + ', '.join(dependents))
            c.stop_one(name)  # 仅精确操作本目录持有的进程；不停止依赖或端口上的外来进程。
        print(json.dumps({'success': True}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # 启动脚本只输出经过控制器处理的错误；不回传上游日志和命令环境。
        print(json.dumps({'detail': str(error) if isinstance(error, (ValueError, RuntimeError, BlockingIOError, AssertionError)) else '管理操作失败，请查看本目录服务日志'}, ensure_ascii=False))
        raise SystemExit(1)
