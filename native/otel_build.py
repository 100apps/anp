"""从固定官方发行包组装 LGTM 和 Mac Redis，不编译上游项目。"""
import json
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


def install_redis(bundle, archive):
    destination = bundle / "storage/redis"
    destination.mkdir()
    libraries = destination / "lib"
    libraries.mkdir()
    for name in ("libssl.3.dylib", "libcrypto.3.dylib"):
        shutil.copy2(bundle / "storage/postgres/lib" / name, libraries / name)
    subprocess.run(["install_name_tool", "-change", "@rpath/libcrypto.3.dylib",
                    "@loader_path/libcrypto.3.dylib", str(libraries / "libssl.3.dylib")], check=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in ("redis-server", "redis-cli"):
            path = destination / name
            path.write_bytes(zipped.read("bin/" + name))
            path.chmod(0o755)
            for library in ("libssl.3.dylib", "libcrypto.3.dylib"):
                subprocess.run(["install_name_tool", "-change", "/opt/homebrew/opt/openssl@3/lib/" + library,
                                "@executable_path/lib/" + library, str(path)], check=True)
    (bundle / "manifest/redis-distribution.json").write_text(json.dumps({
        "distribution": "official-binary", "version": "8.10.2", "modules_enabled": False,
        "adaptations": ["OpenSSL -> bundled official PostgreSQL libraries", "final Mach-O signing"],
    }, indent=2) + "\n")
