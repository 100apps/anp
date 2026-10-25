"""冻结 LiteLLM 管理台需要的 Prisma Python 客户端、CLI 与官方引擎。"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def generate(bundle, target, fetch):
    lock_path = ROOT / "native/locks" / ("litellm-prisma-" + target + ".json")
    lock = json.loads(lock_path.read_text())
    package = ROOT / "native/packages/litellm-prisma"
    node = bundle / "tools/node/bin/node"
    env = {**os.environ, "PATH": str(node.parent) + ":" + os.environ["PATH"], "PRISMA_SKIP_POSTINSTALL_GENERATE": "true"}
    subprocess.run([str(node.parent / "npm"), "ci", "--ignore-scripts", "--userconfig", "/dev/null", "--registry=https://registry.npmjs.org"],
                   cwd=package, env=env, check=True)
    lite = bundle / "platform/litellm"
    cli = lite / "cli"
    shutil.copytree(package / "node_modules", cli / "node_modules", symlinks=True)
    shutil.copy2(package / "package.json", cli / "package.json")
    shutil.copy2(package / "package-lock.json", cli / "package-lock.json")
    engines = lite / "engines"
    engines.mkdir()
    for name, item in lock["artifacts"].items():
        (engines / name).write_bytes(gzip.decompress(fetch(item).read_bytes()))
        (engines / name).chmod(0o755)
    scope_migration_metadata(bundle)
    generate_client(bundle, lock)
    adapt_prisma_engine_slot(bundle)
    (bundle / "manifest/litellm-prisma.json").write_text(json.dumps({"prisma_version": lock["prisma_version"],
        "engine_commit": lock["engine_commit"], "platform": lock["platform"], "artifacts": lock["artifacts"],
        "package_lock_sha256": hashlib.sha256((package / "package-lock.json").read_bytes()).hexdigest()}, indent=2) + "\n")


def scope_migration_metadata(bundle):
    """官方幂等检查须限定 schema，防止共享库的同名约束影响独立实例。"""
    receipts = []
    folder = bundle / "platform/litellm/site-packages/litellm_proxy_extras/migrations"
    for path in sorted(folder.glob("*/migration.sql")):
        original = path.read_text()
        updated = re.sub(r"FROM pg_constraint WHERE (?!connamespace)",
                         "FROM pg_constraint WHERE connamespace = current_schema()::regnamespace AND ", original)
        updated = re.sub(r"(FROM information_schema\.columns\s+WHERE )(?!table_schema)",
                         r"\1table_schema = current_schema() AND ", updated)
        if updated != original:
            mode = path.stat().st_mode
            # 也可用于发行目录派生的硬链接副本，绝不改动原始实例。
            path.unlink()
            path.write_text(updated)
            path.chmod(mode)
            receipts.append({"migration": path.parent.name,
                "upstream_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "patched_sha256": hashlib.sha256(updated.encode()).hexdigest()})
    (bundle / "manifest/litellm-migration-scope.json").write_text(json.dumps(receipts, indent=2) + "\n")
    return receipts


def adapt_prisma_engine_slot(bundle):
    """LiteLLM 的引擎跟踪写入口适配冻结的 Prisma Python 0.15。"""
    site = bundle / "platform/litellm/site-packages"
    path = site / "litellm/proxy/db/prisma_client.py"
    original = path.read_text()
    before = "        prisma_client._Prisma__engine = engine"
    after = "        prisma_client._internal_engine = engine"
    if original.count(before) != 1 or "'_internal_engine'," not in (site / "prisma/_base_client.py").read_text():
        raise RuntimeError("未知 LiteLLM/Prisma 引擎布局，拒绝适配")
    updated = original.replace(before, after, 1)
    path.write_text(updated)
    (bundle / "manifest/litellm-prisma-compat.json").write_text(json.dumps({
        "path": str(path.relative_to(bundle)), "prisma_version": "0.15.0",
        "reason": "引擎跟踪使用 Prisma 0.15 的实际存储槽，保留事务跟踪和重连行为",
        "upstream_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "patched_sha256": hashlib.sha256(updated.encode()).hexdigest(),
    }, ensure_ascii=False, indent=2) + "\n")


def generate_client(bundle, lock):
    """独立生成步骤，也供已离线准备 CLI/引擎的验收使用。"""
    lite = bundle / "platform/litellm"
    cli, engines = lite / "cli", lite / "engines"
    node = bundle / "tools/node/bin/node"
    env = {**os.environ, "PATH": str(node.parent) + ":" + os.environ["PATH"]}
    schema = (lite / "site-packages/litellm/proxy/schema.prisma").read_text()
    schema = re.sub(r'binaryTargets\s*=\s*\[[^\]]*\]', 'binaryTargets = ' + json.dumps([lock["platform"]]), schema)
    schema = schema.replace('provider = "prisma-client-py"', 'provider = "prisma-client-py"\n  output = ' + json.dumps(str(lite / "site-packages/prisma")))
    with tempfile.TemporaryDirectory(prefix="litellm-prisma-", dir=bundle.parent) as scratch:
        work = Path(scratch)
        (work / "schema.prisma").write_text(schema)
        launcher = work / "prisma-client-py"
        launcher.write_text("#!/bin/sh\nexec " + shlex.quote(str(bundle / "tools/python/bin/python3")) + " -I -B " + shlex.quote(str(bundle / "runtime/prisma_entry.py")) + ' "$@"\n')
        launcher.chmod(0o755)
        env.update({"PATH": str(work) + ":" + env["PATH"], "PRISMA_QUERY_ENGINE_BINARY": str(engines / "query-engine"),
                    "PRISMA_SCHEMA_ENGINE_BINARY": str(engines / "schema-engine"), "PRISMA_CLI_QUERY_ENGINE_TYPE": "binary",
                    "PRISMA_BINARY_PLATFORM": lock["platform"], "PRISMA_BINARY_CACHE_DIR": str(cli),
                    "DATABASE_URL": "postgresql://build:build@127.0.0.1:9/build", "PRISMA_HIDE_UPDATE_MESSAGE": "true"})
        subprocess.run([str(node), str(cli / "node_modules/prisma/build/index.js"), "generate", "--schema", str(work / "schema.prisma")],
                       cwd=work, env=env, check=True)
    if not (lite / "site-packages/prisma/models.py").is_file():
        raise RuntimeError("LiteLLM Prisma Python 客户端未生成")
