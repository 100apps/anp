"""译配快捷入口；只创建本项目配置、Supabase 对象与独立桶。"""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "business/src"))
load_dotenv(ROOT / "business/.env")


def configure():
    root_env = dotenv_values(ROOT / ".env")
    existing = dotenv_values(ROOT / "business/.env")
    old = ROOT.parent / "anp"
    old_secrets = dotenv_values(old / "config/secrets.local.env")
    generated = dotenv_values(old / ".local/generated/platform-v3/business.env")
    values = {
        "ANP_DUB_DATABASE_URL": f"postgresql://supabase_admin:{root_env['POSTGRES_PASSWORD']}@127.0.0.1:{root_env['PG_PORT']}/postgres",
        "ANP_DUB_ACTOR_ID": str(uuid4()), "ANP_DUB_WORKSPACE_ID": str(uuid4()),
        "ANP_DUB_ROOT": (ROOT / "var/dubbing").as_posix(),
        "ANP_DUB_S3_ENDPOINT": f"http://127.0.0.1:{root_env['S3_PORT']}",
        "ANP_DUB_S3_ACCESS_KEY": root_env["S3_ACCESS_KEY"], "ANP_DUB_S3_SECRET_KEY": root_env["S3_SECRET_KEY"],
        "ANP_DUB_S3_BUCKET": "anp2-dubbing", "ANP_DUB_MODEL_BASE_URL": generated.get("ANP_LITELLM_URL", "http://127.0.0.1:4000/v1"),
        "ANP_DUB_MODEL_API_KEY": old_secrets.get("LITELLM_KEY", ""), "ANP_DUB_MODEL": "auto", "ANP_DUB_REVIEW_MODEL": "auto",
        "ANP_DUB_AUDIO_CONFIG": (ROOT / "var/config/dubbing/audio.json").as_posix(),
        "ANP_DUB_AUDIO_TOKEN": secrets.token_hex(32),
        "ANP_DUB_INPUT_ROOTS": (ROOT / "var/dubbing/inputs").as_posix(),
    }
    additions = {k: v for k, v in values.items() if k not in existing}
    with (ROOT / "business/.env").open("a", encoding="utf-8") as file:
        file.write("\n# 视频译配私有配置（凭据不进入前端）\n")
        for key, value in additions.items():
            file.write(f'{key}="{value}"\n')
    audio_path = Path(values["ANP_DUB_AUDIO_CONFIG"])
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    if not audio_path.exists():
        owned = json.loads((old / ".local/deep-dub-owned-tts.json").read_text(encoding="utf-8"))
        native = json.loads(Path("F:/audio.cpp/v0.9.0-cuda13.3/gpu-5-server.json").read_text(encoding="utf-8"))
        names = {"tts": "index-tts2-5", "asr": "qwen3-asr-1.7b-q8", "separation": "bs-roformer-ep368-q8"}
        audio_path.write_text(json.dumps({"transport": "owned", "executable": owned["executable"],
            "device": owned["gpu_index"], "gpu_uuid": owned["gpu_uuid"], "port": 18190,
            "coordination_lock": owned["coordination_locks"][0],
            "models": {kind: next(m for m in native["models"] if m["id"] == name) for kind, name in names.items()}},
            ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "var/dubbing/inputs").mkdir(parents=True, exist_ok=True)
    # Docker 只连接本项目PG/S3，音频计算通过带令牌的字节协议调用宿主，不传Windows路径。
    current = dotenv_values(ROOT / "business/.env")
    container = {k: v for k, v in current.items() if k.startswith("ANP_DUB_")}
    container["ANP_DUB_DATABASE_URL"] = f"postgresql://supabase_admin:{root_env['POSTGRES_PASSWORD']}@postgres:5432/postgres"
    container["ANP_DUB_S3_ENDPOINT"] = "http://rustfs:9000"
    container["ANP_DUB_ROOT"] = "/app/.langgraph_api/dubbing"
    container["ANP_DUB_INPUT_ROOTS"] = "/app/dubbing-inputs"
    container["ANP_DUB_AUDIO_CONFIG"] = "/app/dubbing-config/audio.bridge.json"
    container["ANP_DUB_MODEL_BASE_URL"] = current["ANP_DUB_MODEL_BASE_URL"].replace("127.0.0.1", "host.docker.internal")
    docker_env_path = ROOT / "var/config/services/dubbing.env"
    if not docker_env_path.exists():
        docker_env_path.write_text("".join(f'{k}="{v}"\n' for k, v in container.items()), encoding="utf-8")
    local_audio = json.loads(audio_path.read_text(encoding="utf-8"))
    bridge = {"transport": "bridge", "endpoint": "http://host.docker.internal:18191",
              "models": {name: {"id": entry["id"]} for name, entry in local_audio["models"].items()}}
    (audio_path.parent / "audio.bridge.json").write_text(json.dumps(bridge, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已准备本项目私有配置；仅复制模型网关凭据和音频软件配置，未改动旧项目。")


def migrate():
    import psycopg
    from anp_business.dubbing.media import s3
    from anp_business.dubbing.settings import required
    from psycopg.types.json import Jsonb
    with psycopg.connect(required("ANP_DUB_DATABASE_URL")) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.anp2_schema_migrations(name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        conn.execute("REVOKE ALL ON public.anp2_schema_migrations FROM PUBLIC, anon, authenticated")
        for path in sorted((ROOT / "business/supabase/migrations").glob("*.sql")):
            if not conn.execute("SELECT 1 FROM public.anp2_schema_migrations WHERE name=%s", (path.name,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.anp2_schema_migrations(name) VALUES (%s)", (path.name,))
        user = required("ANP_DUB_ACTOR_ID")
        conn.execute("INSERT INTO auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data) VALUES (%s,'authenticated','authenticated',%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                     (user, f"anp2-{user}@local.invalid", Jsonb({"provider": "local-development"}), Jsonb({})))
        conn.execute("INSERT INTO dubbing.workspaces(id,name) VALUES (%s,'本机译配工作空间') ON CONFLICT DO NOTHING", (required("ANP_DUB_WORKSPACE_ID"),))
        conn.execute("INSERT INTO dubbing.memberships(workspace_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (required("ANP_DUB_WORKSPACE_ID"), user))
    client = s3()
    bucket = os.getenv("ANP_DUB_S3_BUCKET", "anp2-dubbing")
    if bucket not in {b["Name"] for b in client.list_buckets()["Buckets"]}:
        client.create_bucket(Bucket=bucket)
    print("Supabase 对象迁移、RLS、本机开发成员与私有桶已就绪。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["config", "migrate", "run", "retry", "list", "show", "worker"])
    parser.add_argument("input", nargs="?")
    parser.add_argument("--title", default="视频译配")
    parser.add_argument("--subtitle")
    args = parser.parse_args()
    if args.command == "config":
        configure()
    elif args.command == "migrate":
        migrate()
    elif args.command == "worker":
        import uvicorn
        uvicorn.run("anp_business.dubbing.audio_worker:app", host="127.0.0.1", port=18191)
    else:
        from anp_business.dubbing import service
        if args.command == "run":
            run = service.create_run(args.input, args.title, args.subtitle)
            print("已登记运行：", run["id"], flush=True)
            service.execute(str(run["id"]))
            print(json.dumps(service.describe(str(run["id"])), ensure_ascii=False, default=str, indent=2))
        elif args.command == "retry":
            service.execute(args.input)
            print(json.dumps(service.describe(args.input), ensure_ascii=False, default=str, indent=2))
        elif args.command == "show":
            print(json.dumps(service.describe(args.input), ensure_ascii=False, default=str, indent=2))
        elif args.command == "list":
            print(json.dumps(service.list_runs(), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
