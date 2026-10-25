"""完整原生包验收：真实 Auth、Data API/RLS、Storage、Studio、Langfuse。"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

ROOT = Path(__file__).resolve().parents[2]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def owned_prisma_engine(bundle, pid, group, litellm_group):
    """仅识别本实例 LiteLLM 进程组内的固定官方查询引擎。"""
    if not litellm_group or group != litellm_group:
        return False
    if sys.platform.startswith("linux"):
        expected = bundle / "var/cache/elf/platform/litellm/engines/query-engine.elf"
        try:
            return os.readlink("/proc/" + str(pid) + "/exe") == str(expected)
        except OSError:
            return False
    expected = str(bundle / "platform/litellm/engines/query-engine")
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-d", "txt", "-Fn"], capture_output=True, text=True)
    return "n" + expected in result.stdout.splitlines()


def inspect_linux_library_mappings(bundle, executable, lines):
    mappings = []
    for line in lines:
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        name = fields[5].replace("\\040", " ").removesuffix(" (deleted)")
        if ".so" in Path(name).name or name.endswith(".node"):
            mappings.append((name, fields[1]))
    own_loader = any(Path(name).is_relative_to(bundle) and Path(name).name == "ld-linux-x86-64.so.2" and "x" in mode for name, mode in mappings)
    clickhouse = Path(executable) == bundle / "var/cache/elf/storage/clickhouse/clickhouse.elf"
    foreign, metadata = [], []
    for name, mode in mappings:
        if Path(name).is_relative_to(bundle):
            continue
        # ClickHouse SymbolIndex 可选地只读映射旧 PT_INTERP 名称对应的 ELF。
        # 仅允许加载器的只读数据映射，同时必须看到实际包内加载器代码。
        # Alpine 没有该文件也能正常启动和查询；不能把它混同于执行宿主库。
        if (clickhouse and own_loader and Path(name).name == "ld-linux-x86-64.so.2" and mode == "r--p"
                and all(other_mode == "r--p" for other_name, other_mode in mappings if other_name == name)):
            metadata.append({"path": name, "permissions": mode, "purpose": "optional-symbol-metadata"})
        else:
            foreign.append({"library": name, "permissions": mode, "process": executable})
    return foreign, metadata


def linux_listeners_and_libraries(bundle, groups):
    """检查实际服务进程与子进程，不依赖容器安装 lsof/ps。"""
    spec = __import__("importlib.util", fromlist=["spec_from_file_location"])
    description = spec.spec_from_file_location("anp_accept_linux_process", bundle / "runtime/linux_process.py")
    process = spec.module_from_spec(description)
    description.loader.exec_module(process)
    listeners = set()
    internal = set()
    litellm_file = bundle / "var/run/litellm.json"
    litellm_group = json.loads(litellm_file.read_text())["pid"] if litellm_file.exists() else None
    foreign = []
    metadata = []
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        if not Path(filename).exists():
            continue
        for line in Path(filename).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] != "0A":
                continue
            address, port = fields[1].split(":")
            port = int(port, 16)
            owners = [pid for pid in process.listener_pids(port) if os.getpgid(pid) in groups]
            if owners:
                assert filename.endswith("/tcp") and address == "0100007F", "存在非 loopback 监听"
                if all(owned_prisma_engine(bundle, pid, os.getpgid(pid), litellm_group) for pid in owners):
                    internal.add(port)
                else:
                    listeners.add(port)
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            if os.getpgid(int(folder.name)) not in groups:
                continue
            lines = (folder / "maps").read_text().splitlines()
        except (ProcessLookupError, FileNotFoundError):
            continue
        bad, notes = inspect_linux_library_mappings(bundle, os.readlink(folder / "exe"), lines)
        foreign.extend(bad)
        metadata.extend(notes)
    assert not foreign, "包外动态库：" + json.dumps(foreign, ensure_ascii=False)
    assert len(internal) <= 1, "Prisma 查询引擎出现多个内部监听"
    return listeners, metadata, internal


def main():
    target = "linux-amd64" if sys.platform.startswith("linux") else "macos-arm64"
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--host-runtime", action="store_true", help="Linux 实机功能验收；不声明无外网或无宿主工具")
    parser.add_argument("--bundle", type=Path, default=ROOT / ("var/native/build/anp-" + target))
    args = parser.parse_args()
    if args.host_runtime:
        assert sys.platform.startswith("linux") and os.getenv("ANP_CLEAN_RUNTIME") != "1", "实机验收与干净容器验收不能混用"
    receipt = {"passed": False, "target": target, "remote_model_calls": 0, "synthetic_trace": True,
               "acceptance_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "checks": {}}
    if args.host_runtime:
        receipt["runtime_scope"] = "linux-host"
        receipt["external_network_isolation_enforced"] = False
    evidence = ROOT / "var/native/evidence/platform"
    evidence.mkdir(parents=True, exist_ok=True)
    acceptance = ROOT / "var/native/acceptance"
    acceptance.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="platform-check-", dir=acceptance) as scratch:
        folder = Path(scratch)
        bundle = folder / "首次 解压"
        if args.archive:
            sha = hashlib.file_digest(args.archive.open("rb"), "sha256").hexdigest()
            assert sha == args.archive.with_suffix(args.archive.suffix + ".sha256").read_text().split()[0]
            with tarfile.open(args.archive) as tar:
                tar.extractall(folder, filter="data")
            (folder / ("anp-" + target)).rename(bundle)
            receipt["archive_sha256"] = sha
        else:
            assert not (args.bundle / "var").exists(), "拒绝复制已有用户运行数据"
            if sys.platform == "darwin":
                subprocess.run(["/bin/cp", "-cR", str(args.bundle), str(bundle)], check=True)
            else:
                shutil.copytree(args.bundle, bundle, symlinks=True)
        build_info = json.loads((bundle / "manifest/build.json").read_text())
        receipt["version"] = build_info["version"]
        receipt["bundle_files_manifest_sha256"] = hashlib.sha256((bundle / "manifest/files.sha256.json").read_bytes()).hexdigest()
        defaults = json.loads((bundle / "defaults/anp.json").read_text())
        ports = {}
        sockets = []
        for name in defaults["ports"]:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
            ports[name] = sock.getsockname()[1]
        for sock in sockets:
            sock.close()
        defaults["ports"] = ports
        defaults["observability"]["enabled"] = True
        defaults["services"] = list(dict.fromkeys(defaults["services"] + ["otelcol", "grafana", "prometheus", "loki", "tempo"]))
        (bundle / "config/anp.json").write_text(json.dumps(defaults))
        print("独立验收副本已准备", flush=True)
        denied = [ROOT / "platform/sources", ROOT / "var/native/langfuse-build", ROOT / "native", ROOT / "business", Path.home() / "Library/pnpm"]
        denied += [path for path in (ROOT / "var/native").iterdir() if path.is_dir() and path.name != "acceptance"]

        def cli(command, check=True):
            result = subprocess.run([str(bundle / "anp"), command], env={**os.environ, "ANP_OFFLINE": "1",
                                    "ANP_OFFLINE_DENY_ROOTS": json.dumps(list(map(str, denied)))},
                                    capture_output=True, text=True, timeout=900)
            assert not check or result.returncode == 0, command + "：" + result.stderr[-1500:]
            return result

        def api(path, method="GET", data=None, headers=None, service="business"):
            payload = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
            request = urllib.request.Request("http://127.0.0.1:" + str(ports[service]) + path, data=payload,
                method=method, headers={"Content-Type": "application/json", **(headers or {})})
            try:
                with HTTP.open(request, timeout=60) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        private = {}
        try:
            from .poc_check import SANDBOX
            sandbox = []
            if sys.platform == "darwin":
                profile = SANDBOX[2] + "(deny file-read* " + " ".join("(subpath " + json.dumps(str(p)) + ")" for p in denied) + ")"
                sandbox = ["/usr/bin/sandbox-exec", "-p", profile]
                probe = "import socket,errno; s=socket.socket();s.settimeout(1);assert s.connect_ex(('192.0.2.1',9))==errno.EPERM\ntry: open(" + repr(str(ROOT / "native/build.py")) + ")\nexcept PermissionError: pass\nelse: raise AssertionError('source tree was readable')"
            elif not args.host_runtime:
                assert os.getenv("ANP_CLEAN_RUNTIME") == "1", "Linux 验收需要干净容器"
                assert all(shutil.which(name) is None for name in ("python3", "node", "uv", "gcc", "patchelf"))
                assert not (ROOT / "platform/sources").exists() and not (ROOT / "business").exists()
                probe = "import socket,errno;s=socket.socket();s.settimeout(1);assert s.connect_ex(('192.0.2.1',9)) in (errno.ENETUNREACH,errno.EPERM)"
            if not args.host_runtime:
                subprocess.run([*sandbox, str(bundle / "tools/python/bin/python3"), "-I", "-B", "-c", probe], check=True, capture_output=True)
            for command in ([bundle / "tools/uv/uv", "--version"], [bundle / "tools/python/bin/python3", "--version"],
                            [bundle / "tools/node/bin/node", "-e", "if(process.arch!==" + repr("arm64" if target == "macos-arm64" else "x64") + ")process.exit(1);if(process.env.LD_LIBRARY_PATH||process.env.LD_PRELOAD)throw Error('host library environment leaked');for(const [p,a] of [[process.execPath,['--version']],['/bin/sh',['-c','true']]]){let r=require('child_process').spawnSync(p,a);if(r.status!==0)process.exit(2)}"],
                            [bundle / "tools/python/bin/python3", "-I", "-c", "import os,subprocess,sys;assert not {'LD_LIBRARY_PATH','LD_PRELOAD'} & os.environ.keys();subprocess.run([sys.executable,'-I','-c','print(42)'],check=True);subprocess.run(['/bin/sh','-c','true'],check=True)"]):
                subprocess.run([*sandbox, *map(str, command)], check=True, capture_output=True,
                               env={"PATH": "/usr/bin:/bin", "HOME": str(bundle / "var/cache")})
            receipt["checks"]["bundled_uv_python_node_native_tools"] = True
            # 显式使用包内官方引擎，即使 PATH 里没有 openssl 也必须可用。
            engine = bundle / "platform/litellm/engines/query-engine"
            probe_engine = (
                "import os,site,subprocess;from pathlib import Path;"
                "site.addsitedir(" + repr(str(bundle / "platform/litellm/site-packages")) + ");"
                "from prisma.engine.utils import ensure\n"
                "original_run=subprocess.run\n"
                "def guarded_run(args,**kwargs):\n"
                " assert Path(args[0]).name!='openssl','unexpected host openssl probe'\n"
                " return original_run(args,**kwargs)\n"
                "subprocess.run=guarded_run\n"
                "assert ensure({})==Path(os.environ['PRISMA_QUERY_ENGINE_BINARY'])"
            )
            engine_probe = subprocess.run([*sandbox, str(bundle / "tools/python/bin/python3"), "-I", "-B", "-c", probe_engine],
                           env={"PATH": "/usr/bin:/bin", "HOME": str(bundle / "var/cache"),
                                "PRISMA_QUERY_ENGINE_BINARY": str(engine)},
                           text=True, capture_output=True)
            assert engine_probe.returncode == 0, "Prisma engine probe failed: " + engine_probe.stderr[-1500:]
            receipt["checks"]["prisma_explicit_engine_without_host_openssl"] = True
            cli("verify")
            receipt["checks"]["immutable_manifest"] = True
            with socket.socket() as foreign:
                foreign.bind(("127.0.0.1", ports["clickhouse-native"]))
                foreign.listen()
                assert cli("start", False).returncode != 0
                assert not (bundle / "var/secrets/runtime.json").exists()
            receipt["checks"]["foreign_auxiliary_listener_rejected"] = True
            cli("start")
            cli("status")
            receipt["checks"]["all_services_ready"] = True
            print(f"{len(defaults['services'])} 个服务已就绪，开始真实 API 验收", flush=True)
            if not args.host_runtime:
                receipt["checks"]["external_network_host_package_managers_and_source_tree_denied" if sandbox else "network_none_and_no_host_platform_business_sources"] = True
            groups = {json.loads(p.read_text())["pid"] for p in (bundle / "var/run").glob("*.json")}
            if target == "linux-amd64":
                listeners, metadata, internal = linux_listeners_and_libraries(bundle, groups)
                receipt["checks"]["all_executable_library_mappings_in_bundle"] = True
                receipt["clickhouse_optional_host_symbol_metadata"] = metadata
            else:
                processes = {int(parts[0]): int(parts[1]) for line in subprocess.check_output(["/bin/ps", "-Ao", "pid=,pgid="], text=True).splitlines() if len(parts := line.split()) == 2}
                sockets = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], capture_output=True, text=True)
                pid = None
                listeners = []
                internal = set()
                litellm_group = json.loads((bundle / "var/run/litellm.json").read_text())["pid"]
                engine_pids = {pid for pid, group in processes.items() if owned_prisma_engine(bundle, pid, group, litellm_group)}
                for line in sockets.stdout.splitlines():
                    if line.startswith("p"):
                        pid = int(line[1:])
                    elif line.startswith("n") and processes.get(pid) in groups:
                        address = line[1:]
                        assert address.startswith("127.0.0.1:"), "发现非 loopback 服务监听：" + address
                        port = int(address.rsplit(":", 1)[1])
                        if pid in engine_pids:
                            internal.add(port)
                        else:
                            listeners.append(port)
                assert len(internal) <= 1, "Prisma 查询引擎出现多个内部监听"
            receipt["internal_prisma_http_ports"] = sorted(internal)
            import sys as _sys
            _sys.path.insert(0, str(bundle / "runtime"))
            import platforms as _platforms
            active_ports = {value for key, value in ports.items() if key != "tempo-memberlist" and (key in defaults["services"] or _platforms.EXTRA_PORTS.get(key) in defaults["services"])}
            assert set(listeners) == active_ports, "服务存在未记录的附属监听端口"
            receipt["checks"]["all_process_group_listeners_loopback_and_accounted"] = True
            private = json.loads((bundle / "var/secrets/runtime.json").read_text())
            if "business" in ports:
                from .business_check import check as check_business
                origin = "http://127.0.0.1:" + str(ports["business"])
                check_business(origin, origin, bundle / "var/secrets/runtime.json", acceptance / "business-last.json")
                receipt["checks"]["business_supabase_auth_roles_threads_and_demos"] = True
                from .dbx_check import check as check_dbx
                check_dbx(origin, ports["postgres"], private)
                receipt["checks"]["dbx_admin_proxy_assets_login_real_query"] = True
                from .ingress_check import check as check_ingress
                from .storage_check import check as check_storage
                check_ingress(bundle, acceptance / "ingress-last.json")
                receipt["checks"]["single_entry_admin_studio_and_sdk_boundaries"] = True
                storage_result = check_storage(bundle)
                (acceptance / "storage-last.json").write_text(json.dumps(storage_result, indent=2) + "\n")
                receipt["checks"]["storage_ordinary_and_resumable_8mib_upload_readback"] = True
            redis = [str(bundle / "storage/redis/redis-cli"), "-p", str(ports["redis"])]
            subprocess.run([*redis, "SET", "anp:native:acceptance", "persisted"], check=True, capture_output=True)
            vector = subprocess.check_output([*redis, "VADD", "anp:native:vector", "VALUES", "2", "1", "2", "point"], text=True).strip()
            assert vector == "1"
            admin = {"apikey": private["service-key"], "Authorization": "Bearer " + private["service-key"]}
            anon = {"apikey": private["anon-key"], "Authorization": "Bearer " + private["anon-key"]}

            def sql(statement):
                result = subprocess.run([str(bundle / "storage/postgres/bin/psql"), "-h", "127.0.0.1", "-p", str(ports["postgres"]),
                    "-U", "anp", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At"], input=statement,
                    env={"PATH": "/usr/bin:/bin", "PGPASSWORD": private["postgres"]}, capture_output=True, text=True)
                assert result.returncode == 0, "验收 fixture SQL 初始化失败"

            users = []
            for index in range(2):
                email = "native-" + uuid.uuid4().hex + "@example.test"
                password = uuid.uuid4().hex + "!Anp1"
                status, body = api("/auth/v1/signup", "POST", {"email": email, "password": password}, anon)
                assert status in (200, 201), "Auth 注册失败：" + str(status)
                status, body = api("/auth/v1/token?grant_type=password", "POST", {"email": email, "password": password}, anon)
                assert status == 200, "Auth 登录失败：" + str(status)
                user = json.loads(body)
                header = {"apikey": private["anon-key"], "Authorization": "Bearer " + user["access_token"]}
                assert api("/auth/v1/user", headers=header)[0] == 200
                users.append((user["user"]["id"], header))
            receipt["checks"]["auth_signup_password_login_current_user"] = True
            status, body = api("/auth/v1/admin/users", headers=admin)
            assert status == 200 and {item[0] for item in users} <= {u["id"] for u in json.loads(body)["users"]}
            receipt["checks"]["auth_admin_user_list"] = True
            table = "native_" + uuid.uuid4().hex[:12]
            bucket = "native-" + uuid.uuid4().hex[:12]
            sql(f'''CREATE TABLE public.{table}(id uuid PRIMARY KEY DEFAULT extensions.gen_random_uuid(), owner_id uuid NOT NULL DEFAULT auth.uid(), content text NOT NULL);
ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.{table} TO authenticated,service_role;
CREATE POLICY own_rows ON public.{table} TO authenticated USING (owner_id=auth.uid()) WITH CHECK (owner_id=auth.uid());
CREATE FUNCTION public.{table}_count() RETURNS bigint LANGUAGE sql STABLE SECURITY INVOKER AS $$ SELECT count(*) FROM public.{table} $$;
GRANT EXECUTE ON FUNCTION public.{table}_count() TO authenticated;
CREATE POLICY {table}_files ON storage.objects TO authenticated USING (bucket_id='{bucket}' AND owner_id=auth.uid()::text) WITH CHECK (bucket_id='{bucket}' AND owner_id=auth.uid()::text);
NOTIFY pgrst,'reload schema';''')
            time.sleep(2)
            owner = {**users[0][1], "Prefer": "return=representation"}
            other = users[1][1]
            path = "/rest/v1/" + table
            status, body = api(path, "POST", {"content": "native real persistence"}, owner)
            assert status == 201, "Data API 创建失败：" + str(status)
            item = json.loads(body)[0]["id"]
            assert json.loads(api(path, headers=other)[1]) == []
            assert api(path, "POST", {"owner_id": users[0][0], "content": "forged owner"}, other)[0] in (401, 403)
            assert api(path, headers=anon)[0] in (401, 403)
            status, body = api(path + "?id=eq." + item, "PATCH", {"content": "updated"}, owner)
            assert status == 200 and json.loads(body)[0]["content"] == "updated"
            assert json.loads(api("/rest/v1/rpc/" + table + "_count", "POST", {}, owner)[1]) == 1
            receipt["checks"]["data_api_crud_rpc_user_rls"] = True
            status, _ = api("/storage/v1/bucket", "POST", {"id": bucket, "name": bucket, "public": False}, admin)
            assert status in (200, 201), "Storage 创建桶失败：" + str(status)
            content = ("ANP Mac 真实文件 " + uuid.uuid4().hex).encode()
            obj = "/storage/v1/object/" + bucket + "/" + users[0][0] + "/probe.txt"
            status, _ = api(obj, "POST", content, {**owner, "Content-Type": "text/plain"})
            assert status in (200, 201), "Storage 上传失败：" + str(status)
            assert api(obj, headers=owner) == (200, content)
            assert api(obj, headers=other)[0] in (400, 401, 403, 404)
            assert api(obj, headers=anon)[0] in (400, 401, 403, 404)
            status, body = api("/storage/v1/object/sign/" + bucket + "/" + users[0][0] + "/probe.txt", "POST", {"expiresIn": 60}, owner)
            assert status == 200
            signed = json.loads(body)["signedURL"]
            if signed.startswith("/object/"):
                signed = "/storage/v1" + signed
            assert api(signed) == (200, content)
            receipt["checks"]["storage_upload_download_signed_url_user_rls"] = True
            status, body = api("/auth/v1/token?grant_type=password", "POST", {"email": "admin@anp.local", "password": private["business-user-password"]}, anon)
            assert status == 200
            console_headers = {"Authorization": "Bearer " + json.loads(body)["access_token"]}
            status, body = api("/api/platform/pg-meta/default/query", "POST", {"query": "SELECT 1 AS value"}, console_headers)
            assert status == 200 and json.loads(body) == [{"value": 1}], "Studio 实际查询失败"
            receipt["checks"]["studio_real_database_query"] = True
            status, body = api("/project/default", headers=console_headers)
            assert status == 200 and b"/_next/" in body, "Studio 页面失败"
            asset = re.search(rb'<script[^>]+src="([^"]+)"', body)
            assert asset and api(asset[1].decode().replace("&amp;", "&"), headers=console_headers)[0] == 200, "Studio 脚本资源加载失败"
            receipt["checks"]["studio_page"] = True
            assert api("/litellm/v1/models")[0] in (401, 403)
            assert api("/litellm/v1/models", headers={"Authorization": "Bearer " + private["litellm"]})[0] == 200
            receipt["checks"]["litellm_gateway_auth"] = True
            status, body = api("/litellm/v2/login", "POST", {"username": "admin", "password": private["litellm"]}, service="litellm")
            assert status == 200 and bool(json.loads(body).get("token")), "LiteLLM 管理台登录失败"
            receipt["checks"]["litellm_dashboard_database_login"] = True
            assert api("/realtime/v1/websocket")[0] == 404
            receipt["checks"]["realtime_excluded"] = True
            print("Auth、Data API/RPC/RLS、Storage、Studio、LiteLLM 验收通过", flush=True)
            # 使用真实 Cookie/CSRF 进行 Langfuse 管理员登录，不记录密码或会话。
            origin = "http://127.0.0.1:" + str(ports["business"])
            cookies = http.cookiejar.CookieJar()
            browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
            admin_login = urllib.request.Request(origin + "/api/auth/login", data=json.dumps({"email": "admin@anp.local",
                "password": private["business-user-password"]}).encode(), headers={"Content-Type": "application/json"})
            with browser.open(admin_login, timeout=30) as response:
                assert response.status == 200, "平台管理员统一登录失败"
            with browser.open(origin + "/langfuse/api/auth/csrf", timeout=30) as response:
                csrf = json.load(response)["csrfToken"]
            login = urllib.parse.urlencode({"email": "admin@anp.local", "password": private["langfuse-user-password"],
                "csrfToken": csrf, "callbackUrl": origin + "/langfuse/", "json": "true"}).encode()
            with browser.open(urllib.request.Request(origin + "/langfuse/api/auth/callback/credentials", data=login,
                headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=60) as response:
                response.read()
            with browser.open(origin + "/langfuse/api/auth/session", timeout=30) as response:
                session = json.load(response)
            assert session.get("user", {}).get("email") == "admin@anp.local", "Langfuse 管理员 Cookie 登录失败"
            with browser.open(origin + "/langfuse/", timeout=30) as response:
                page = response.read()
            asset = re.search(rb'<script[^>]+src="([^"]+)"', page)
            assert asset and asset[1].startswith(b"/langfuse/_next/"), "Langfuse 根路径错误落到了其他应用"
            asset_path = asset[1].decode().replace("&amp;", "&")
            assert api(asset_path)[0] == 401, "Langfuse 静态资源缺少管理员鉴权"
            with browser.open(origin + asset_path, timeout=30) as response:
                assert response.status == 200, "Langfuse 管理员静态资源请求失败"
            receipt["checks"]["langfuse_admin_cookie_login_and_page_assets"] = True
            auth = "Basic " + base64.b64encode((private["langfuse-public-key"] + ":" + private["langfuse-secret-key"]).encode()).decode()
            trace, span = uuid.uuid4().hex, uuid.uuid4().hex[:16]
            timestamp = time.time_ns()
            values = {"langfuse.trace.name": "anp-native-acceptance", "langfuse.observation.type": "span",
                      "langfuse.observation.input": json.dumps({"scope": "synthetic local fixture"}),
                      "langfuse.observation.output": json.dumps({"verified": True})}
            payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "anp-native-acceptance"}}]},
                "scopeSpans": [{"scope": {"name": "anp-native-acceptance"}, "spans": [{"traceId": trace, "spanId": span,
                "name": "anp-native-acceptance", "kind": 1, "startTimeUnixNano": str(timestamp), "endTimeUnixNano": str(timestamp + 1000000),
                "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in values.items()]}]}]}]}
            status, body = api("/langfuse/api/public/otel/v1/traces", "POST", payload, {"Authorization": auth})
            assert status == 200 and not json.loads(body).get("partialSuccess", {}).get("rejectedSpans"), "Langfuse OTLP 写入失败"

            def trace_read():
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    status, body = api("/langfuse/api/public/v2/observations?traceId=" + trace + "&fields=basic,io", headers={"Authorization": auth})
                    if status == 200 and json.loads(body).get("data"):
                        observation = json.loads(body)["data"][0]
                        assert observation["traceId"] == trace
                        for key, expected in [("input", {"scope": "synthetic local fixture"}), ("output", {"verified": True})]:
                            value = observation[key]
                            assert (json.loads(value) if isinstance(value, str) else value) == expected
                        return
                    assert status in (200, 404, 503), "Langfuse 读回失败：" + str(status)
                    time.sleep(2)
                raise AssertionError("Langfuse worker 未在 180 秒内处理 trace")

            trace_read()
            assert api("/langfuse/api/public/v2/observations?traceId=" + trace)[0] == 401
            receipt["checks"]["langfuse_otlp_async_worker_readback_auth"] = True
            print("Langfuse OTLP 异步处理与读回通过", flush=True)
            receipt["trace_id"] = trace
            cli("stop")
            moved = folder / "第二次 搬移"
            bundle.rename(moved)
            bundle = moved
            cli("start")
            cli("status")
            assert json.loads(api(path + "?id=eq." + item, headers=owner)[1])[0]["content"] == "updated"
            assert api(obj, headers=owner) == (200, content)
            trace_read()
            assert json.loads((bundle / "var/secrets/runtime.json").read_text()) == private
            redis[0] = str(bundle / "storage/redis/redis-cli")
            assert subprocess.check_output([*redis, "GET", "anp:native:acceptance"], text=True).strip() == "persisted"
            assert subprocess.check_output([*redis, "VCARD", "anp:native:vector"], text=True).strip() == "1"
            receipt["checks"]["redis_vectors_and_restart_persistence"] = True
            receipt["checks"]["relocation_database_file_trace_secrets_persistence"] = True
            print("目录搬移、重启及数据库/文件/trace 持久化通过", flush=True)
            assert api(path + "?id=eq." + item, "DELETE", headers=owner)[0] in (200, 204)
            status, _ = api("/storage/v1/object/" + bucket, "DELETE", {"prefixes": [users[0][0] + "/probe.txt"]}, owner)
            assert status == 200
            receipt["checks"]["api_deletion"] = True
            cli("stop")
            cli("verify")
            receipt["checks"]["immutable_after_relocation_run"] = True
            receipt["passed"] = True
        finally:
            cli("stop", False)
            secret_file = bundle / "var/secrets/runtime.json"
            if secret_file.exists():
                private = json.loads(secret_file.read_text())
            for p in (bundle / "var/logs").glob("*.log"):
                text = p.read_text(errors="replace")
                for value in private.values():
                    if isinstance(value, str) and value:
                        for variant in (value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)):
                            text = text.replace(variant, "[REDACTED]")
                (evidence / p.name).write_text(text)
                if not receipt["passed"] and p.name == "litellm.log":
                    print("LiteLLM 失败诊断（已脱敏）：\n" + text[-12000:], file=sys.stderr, flush=True)
            (evidence / "acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
