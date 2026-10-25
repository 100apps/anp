"""从固定官方发行包组装 LGTM 和 Mac Redis，不编译上游项目。"""
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def install(bundle, target, fetch, copy_tree):
    from platform_build import unpack, artifact_binary
    lock_path = ROOT / "native/locks" / ("otel-" + target + ".json")
    lock = json.loads(lock_path.read_text())
    excluded_helpers = []
    for name, item in lock["artifacts"].items():
        print("观测官方产物：" + name, flush=True)
        destination = bundle / "platform/otel" / name
        with tempfile.TemporaryDirectory(prefix="otel-extract-", dir=bundle.parent) as temporary:
            folder = Path(temporary)
            unpack(fetch(item), folder)
            if item.get("binary"):
                destination.mkdir(parents=True)
                binary = artifact_binary(folder, item["binary"])
                installed = destination / item.get("installed_name", item["binary"])
                shutil.copy2(binary, installed)
                installed.chmod(0o755)
                for path in folder.rglob("*"):
                    if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                        output = destination / "licenses" / path.relative_to(folder)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, output)
            else:
                children = list(folder.iterdir())
                payload = children[0] if len(children) == 1 and children[0].is_dir() else folder
                copy_tree(payload, destination)
            if name == "prometheus":
                if not (destination / "prometheus").is_file():
                    raise RuntimeError("官方 Prometheus 发行包缺少服务二进制")
                helper = destination / "promtool"
                if helper.exists():
                    excluded_helpers.append({"path": str(helper.relative_to(bundle)),
                                             "reason": "附带诊断 CLI；不参与 ANP 服务启动与运行"})
                    helper.unlink()
    copy_tree(ROOT / "native/defaults/otel", bundle / "defaults/otel")
    licenses = bundle / "manifest/licenses/otel"
    licenses.mkdir(parents=True, exist_ok=True)
    for name, item in lock.get("licenses", {}).items():
        import hashlib
        path = ROOT / "native/licenses" / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError("观测组件许可证摘要错误：" + name)
        shutil.copy2(path, licenses / name)
    shutil.copy2(lock_path, bundle / "manifest/otel-sources.lock.json")
    (bundle / "manifest/otel-runtime.json").write_text(json.dumps({"excluded_helpers": excluded_helpers},
        ensure_ascii=False, indent=2) + "\n")


def install_redis(bundle, archive):
    destination = bundle / "storage/redis"
    destination.mkdir()
    libraries = destination / "lib"
    libraries.mkdir()
    for name in ("libssl.3.dylib", "libcrypto.3.dylib"):
        shutil.copy2(bundle / "storage/postgres/lib" / name, libraries / name)
    binaries = destination / "bin"
    binaries.mkdir()
    signed_binaries, launchers = {}, {}
    with zipfile.ZipFile(archive) as zipped:
        for name in ("redis-server", "redis-cli"):
            path = binaries / name
            path.write_bytes(zipped.read("bin/" + name))
            path.chmod(0o755)
            signed_binaries[str(path.relative_to(bundle))] = hashlib.sha256(path.read_bytes()).hexdigest()
            # Redis 官方签名允许 DYLD_LIBRARY_PATH；只在此进程中绑定包内库。
            # 改写 Mach-O 会破坏 Redis Labs 的 Developer ID，不能重新改为 adhoc。
            launcher = destination / name
            launcher.write_text('#!/bin/sh\nset -eu\nANP_REDIS_HOME=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                                'DYLD_LIBRARY_PATH="$ANP_REDIS_HOME/lib" exec "$ANP_REDIS_HOME/bin/' + name + '" "$@"\n')
            launcher.chmod(0o755)
            launchers[str(launcher.relative_to(bundle))] = hashlib.sha256(launcher.read_bytes()).hexdigest()
    (bundle / "manifest/redis-distribution.json").write_text(json.dumps({
        "distribution": "official-binary", "version": "8.10.2", "modules_enabled": False,
        "adaptations": ["unchanged Redis Labs signed binaries", "process-scoped bundled OpenSSL lookup"],
        "signed_binaries": signed_binaries, "launchers": launchers,
    }, indent=2) + "\n")
