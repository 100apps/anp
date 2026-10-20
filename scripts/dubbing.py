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
    # 本地业务配置由统一入口生成；供应商和音频服务通过 business/.env 显式设置。
    from manage import init
    init()
    print("本机业务配置已准备；真实模型需设置 ANP_DUB_MODEL_* 与音频配置。")


def migrate():
    import psycopg
    from anp_business.dubbing.media import s3
    from anp_business.dubbing.settings import required
    from psycopg.types.json import Jsonb
    with psycopg.connect(required("ANP_DUB_DATABASE_URL")) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.anp_schema_migrations(name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        conn.execute("REVOKE ALL ON public.anp_schema_migrations FROM PUBLIC, anon, authenticated")
        for path in sorted((ROOT / "business/supabase/migrations").glob("*.sql")):
            if not conn.execute("SELECT 1 FROM public.anp_schema_migrations WHERE name=%s", (path.name,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.anp_schema_migrations(name) VALUES (%s)", (path.name,))
        user = required("ANP_DUB_ACTOR_ID")
        conn.execute("INSERT INTO auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data) VALUES (%s,'authenticated','authenticated',%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                     (user, f"anp-{user}@local.invalid", Jsonb({"provider": "local-development"}), Jsonb({})))
        conn.execute("INSERT INTO dubbing.workspaces(id,name) VALUES (%s,'本机译配工作空间') ON CONFLICT DO NOTHING", (required("ANP_DUB_WORKSPACE_ID"),))
        conn.execute("INSERT INTO dubbing.memberships(workspace_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (required("ANP_DUB_WORKSPACE_ID"), user))
    client = s3()
    bucket = os.getenv("ANP_DUB_S3_BUCKET", "anp-dubbing")
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
