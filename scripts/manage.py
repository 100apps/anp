"""跨平台 Make 命令。只管理固定 anp2-next 项目。"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "anp2-next"


def read_env(path):
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "=" in line)


def run(args, cwd=ROOT):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)


def sign_jwt(role, secret):
    def encode(item):
        return base64.urlsafe_b64encode(json.dumps(item).encode()).rstrip(b"=")
    body = encode({"alg": "HS256", "typ": "JWT"}) + b"." + encode(
        {"role": role, "iss": "supabase", "iat": 0, "exp": 2147483647})
    signature = base64.urlsafe_b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).rstrip(b"=")
    return (body + b"." + signature).decode()


def init():
    values = read_env(ROOT / ".env.example")
    values.update(read_env(ROOT / "docker/versions.env"))
    for name in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "CLICKHOUSE_PASSWORD", "S3_SECRET_KEY"):
        values[name] = secrets.token_hex(20)
    # Windows NTFS 不满足 ClickHouse 的目录重命名语义，使用 Docker Linux 主机绑定目录。
    values["ANP_DATA_ROOT"] = "/var/lib/anp2-next/data" if os.name == "nt" else (ROOT / "var/data").as_posix()
    env = ROOT / ".env"
    if not env.exists():
        env.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    values = read_env(env)
    if "JWT_SECRET" not in values:
        values["JWT_SECRET"] = secrets.token_hex(32)
        with env.open("a", encoding="utf-8") as stream:
            stream.write("JWT_SECRET=" + values["JWT_SECRET"] + "\n")
    for service in ("postgres", "redis", "clickhouse", "rustfs", "dbx", "supabase"):
        (ROOT / "var/data" / service).mkdir(parents=True, exist_ok=True)
    for name in ("evidence", "run", "logs/rustfs", "logs/dev", "config/services"):
        (ROOT / "var" / name).mkdir(parents=True, exist_ok=True)
    copies = {
        "docker/storage/postgres/postgresql.conf": "var/config/postgres/conf.d/99-anp.conf",
        "docker/storage/redis/redis.conf": "var/config/redis/redis.conf",
        "docker/storage/clickhouse/config.d/anp.xml": "var/config/clickhouse/anp.xml",
        "docker/platform/litellm/config/config.yaml": "var/config/platform/litellm/config.yaml",
    }
    for source, target in copies.items():
        dest = ROOT / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(ROOT / source, dest)
    for service in ("dbx", "langfuse", "litellm", "supabase", "business", "business.production"):
        target = ROOT / f"var/config/services/{service}.env"
        source = ROOT / f"config/services/{service}.env.example"
        if not target.exists():
            content = source.read_text(encoding="utf-8") if source.exists() else "# 本组件私有配置\n"
            content = content.replace("CHANGE_ME", values["POSTGRES_PASSWORD"])
            content = content.replace("__GENERATED__", secrets.token_hex(24))
            data = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
            if service == "langfuse":
                data.update(CLICKHOUSE_PASSWORD=values["CLICKHOUSE_PASSWORD"], REDIS_AUTH=values["REDIS_PASSWORD"],
                            SALT=secrets.token_hex(24), ENCRYPTION_KEY=secrets.token_hex(32),
                            NEXTAUTH_SECRET=secrets.token_hex(32),
                            LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=values["S3_SECRET_KEY"],
                            LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=values["S3_SECRET_KEY"])
            if service == "supabase":
                secret = values["JWT_SECRET"]
                data.update(GOTRUE_JWT_SECRET=secret, PGRST_JWT_SECRET=secret, AUTH_JWT_SECRET=secret,
                            ANON_KEY=sign_jwt("anon", secret), SUPABASE_ANON_KEY=sign_jwt("anon", secret),
                            SERVICE_KEY=sign_jwt("service_role", secret), SUPABASE_SERVICE_KEY=sign_jwt("service_role", secret),
                            AWS_SECRET_ACCESS_KEY=values["S3_SECRET_KEY"],
                            POSTGREST_URL="http://supabase-rest:3000")
            if service == "business.production":
                data["REDIS_URI"] = "redis://:" + values["REDIS_PASSWORD"] + "@redis:6379/2"
            target.write_text("\n".join(f"{key}={value}" for key, value in data.items()) + "\n", encoding="utf-8")
    business_env = ROOT / "business/.env"
    if not business_env.exists():
        business_env.write_text("ANP_MODEL_MODE=demo\nLANGSMITH_TRACING=false\nBUSINESS_PORT=20240\n", encoding="utf-8")
    print("配置已准备；已有文件保持原值。项目：anp2-next；数据目录：" + values["ANP_DATA_ROOT"], flush=True)


def compose(*args):
    init()
    selected = os.environ.get("COMPOSE_PROFILES", read_env(ROOT / ".env").get("COMPOSE_PROFILES", ""))
    profiles = [item.strip() for item in selected.split(",") if item.strip()]
    profile_args = [value for profile in profiles for value in ("--profile", profile)]
    # 固定项目名，禁止通过 .env 意外接管旧 anp2。
    run(["docker", "compose", "--project-name", PROJECT, "--project-directory", ROOT,
         "--env-file", ROOT / ".env", "-f", ROOT / "compose.yaml", *profile_args, *args])


def seed_pg_config():
    """完整保留 Supabase 镜像自带配置；只复制缺失文件。"""
    init()
    if "local-pg" not in read_env(ROOT / ".env").get("COMPOSE_PROFILES", "").split(","):
        return
    dest = ROOT / "var/config/postgres"
    if (dest / "wal-g.conf").exists():
        return
    image = read_env(ROOT / ".env")["POSTGRES_IMAGE"]
    name = PROJECT + "-config-seed"
    run(["docker", "create", "--name", name, "--entrypoint", "true", image])
    try:
        with tempfile.TemporaryDirectory(prefix="anp2-config-") as temp:
            run(["docker", "cp", name + ":/etc/postgresql-custom/.", temp])
            for source in Path(temp).rglob("*"):
                target = dest / source.relative_to(temp)
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif not target.exists():
                    shutil.copyfile(source, target)
    finally:
        run(["docker", "rm", "-v", name])


def prepare():
    init()
    run(["uv", "sync", "--project", "business", "--frozen"])
    run([shutil.which("pnpm.cmd") or shutil.which("pnpm"), "install", "--frozen-lockfile"], ROOT / "business/ui")


def main():
    parser = argparse.ArgumentParser(description="ANP2 快捷命令")
    parser.add_argument("command")
    parser.add_argument("args", nargs="*")
    options = parser.parse_args()
    cmd = options.command
    if cmd == "init":
        init()
    elif cmd == "install":
        init()
        run(["uv", "sync", "--project", "business", "--frozen"])
        run([shutil.which("pnpm.cmd") or "pnpm", "install", "--frozen-lockfile"], ROOT / "business/ui")
    elif cmd == "prepare":
        prepare()
        seed_pg_config()
        if read_env(ROOT / ".env").get("COMPOSE_PROFILES"):
            compose("up", "-d", "--wait")
    elif cmd == "storage":
        seed_pg_config()
        compose("up", "-d", "--wait", "postgres", "redis", "clickhouse", "rustfs")
    elif cmd == "up":
        seed_pg_config()
        compose("--profile", "business", "up", "-d", "--wait")
    elif cmd == "down":
        compose("--profile", "business", "--profile", "dbx", "--profile", "langfuse",
                "--profile", "litellm", "--profile", "supabase", "down")
    elif cmd == "config":
        compose("config", "--quiet")
    elif cmd in ("ps", "logs"):
        compose(cmd, *options.args)
    elif cmd == "build":
        compose("--profile", "business", "build", "business")
    elif cmd == "build-production":
        compose("-f", ROOT / "docker/business/compose.production.yaml", "--profile", "business", "build", "business")
    elif cmd == "up-production":
        init()
        config = read_env(ROOT / "var/config/services/business.production.env")
        if not config.get("LANGSMITH_API_KEY") or not config.get("LANGGRAPH_CLOUD_LICENSE_KEY"):
            raise SystemExit("请先在 var/config/services/business.production.env 配置官方生产运行授权")
        compose("-f", ROOT / "docker/business/compose.production.yaml", "--profile", "business", "up", "-d", "--wait")
    elif cmd == "build-platform":
        compose("--profile", "dbx", "--profile", "langfuse", "--profile", "litellm",
                "--profile", "supabase", "build", *(options.args or []))
    elif cmd == "platform":
        compose("--profile", "dbx", "--profile", "langfuse", "--profile", "litellm",
                "--profile", "supabase", "up", "-d", "--wait")
    elif cmd == "test":
        run(["uv", "run", "--project", "business", "--frozen", "pytest", "business/tests", "tests/integration/test_compose.py", "-q"])
        run([shutil.which("pnpm.cmd") or "pnpm", "build"], ROOT / "business/ui")
    elif cmd in ("smoke", "check"):
        run(["uv", "run", "--project", "business", "--frozen", "python", "scripts/check.py",
             *(["--health"] if cmd == "check" else []), *options.args])
    elif cmd == "bootstrap":
        run(["uv", "run", "--project", "business", "--frozen", "python", "scripts/bootstrap.py"])
    elif cmd == "test-integration":
        run(["uv", "run", "--project", "business", "--frozen", "python", "scripts/check.py", "--restart"])
    elif cmd == "smoke-docker":
        run(["uv", "run", "--project", "business", "--frozen", "python", "scripts/check.py", "--api-url", "http://127.0.0.1:52024"])
    elif cmd in ("dev", "frontend", "stop"):
        init()
        if cmd == "dev":
            prepare()
            seed_pg_config()
            if read_env(ROOT / ".env").get("COMPOSE_PROFILES"):
                compose("up", "-d", "--wait")
        run(["uv", "run", "--project", "business", "--frozen", "python", ROOT / "scripts/dev.py", cmd])
    else:
        parser.error("未知命令：" + cmd)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
