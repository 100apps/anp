"""真实原生验收：独立解压目录、数据 API、鉴权、重启和搬移，无供应商调用。"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SANDBOX = ["/usr/bin/sandbox-exec", "-p", '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))(deny file-read* (subpath "/opt/homebrew") (subpath "/usr/local") (subpath "/nix"))']


def token(secret):
    def encode(obj):
        return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=")
    body = encode({"alg": "HS256", "typ": "JWT"}) + b"." + encode({"role": "anp_user", "exp": int(time.time()) + 3600})
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).rstrip(b"=")
    return (body + b"." + signature).decode()


def api(port, path, method="GET", data=None, headers=None):
    request = urllib.request.Request("http://127.0.0.1:" + str(port) + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        with HTTP.open(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    parser = argparse.ArgumentParser()
    target = "linux-amd64" if sys.platform.startswith("linux") else "macos-arm64"
    parser.add_argument("--bundle", type=Path, default=ROOT / ("var/native/build/anp-" + target))
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.archive:
        with tarfile.open(args.archive) as archive:
            metadata = json.load(archive.extractfile("anp-" + target + "/manifest/build.json"))
    else:
        metadata = json.loads((args.bundle / "manifest/build.json").read_text()) if (args.bundle / "manifest/build.json").exists() else {}
    if metadata.get("profile") == "platform":
        from platform_check import main as platform_main
        platform_main()
        return
    assert args.archive or (args.bundle / "manifest/build.json").exists(), "请先 make build"
    acceptance = ROOT / "var/native/acceptance"
    acceptance.mkdir(parents=True, exist_ok=True)
    result = {"passed": False, "target": target, "remote_model_calls": 0, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="native-check-", dir=acceptance) as scratch:
        folder = Path(scratch)
        bundle = folder / "第一次 解压"
        if args.archive:
            expected = args.archive.with_suffix(args.archive.suffix + ".sha256").read_text().split()[0]
            actual = hashlib.sha256(args.archive.read_bytes()).hexdigest()
            assert actual == expected, "归档摘要错误"
            with tarfile.open(args.archive) as archive:
                archive.extractall(folder, filter="data")
            (folder / ("anp-" + target)).rename(bundle)
            result["archive_sha256"] = actual
        else:
            assert not (args.bundle / "var").exists(), "请用未运行的构建目录验收，避免复制用户数据"
            # APFS clone 是独立 inode 的写时复制，减少验收副本占用；不使用硬链接。
            if sys.platform == "darwin":
                subprocess.run(["/bin/cp", "-cR", str(args.bundle), str(bundle)], check=True)
            else:
                shutil.copytree(args.bundle, bundle, symlinks=True)

        def cli(command, check=True):
            # 管理器需调用 macOS setuid ps；只将应用进程/初始化置于离线 sandbox。
            response = subprocess.run([str(bundle / "anp"), command], env={**os.environ, "ANP_OFFLINE": "1"}, capture_output=True, text=True, timeout=150)
            if check and response.returncode:
                print(response.stderr[-2000:])
                raise AssertionError("命令失败：" + command)
            return response

        ports = {}
        for name in ("postgres", "redis", "postgrest", "litellm"):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                ports[name] = s.getsockname()[1]
        assert len(set(ports.values())) == 4
        config = {"bind": "127.0.0.1", "ports": ports, "services": list(ports)}
        (bundle / "config/anp.json").write_text(json.dumps(config))
        # 用保留的 TEST-NET 地址检查 sandbox 确实以 EPERM 阻止包外网络。
        sandbox = SANDBOX if sys.platform == "darwin" else []
        probe = "import socket,errno; s=socket.socket(); s.settimeout(1); assert s.connect_ex(('192.0.2.1',9))==errno.EPERM" if sandbox else "import socket,errno; s=socket.socket(); s.settimeout(1); assert s.connect_ex(('192.0.2.1',9)) in (errno.ENETUNREACH,errno.EPERM)"
        offline = subprocess.run([*sandbox, str(bundle / "tools/python/bin/python3"), "-I", "-B", "-c", probe], capture_output=True)
        assert offline.returncode == 0, "外网阻断探针未通过"
        result["checks"]["external_network_denied"] = True
        if sandbox:
            result["checks"]["host_package_manager_paths_denied"] = True
        else:
            assert os.getenv("ANP_CLEAN_RUNTIME") == "1", "Linux 验收需要在干净 Docker 环境中运行"
            assert all(shutil.which(name) is None for name in ("python3", "python", "node", "uv", "gcc", "patchelf")), "运行容器含宿主开发工具"
            result["checks"]["clean_runtime_without_developer_tools"] = True
        for command in ([bundle / "tools/uv/uv", "--version"],
                        [bundle / "tools/python/bin/python3", "--version"],
                        [bundle / "tools/node/bin/node", "-e", "if(process.arch!==" + repr("arm64" if target == "macos-arm64" else "x64") + ")process.exit(1);let r=require('child_process').spawnSync(process.execPath,['--version']);if(r.status!==0)process.exit(2)"],
                        [bundle / "tools/python/bin/python3", "-I", "-c", "import subprocess,sys;subprocess.run([sys.executable,'-I','-c','print(42)'],check=True)"]):
            subprocess.run([*sandbox, *map(str, command)], check=True, capture_output=True,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(bundle)})
        result["checks"]["bundled_tools"] = True
        cli("verify")
        result["checks"]["manifest"] = True
        # 外来端口必须在数据初始化前被拒绝；不能停止或接管监听者。
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", ports["redis"]))
            foreign.listen()
            assert cli("start", check=False).returncode != 0
            assert not (bundle / "var/secrets/runtime.json").exists()
        result["checks"]["foreign_listener_rejected"] = True
        try:
            cli("start")
            cli("status")
            result["checks"]["fresh_start"] = True
            private = json.loads((bundle / "var/secrets/runtime.json").read_text())
            auth = {"Authorization": "Bearer " + token(private["jwt"]), "Prefer": "return=representation"}
            endpoint = ports["postgrest"]
            assert api(endpoint, "/items", "POST", {"content": "anonymous denied"})[0] in (401, 403)
            status, body = api(endpoint, "/items", "POST", {"content": "native-poc"}, auth)
            assert status == 201
            item = json.loads(body)[0]["id"]
            status, body = api(endpoint, "/items?id=eq." + str(item), "PATCH", {"content": "updated"}, auth)
            assert status == 200 and json.loads(body)[0]["content"] == "updated"
            assert api(endpoint, "/items", headers={"Authorization": "Bearer invalid"})[0] == 401
            result["checks"]["data_api_crud_and_roles"] = True
            redis = [str(bundle / "storage/redis/redis-cli"), "-p", str(ports["redis"])]
            subprocess.run([*redis, "SET", "anp:poc:acceptance", "persisted"], check=True, capture_output=True)
            vector = subprocess.check_output([*redis, "VADD", "anp:poc:vector", "VALUES", "2", "1", "2", "point"], text=True).strip()
            assert vector == "1"
            result["checks"]["redis_vectorset"] = True
            assert api(ports["litellm"], "/v1/models")[0] in (401, 403)
            status, body = api(ports["litellm"], "/v1/models", headers={"Authorization": "Bearer " + private["litellm"]})
            assert status == 200 and isinstance(json.loads(body)["data"], list)
            result["checks"]["litellm_real_auth"] = True
            cli("stop")
            # 移动的是同一套程序、配置和数据。路径含空格和中文。
            moved = folder / "第二次 搬移"
            bundle.rename(moved)
            bundle = moved
            cli("start")
            cli("status")
            status, body = api(endpoint, "/items?id=eq." + str(item))
            assert status == 200 and json.loads(body)[0]["content"] == "updated"
            redis[0] = str(bundle / "storage/redis/redis-cli")
            value = subprocess.check_output([*redis, "GET", "anp:poc:acceptance"], text=True).strip()
            assert value == "persisted"
            assert json.loads((bundle / "var/secrets/runtime.json").read_text()) == private
            result["checks"]["relocation_and_persistence"] = True
            assert api(endpoint, "/items?id=eq." + str(item), "DELETE", headers=auth)[0] in (200, 204)
            result["checks"]["delete"] = True
            cli("stop")
            cli("verify")
            result["checks"]["immutable_files_after_run"] = True
            result["passed"] = True
        finally:
            cli("stop", check=False)
            # 仅保留本任务日志；生成的秘密值不写进回执或日志归档。
            secret_file = bundle / "var/secrets/runtime.json"
            values = list(json.loads(secret_file.read_text()).values()) if secret_file.exists() else []
            logs = ROOT / "var/native/evidence/logs"
            logs.mkdir(parents=True, exist_ok=True)
            for path in (bundle / "var/logs").glob("*.log"):
                content = path.read_text(errors="replace")
                for value in values:
                    content = content.replace(value, "[REDACTED]")
                (logs / path.name).write_text(content)
            evidence = ROOT / "var/native/evidence/acceptance.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
