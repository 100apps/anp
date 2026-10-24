"""可搬移原生 PoC 的生命周期。外来端口启动时拒绝接管，停止时显式确认。"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import importlib.util
from urllib.parse import quote

# -I 不将脚本目录加入 sys.path；只加入本包可信运行目录，保持 cwd 隔离。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import external

_spec = importlib.util.spec_from_file_location("anp_native_platforms", Path(__file__).with_name("platforms.py"))
platforms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platforms)

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ORDER = ["postgres", "redis", "postgrest", "litellm"]
PLATFORM = (ROOT / "platform/langfuse").exists()
if PLATFORM:
    ORDER = platforms.ORDER
OFFLINE_PROFILE = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))(deny file-read* (subpath "/opt/homebrew") (subpath "/usr/local") (subpath "/nix"))'


def confined(args):
    if sys.platform == "darwin" and os.getenv("ANP_OFFLINE") == "1":
        profile = OFFLINE_PROFILE
        denied = json.loads(os.getenv("ANP_OFFLINE_DENY_ROOTS", "[]"))
        if denied:
            assert all(isinstance(path, str) and Path(path).is_absolute() for path in denied)
            profile += "(deny file-read* " + " ".join("(subpath " + json.dumps(path, ensure_ascii=False) + ")" for path in denied) + ")"
        return ["/usr/bin/sandbox-exec", "-p", profile, *map(str, args)]
    return [str(x) for x in args]


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def configuration():
    path = ROOT / "config/anp.json"
    config = json.loads(path.read_text() if path.exists() else (ROOT / "defaults/anp.json").read_text())
    assert config["bind"] == "127.0.0.1", "PoC 仅允许 loopback"
    ports = config["ports"]
    expected = set(ORDER) | (set(platforms.EXTRA_PORTS) if PLATFORM else set())
    assert set(ports) == expected and len(set(ports.values())) == len(expected)
    assert all(isinstance(x, int) and 1024 <= x <= 65535 for x in ports.values())
    assert config["services"] and set(config["services"]) <= set(ORDER)
    external.load(config)
    return config


def private():
    path = VAR / "secrets/runtime.json"
    if not path.exists():
        write(path, json.dumps({"postgres": secrets.token_urlsafe(32), "rest": secrets.token_urlsafe(32),
                               "jwt": secrets.token_urlsafe(48), "litellm": "sk-" + secrets.token_urlsafe(32)}, indent=2) + "\n")
    values = json.loads(path.read_text())
    if PLATFORM:
        original = dict(values)
        platforms.extend_secrets(values)
        resources = external.load(configuration())
        if resources:
            roles = external.roles(resources)
            values["anon-key"] = platforms.jwt(roles["anon"], values["jwt"])
            values["service-key"] = platforms.jwt(roles["service"], values["jwt"])
        if values != original:
            write(path, json.dumps(values, indent=2) + "\n")
    return values


def env():
    # 不继承宿主数据库、模型密钥、Python 路径和 Homebrew 动态库注入。
    locale = "C" if sys.platform.startswith("linux") else "en_US.UTF-8"
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(VAR / "cache"),
            "TMPDIR": str(VAR / "run"), "LANG": locale, "LC_ALL": locale,
            "LITELLM_LOCAL_MODEL_COST_MAP": "true", "DO_NOT_TRACK": "1", "LITELLM_TELEMETRY": "false",
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}


def execute(args, check=True, **kwargs):
    result = subprocess.run(confined(args), env=env(), capture_output=True, text=True, **kwargs)
    if check and result.returncode:
        content = (result.stdout + "\n" + result.stderr)[-12000:]
        path = VAR / "secrets/runtime.json"
        if path.exists():
            for value in json.loads(path.read_text()).values():
                content = content.replace(value, "[REDACTED]")
        resources = external.load(configuration())
        if resources:
            content = external.redact(content, resources)
        log = VAR / "logs/initialization.log"
        write(log, content)
        raise RuntimeError("初始化命令失败：" + Path(str(args[0])).name + "；日志：" + str(log))
    return result


def identity(pid):
    if sys.platform.startswith("linux"):
        from linux_process import identity as linux_identity
        return linux_identity(pid)
    p = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def state_path(name):
    return VAR / "run" / (name + ".json")


def retitled_node_owned(name, pid, current):
    # Next 16.3.8 会把 argv 改成 next-server，不能仅凭进程名认领。
    # 仍须同时匹配本包的 Node 可执行文件和 Langfuse 工作目录。
    if name != "langfuse-web" or not current[24:].strip().startswith("next-server ("):
        return False
    if sys.platform.startswith("linux"):
        from linux_process import node_owned
        return node_owned(ROOT, pid)
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-d", "cwd,txt", "-Fn"], capture_output=True, text=True)
    cwd, executable = set(), set()
    descriptor = ""
    for line in result.stdout.splitlines():
        if line.startswith("f"):
            descriptor = line[1:]
        elif line.startswith("n"):
            path = Path(line[1:]).resolve()
            if descriptor == "cwd":
                cwd.add(path)
            elif descriptor == "txt":
                executable.add(path)
    allowed = {(ROOT / "platform/langfuse/web").resolve(), (ROOT / "platform/langfuse/web/web").resolve()}
    return (ROOT / "tools/node/bin/node").resolve() in executable and bool(cwd & allowed)


def owned(name):
    path = state_path(name)
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    current = identity(state["pid"])
    if not current:
        return None
    # 同一 PID 的包装脚本允许 exec；出生时间、目录与进程组必须仍一致。
    if current[:24] != state["identity"][:24]:
        raise RuntimeError("PID 已变化或不属于本目录，拒绝操作：" + name)
    try:
        group = os.getpgid(state["pid"])
    except ProcessLookupError:
        return None
    if group != state["pid"] or (str(ROOT) not in current and not retitled_node_owned(name, state["pid"], current)):
        raise RuntimeError("PID 已变化或不属于本目录，拒绝操作：" + name)
    return state


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def listener_pids(port):
    if sys.platform.startswith("linux"):
        from linux_process import listener_pids as linux_listeners
        return linux_listeners(port)
    p = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return {int(s) for s in p.stdout.split()}


def group_owns_port(state, port):
    pids = listener_pids(port)
    try:
        return bool(pids) and all(os.getpgid(pid) == state["pid"] for pid in pids)
    except ProcessLookupError:
        return False


def healthy(name, config):
    state = owned(name)
    if not state or not group_owns_port(state, config["ports"][name]):
        return False
    if name == "postgres":
        return execute([ROOT / "storage/postgres/bin/pg_isready", "-h", "127.0.0.1", "-p", config["ports"][name]], check=False).returncode == 0
    if name == "redis":
        return execute([ROOT / "storage/redis/redis-cli", "-p", config["ports"][name], "PING"], check=False).stdout.strip() == "PONG"
    try:
        path = platforms.HEALTH.get(name, "/health/liveliness") if PLATFORM else "/" if name == "postgrest" else "/health/liveliness"
        with HTTP.open("http://127.0.0.1:" + str(config["ports"][name]) + path, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def sql(config, secret, statement):
    result = subprocess.run([str(ROOT / "storage/postgres/bin/psql"), "-h", "127.0.0.1", "-p", str(config["ports"]["postgres"]),
                             "-U", "anp", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At"],
                            input=statement, env={**env(), "PGPASSWORD": secret["postgres"]}, text=True, capture_output=True)
    if result.returncode:
        # SQL 可能含秘密；不把完整 stderr/statement 打到日志。
        raise RuntimeError("PoC 数据库初始化失败，请核查本目录数据库日志")
    return result.stdout


def prepare_postgres(secret):
    database = VAR / "data/postgres"
    if not (database / "PG_VERSION").exists():
        if database.exists() and any(database.iterdir()):
            raise RuntimeError("数据目录非空且无 PG_VERSION，拒绝覆盖")
        password = VAR / "run/initdb-password"
        write(password, secret["postgres"] + "\n")
        try:
            execute([ROOT / "storage/postgres/bin/initdb", "-D", database, "-U", "anp", "--pwfile", password,
                     "--auth-host=scram-sha-256", "--auth-local=trust", "--encoding=UTF8", "--locale=C"])
        finally:
            password.unlink(missing_ok=True)


def initialize_api(config, secret):
    # 仅初始化本包自己的全新 PoC 数据库，不访问现有 Docker/受管资源。
    initialized = sql(config, secret, "SELECT to_regnamespace('anp_poc');")
    if initialized.strip():
        return
    sql(config, secret, """
