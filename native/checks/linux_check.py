"""在独立的 Debian / Alpine 无开发工具、无外网容器中验收原生包。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "var/native"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=BASE / "build/anp-linux-amd64")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    assert (bundle / "manifest/build.json").exists(), "请先构建 Linux 包"
    images = json.loads((ROOT / "native/locks/linux-runtime-images.json").read_text())
    registry = os.getenv("ANP_RUNTIME_REGISTRY", "docker.io/library").rstrip("/")
    evidence = BASE / "evidence/linux"
    evidence.mkdir(parents=True, exist_ok=True)
    uid = os.getuid() or 1000
    gid = os.getgid() or 1000
    for name, image in images.items():
        with tempfile.TemporaryDirectory(prefix="clean-" + name + "-", dir=BASE) as scratch:
            work = Path(scratch)
            bootstrap = work / "bootstrap"
            for part in ("tools/python", "tools/libc", "tools/elf"):
                shutil.copytree(bundle / part, bootstrap / part, symlinks=True)
            (bootstrap / "runtime").mkdir()
            shutil.copy2(ROOT / "native/runtime/elf_exec.py", bootstrap / "runtime/elf_exec.py")
            # PostgreSQL 要求当前 UID 能通过 getpwuid 查到名称；不挂载宿主账号文件。
            (work / "passwd").write_text(f"root:x:0:0:root:/root:/bin/sh\nanp:x:{uid}:{gid}:ANP:/acceptance/var/home:/bin/sh\n")
            (work / "group").write_text(f"root:x:0:\nanp:x:{gid}:\n")
            if os.getuid() == 0:
                for path in [work, *work.rglob("*")]:
                    os.chown(path, uid, gid, follow_symlinks=False)
            reference = registry + "/" + image
            container = "anp-native-accept-" + name + "-" + str(os.getpid())
            command = ["docker", "run", "--rm", "--name", container, "--network=none", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", "--user", f"{uid}:{gid}", "-e", "ANP_CLEAN_RUNTIME=1",
                       "-v", str(work) + ":/acceptance/var", "-v", str(ROOT / "native") + ":/acceptance/native:ro",
                       "-v", str(work / "passwd") + ":/etc/passwd:ro", "-v", str(work / "group") + ":/etc/group:ro",
                       "-v", str(bundle) + ":/input/anp-linux-amd64:ro"]
            if args.archive:
                archive = args.archive.resolve()
                command += ["-v", str(archive.parent) + ":/archives:ro"]
            command += [reference, "/acceptance/var/bootstrap/tools/python/bin/python3", "-I", "-B",
                        "/acceptance/native/check.py", "--bundle", "/input/anp-linux-amd64"]
            if args.archive:
                command += ["--archive", "/archives/" + args.archive.name]
            print("干净环境验收：" + name, flush=True)
            try:
                subprocess.run(command, check=True)
                paths = [work / "native/evidence/acceptance.json", work / "native/evidence/platform/acceptance.json"]
                receipt = next(path for path in paths if path.exists())
                value = json.loads(receipt.read_text())
                assert value["passed"], "Linux 验收未通过"
                value["runtime_image"] = image
                value["docker_network"] = "none"
                value["docker_user"] = f"{uid}:{gid}"
                value["host_platform_and_business_sources_mounted"] = False
                (evidence / (name + ("-archive" if args.archive else "-directory") + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            finally:
                diagnostics = work / "native/evidence"
                if diagnostics.exists():
                    shutil.copytree(diagnostics, evidence / (name + "-last"), dirs_exist_ok=True)
                business_receipt = work / "native/acceptance/business-last.json"
                if business_receipt.exists():
                    destination = evidence / (name + "-last")
                    destination.mkdir(exist_ok=True)
                    shutil.copy2(business_receipt, destination / "business.json")
                subprocess.run(["docker", "stop", "--time", "10", container], capture_output=True)


if __name__ == "__main__":
    main()