BEGIN;
CREATE ROLE anp_anon NOLOGIN;
CREATE ROLE anp_user NOLOGIN;
CREATE ROLE anp_rest NOINHERIT LOGIN PASSWORD '""" + secret["rest"] + """';
GRANT anp_anon, anp_user TO anp_rest;
CREATE SCHEMA anp_poc;
CREATE TABLE anp_poc.items(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, content text NOT NULL);
GRANT USAGE ON SCHEMA anp_poc TO anp_anon, anp_user;
GRANT SELECT ON anp_poc.items TO anp_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON anp_poc.items TO anp_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA anp_poc TO anp_user;
COMMIT;
""")


def command(name, config, secret):
    if config.get("external_resources_file") and name in external.STORAGE:
        raise RuntimeError("外部资源模式不允许启动本地存储")
    port = config["ports"][name]
    environment = env()
    if name == "postgres":
        prepare_postgres(secret)
        return [ROOT / "storage/postgres/bin/postgres", "-D", VAR / "data/postgres", "-p", port, "-h", "127.0.0.1", "-k", "", "-c", "shared_buffers=64MB", "-c", "max_connections=" + ("100" if PLATFORM else "30")], environment
    if name == "redis":
        folder = VAR / "data/redis"
        folder.mkdir(parents=True, exist_ok=True)
        return [ROOT / "storage/redis/redis-server", "--bind", "127.0.0.1", "--port", port, "--dir", folder, "--appendonly", "yes", "--protected-mode", "yes", "--set-proc-title", "no"], environment
    if PLATFORM and name != "litellm":
        return platforms.command(sys.modules[__name__], name, config, secret)
    if name == "postgrest":
        initialize_api(config, secret)
        environment["DYLD_LIBRARY_PATH"] = str(ROOT / "storage/postgres/lib")
        environment.update({"PGRST_DB_URI": "postgresql://anp_rest:" + secret["rest"] + "@127.0.0.1:" + str(config["ports"]["postgres"]) + "/postgres",
                            "PGRST_DB_SCHEMAS": "anp_poc", "PGRST_DB_ANON_ROLE": "anp_anon", "PGRST_JWT_SECRET": secret["jwt"],
                            "PGRST_SERVER_HOST": "127.0.0.1", "PGRST_SERVER_PORT": str(port), "PGRST_DB_POOL": "3"})
        return [ROOT / "platform/postgrest/postgrest"], environment
    liteconfig = VAR / "run/litellm.yaml"
    write(liteconfig, 'model_list: []\ngeneral_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n')
    environment["LITELLM_MASTER_KEY"] = secret["litellm"]
    if PLATFORM:
        platforms.initialize(sys.modules[__name__], config, secret)
        environment.update({"DATABASE_URL": "postgresql://litellm:" + quote(secret["litellm-db"], safe="") + "@127.0.0.1:" + str(config["ports"]["postgres"]) + "/litellm",
                            "PRISMA_QUERY_ENGINE_BINARY": str(ROOT / "platform/litellm/engines/query-engine"),
                            "PRISMA_SCHEMA_ENGINE_BINARY": str(ROOT / "platform/litellm/engines/schema-engine"),
                            "PRISMA_BINARY_CACHE_DIR": str(ROOT / "platform/litellm/cli"),
                            "PATH": str(ROOT / "runtime/bin") + ":" + str(ROOT / "tools/node/bin") + ":" + environment["PATH"]})
        resources = external.load(config)
        if resources:
            environment["DATABASE_URL"] = external.pgurl(resources, resources["postgres"]["litellm_database"], external.roles(resources)["litellm"])
    return [ROOT / "tools/python/bin/python3", "-I", "-B", ROOT / "runtime/litellm_launcher.py", "--config", liteconfig, "--host", "127.0.0.1", "--port", port], environment


def selected(config, text=None):
    names = set(text.split(",") if text and text != "all" else config["services"])
    if not names or not names <= set(ORDER):
        raise RuntimeError("未知服务")
    cloud = bool(config.get("external_resources_file"))
    if cloud and names & external.STORAGE:
        raise RuntimeError("外部资源模式不允许选择本地存储")
    if PLATFORM:
        while True:
            expanded = names | {dependency for name in names for dependency in platforms.DEPS.get(name, [])}
            if cloud:
                expanded -= external.STORAGE
            if expanded == names:
                break
            names = expanded
    elif "postgrest" in names:
        names.add("postgres")
    return [name for name in ORDER if name in names]


def stop_one(name):
    state = owned(name)
    if state:
        os.killpg(state["pid"], signal.SIGTERM)
        for _ in range(100):
            if not identity(state["pid"]):
                break
            time.sleep(0.1)
        else:
            # 仍需复核 identity，绝不因 PID 重用终止无关进程。
            if owned(name):
                os.killpg(state["pid"], signal.SIGKILL)
    state_path(name).unlink(missing_ok=True)


def stop_conflicts(config, names, assume_yes=False):
    ports = {config["ports"][name] for name in names}
    if PLATFORM:
        ports.update(config["ports"][extra] for extra, owner in platforms.EXTRA_PORTS.items() if owner in names)
    targets = {}
    for port in sorted(ports):
        listeners = listener_pids(port)
        if not listeners and port_open(port):
            raise RuntimeError("端口被占用但无法识别监听进程，请检查进程权限：" + str(port))
        for pid in listeners:
            if pid <= 1 or pid == os.getpid():
                raise RuntimeError("拒绝停止控制进程或系统初始化进程：" + str(pid))
            if pid not in targets:
                current = identity(pid)
                if not current:
                    continue
                # 不显示完整 argv，避免泄露其他程序参数中的秘密。
                if sys.platform.startswith("linux"):
                    from linux_process import label as process_label
                    label = process_label(pid)
                else:
                    label = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()
                targets[pid] = {"identity": current, "label": Path(label).name or "未知进程", "ports": set()}
            targets[pid]["ports"].add(port)
    if not targets:
        return
    print("配置端口仍被以下进程占用：", file=sys.stderr)
    for pid, target in sorted(targets.items()):
        print(f"  PID {pid}  {target['label']}  TCP " + ", ".join(map(str, sorted(target["ports"]))), file=sys.stderr)
    if not assume_yes:
        try:
            confirmed = input("是否停止以上占用端口的进程？[y/N] ").strip().lower() in ("y", "yes", "是")
        except (EOFError, OSError):
            confirmed = False
        if not confirmed:
            raise RuntimeError("未停止占用端口的进程；非交互执行请使用 ./anp stop -y")

    def still_target(pid, target):
        current = identity(pid)
        return bool(current) and current[:24] == target["identity"][:24] and any(pid in listener_pids(port) for port in target["ports"])

    def terminate(pid, target, sig):
        # 确认后也必须重新核对出生时间和监听归属。只发给目标 PID，不杀其进程组。
        if still_target(pid, target):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                raise RuntimeError("无权停止端口占用进程 PID " + str(pid)) from None

    for pid, target in targets.items():
        terminate(pid, target, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(still_target(pid, target) for pid, target in targets.items()):
        time.sleep(0.1)
    for pid, target in targets.items():
        terminate(pid, target, signal.SIGKILL)
    # 不追杀自动重启后的新 PID；新占用者未包含在本次确认清单中。
    for _ in range(20):
        remaining = [port for port in sorted(ports) if listener_pids(port)]
        if not remaining:
            return
        time.sleep(0.1)
    raise RuntimeError("端口仍被占用（可能已被自动重启或更换占用者）：" + ", ".join(map(str, remaining)))


def progress(message, enabled=True):
    if enabled:
        print(time.strftime("[%H:%M:%S] ") + message, file=sys.stderr, flush=True)


def start(config, names, verbose=True):
    beginning = time.monotonic()
    progress(f"启动 ANP：共 {len(names)} 个服务", verbose)
    progress("运行目录：" + str(ROOT), verbose)
    config_path = ROOT / "config/anp.json"
    progress("配置文件：" + str(config_path if config_path.exists() else ROOT / "defaults/anp.json"), verbose)
    progress("数据目录：" + str(VAR / "data"), verbose)
    progress("日志目录：" + str(VAR / "logs"), verbose)
    progress("启动顺序：" + " → ".join(names), verbose)
    progress("检查主端口和附属端口是否可用…", verbose)
    # 在初始化数据和生成秘密之前拒绝所有外来监听者。
    for name in names:
        existing = owned(name)
        if port_open(config["ports"][name]) and (not existing or not group_owns_port(existing, config["ports"][name])):
            raise RuntimeError("端口被其他进程占用，未修改其状态：" + str(config["ports"][name]) + "；PID " + ", ".join(map(str, sorted(listener_pids(config["ports"][name])))))
    if PLATFORM:
        for extra, owner in platforms.EXTRA_PORTS.items():
            state = owned(owner)
            port = config["ports"][extra]
            if owner in names and port_open(port) and (not state or not group_owns_port(state, port)):
                raise RuntimeError("附属端口被其他进程占用：" + str(port) + "；PID " + ", ".join(map(str, sorted(listener_pids(port)))))
    progress("端口检查通过，准备运行目录和配置…", verbose)
    for folder in ("run", "data", "logs", "cache", "secrets"):
        (VAR / folder).mkdir(parents=True, exist_ok=True)
    secret = private()
    started = []
    pids = {}
    try:
        for index, name in enumerate(names, 1):
            prefix = f"[{index:02d}/{len(names):02d}] {name}"
            service_beginning = time.monotonic()
            existing = owned(name)
            if existing:
                if healthy(name, config):
                    pids[name] = existing["pid"]
                    progress(f"{prefix} 已在运行，复用 PID {existing['pid']}，端口 {config['ports'][name]}", verbose)
                    continue
                raise RuntimeError("已有本目录服务不健康，请先 stop：" + name)
            ports = [config["ports"][name]]
            if PLATFORM:
                ports += [config["ports"][extra] for extra, owner in platforms.EXTRA_PORTS.items() if owner == name]
            progress(prefix + " 准备启动，TCP " + ", ".join(map(str, ports)), verbose)
            progress("  日志：" + str(VAR / "logs" / (name + ".log")), verbose)
            cmd, environment = command(name, config, secret)
            with (VAR / "logs" / (name + ".log")).open("ab") as log:
                working = ROOT / "platform/langfuse/web" if name == "langfuse-web" else VAR / "data/rustfs" if name == "rustfs" else ROOT
                process = subprocess.Popen(confined(cmd), cwd=working, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                # 包装脚本允许 exec；记录身份失败也必须回收刚创建的进程。
                time.sleep(0.3)
                record = {"pid": process.pid, "identity": identity(process.pid), "root": str(ROOT)}
                write(state_path(name), json.dumps(record))
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                raise
            started.append(name)
            pids[name] = process.pid
            progress(f"{prefix} 进程已创建，PID {process.pid}，等待就绪…", verbose)
            next_notice = time.monotonic() + 5
            for _ in range(1200 if name in ("langfuse-web", "litellm") else 240):
                if process.poll() is not None:
                    raise RuntimeError(f"启动退出：{name}，PID {process.pid}，退出码 {process.returncode}；日志：" + str(VAR / "logs" / (name + ".log")))
                if healthy(name, config):
                    break
                if time.monotonic() >= next_notice:
                    progress(f"{prefix} 仍在等待就绪，PID {process.pid}，已用时 {time.monotonic() - service_beginning:.1f}s", verbose)
                    next_notice = time.monotonic() + 5
                time.sleep(0.5)
            else:
                raise RuntimeError("启动超时：" + name + "；日志：" + str(VAR / "logs" / (name + ".log")))
            progress(f"{prefix} 就绪，PID {process.pid}，用时 {time.monotonic() - service_beginning:.1f}s", verbose)
        # 某个早期服务可能在后续服务初始化时退出；不能返回成功但 ready=false。
        progress("所有服务已启动，正在复核健康状态…", verbose)
        for attempt in range(3):
            status = {name: {"ready": healthy(name, config), "port": config["ports"][name]} for name in names}
            failed = [name for name, value in status.items() if not value["ready"]]
            if not failed:
                break
            time.sleep(0.5)
        if failed:
            raise RuntimeError("启动最终检查未通过：" + ", ".join(failed))
    except BaseException:
        progress(f"启动未完成，回滚本次新启动的 {len(started)} 个服务…", verbose)
        for name in reversed(started):
            progress("  停止：" + name, verbose)
            stop_one(name)
        progress("回滚完成；原先已运行的服务保留。", verbose)
        raise
    progress(f"启动完成：{len(names)}/{len(names)} 个服务就绪，总用时 {time.monotonic() - beginning:.1f}s", verbose)
    for name in names:
        status[name]["pid"] = pids[name]
    return status


def print_start_summary(result, config):
    print("\n服务                 PID      端口     状态")
    for name, item in result.items():
        print(f"{name:<20} {item['pid']:<8} {item['port']:<8} 就绪")
    if PLATFORM and "gateway" in result:
        origin = "http://127.0.0.1:" + str(config["ports"]["gateway"])
        print("\nSupabase Studio：" + origin + "/")
        print("Langfuse：" + origin + "/langfuse/")
        print("LiteLLM：" + origin + "/litellm/")
        if "business" in result:
            print("业务工作台：" + origin + "/workbench/")
        if "dbx" in result:
            print("DBX：" + origin + "/dbx/")
    else:
        for name in ("studio", "langfuse-web", "litellm", "postgrest"):
            if name in result:
                suffix = "/langfuse/" if name == "langfuse-web" else "/"
                print(name + "：http://127.0.0.1:" + str(result[name]["port"]) + suffix)
    print("\n查看状态：./anp status")
    print("停止服务：./anp stop")
    print("服务日志：" + str(VAR / "logs") + "/<服务名>.log")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="ANP macOS ARM64 原生 PoC")
    parser.add_argument("command", choices=["start", "stop", "status", "config-path", "verify"])
    parser.add_argument("--services", default=None)
    parser.add_argument("--json", action="store_true", help="start/status 输出 JSON；start 不显示进度")
    parser.add_argument("-y", "--yes", action="store_true", help="stop 时不询问，直接停止占用配置端口的进程")
    args = parser.parse_args()
    if args.yes and args.command != "stop":
        parser.error("-y/--yes 仅适用于 stop")
    if args.json and args.command not in ("start", "status"):
        parser.error("--json 仅适用于 start/status")
    if os.geteuid() == 0:
        raise SystemExit("使用普通用户运行，不需要 sudo")
    if args.command == "config-path":
        print(ROOT / "config/anp.json")
        print(VAR / "secrets/runtime.json")
        return 0
    if args.command == "verify":
        hashes = json.loads((ROOT / "manifest/files.sha256.json").read_text())
        for name, expected in hashes.items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, "文件校验失败：" + name
        print(json.dumps({"passed": True, "files": len(hashes)}))
        return 0
    config = configuration()
    if args.command == "status":
        result = {name: {"ready": healthy(name, config), "port": config["ports"][name]} for name in selected(config, args.services)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(x["ready"] for x in result.values()) else 1
    # 锁文件在本目录内；不同解压目录生命周期互不影响。
    (VAR / "run").mkdir(parents=True, exist_ok=True)
    with (VAR / "run/control.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一个 ANP 启动或停止命令正在执行，请等待其结束；锁文件：" + str(VAR / "run/control.lock")) from None
        if args.command == "stop":
            names = selected(config, args.services) if args.services else ORDER
            for name in reversed(names):
                try:
                    stop_one(name)
                except RuntimeError:
                    # 失效记录不能阻断 stop -y，也不能作为终止外来 PID 的依据。
                    print("忽略失效进程记录，端口占用将单独确认：" + name, file=sys.stderr)
                    state_path(name).unlink(missing_ok=True)
            stop_conflicts(config, names, args.yes)
            print("本目录服务已停止；数据保留")
        else:
            result = start(config, selected(config, args.services), verbose=not args.json)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print_start_summary(result, config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, AssertionError, BlockingIOError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
