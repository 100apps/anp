"""Mac 原生平台的依赖、隔离身份、配置与启动命令。"""
import base64
import hashlib
import hmac
import json
import secrets
import sys
from urllib.parse import quote

ORDER = ["postgres", "redis", "clickhouse", "rustfs", "auth", "postgrest", "storage-api", "meta", "studio", "langfuse-web", "langfuse-worker", "litellm", "dbx", "business", "gateway"]
DEPS = {"auth": ["postgres"], "postgrest": ["postgres"], "meta": ["postgres"],
        "storage-api": ["auth", "postgrest", "rustfs"], "studio": ["meta", "storage-api"],
        "langfuse-web": ["postgres", "redis", "clickhouse", "rustfs"],
        "langfuse-worker": ["langfuse-web"], "business": ["auth", "postgrest", "storage-api"],
        "gateway": ["auth", "postgrest", "storage-api", "studio", "langfuse-worker", "litellm", "dbx", "business"]}
EXTRA_PORTS = {"clickhouse-native": "clickhouse", "meta-admin": "meta"}
HEALTH = {"auth": "/health", "postgrest": "/", "storage-api": "/status", "meta": "/health",
          "studio": "/api/platform/profile", "langfuse-web": "/langfuse/api/public/health", "langfuse-worker": "/api/health",
          "gateway": "/auth/v1/health", "clickhouse": "/ping", "rustfs": "/health", "dbx": "/dbx/", "business": "/api/health"}


def jwt(role, key):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=")
    body = enc({"alg": "HS256", "typ": "JWT"}) + b"." + enc({"role": role, "iss": "supabase", "iat": 0, "exp": 2147483647})
    return (body + b"." + base64.urlsafe_b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).rstrip(b"=")).decode()


def extend_secrets(value):
    for key in ("auth-db", "storage-db", "langfuse-db", "clickhouse", "s3", "langfuse-salt", "langfuse-auth", "meta-crypto"):
        value.setdefault(key, secrets.token_hex(32))
    value.setdefault("langfuse-encryption", secrets.token_hex(32))
    value.setdefault("s3-access", "anp-local")
    value.setdefault("langfuse-public-key", "pk-lf-" + secrets.token_hex(16))
    value.setdefault("langfuse-secret-key", "sk-lf-" + secrets.token_hex(32))
    value.setdefault("langfuse-user-password", secrets.token_urlsafe(32))
    value.setdefault("business-user-password", secrets.token_urlsafe(32))
    value.setdefault("dbx-password", secrets.token_urlsafe(32))
    value.setdefault("anon-key", jwt("anon", value["jwt"]))
    value.setdefault("service-key", jwt("service_role", value["jwt"]))
    return value


def initialize(c, config, secret):
    marker = c.VAR / "data/platform-schema-v3"
    if marker.exists():
        return
    # 本包自己的 PostgreSQL；不接触宿主已有 Supabase。
    c.initialize_api(config, secret)
    # 官方 Auth 迁移会向 postgres 授权；本包数据库管理员仍为 anp。
    c.sql(config, secret, "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='postgres') THEN CREATE ROLE postgres NOLOGIN; END IF; END $$;")
    c.sql(config, secret, "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anp_studio_reader') THEN CREATE ROLE anp_studio_reader LOGIN PASSWORD '" + secret["postgres"] + "'; END IF; END $$; GRANT pg_read_all_data, pg_read_all_stats TO anp_studio_reader; ALTER ROLE anp_studio_reader SET default_transaction_read_only=on;")
    if not c.sql(config, secret, "SELECT 1 FROM pg_roles WHERE rolname='langfuse';").strip():
        c.sql(config, secret, """
BEGIN;
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
GRANT anon, authenticated, service_role TO anp_rest;
CREATE ROLE supabase_auth_admin NOINHERIT CREATEROLE LOGIN PASSWORD '""" + secret["auth-db"] + """';
CREATE ROLE supabase_storage_admin NOINHERIT LOGIN PASSWORD '""" + secret["storage-db"] + """';
GRANT anon, authenticated, service_role TO supabase_storage_admin;
CREATE ROLE langfuse LOGIN PASSWORD '""" + secret["langfuse-db"] + """';
CREATE SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
CREATE SCHEMA auth AUTHORIZATION supabase_auth_admin;
CREATE SCHEMA storage AUTHORIZATION supabase_storage_admin;
GRANT CREATE ON DATABASE postgres TO supabase_auth_admin, supabase_storage_admin;
GRANT USAGE ON SCHEMA auth, storage, public, extensions TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public, extensions TO supabase_auth_admin, supabase_storage_admin;
ALTER ROLE supabase_auth_admin SET search_path = auth, public, extensions;
ALTER ROLE supabase_storage_admin SET search_path = storage, public, extensions;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), '')::uuid,
  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid) $$;
CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('request.jwt.claims', true),'')::jsonb $$;
CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$ SELECT coalesce(nullif(current_setting('request.jwt.claim.role',true),''),
  nullif(current_setting('request.jwt.claims',true),'')::jsonb ->> 'role') $$;
ALTER FUNCTION auth.uid() OWNER TO supabase_auth_admin;
ALTER FUNCTION auth.jwt() OWNER TO supabase_auth_admin;
ALTER FUNCTION auth.role() OWNER TO supabase_auth_admin;
COMMIT;
""")
    if not c.sql(config, secret, "SELECT 1 FROM pg_database WHERE datname='langfuse';").strip():
        c.sql(config, secret, "CREATE DATABASE langfuse OWNER langfuse;")
    c.write(marker, "1\n")


def command(c, name, config, secret):
    root, var = c.ROOT, c.VAR
    port = config["ports"][name]
    ports = config["ports"]
    environment = c.env()
    environment.update({"NODE_ENV": "production", "HOSTNAME": "127.0.0.1", "PORT": str(port),
                        "NODE_OPTIONS": "--max-old-space-size=768", "NEXT_TELEMETRY_DISABLED": "1"})
    node = root / "tools/node/bin/node"
    url = lambda service: "http://127.0.0.1:" + str(ports[service])
    def db(user, key, database="postgres"):
        return "postgresql://" + user + ":" + quote(secret[key], safe="") + "@127.0.0.1:" + str(ports["postgres"]) + "/" + database
    if name in ("auth", "postgrest", "storage-api", "meta", "studio", "langfuse-web"):
        initialize(c, config, secret)
    if name == "clickhouse":
        environment["CLICKHOUSE_WATCHDOG_ENABLE"] = "0"
        data = var / "data/clickhouse"
        data.mkdir(parents=True, exist_ok=True)
        conf = var / "run/clickhouse.xml"
        from xml.sax.saxutils import escape
        c.write(conf, f'''<clickhouse><logger><level>warning</level><console>1</console></logger>
<listen_host>127.0.0.1</listen_host><http_port>{port}</http_port><tcp_port>{ports['clickhouse-native']}</tcp_port>
<interserver_http_host>127.0.0.1</interserver_http_host><disable_internal_dns_cache>1</disable_internal_dns_cache>
<path>{escape(str(data))}/</path><tmp_path>{escape(str(data))}/tmp/</tmp_path>
<user_files_path>{escape(str(data))}/user_files/</user_files_path><format_schema_path>{escape(str(data))}/format_schemas/</format_schema_path>
<access_control_path>{escape(str(data))}/access/</access_control_path>
<max_server_memory_usage>4294967296</max_server_memory_usage><max_connections>128</max_connections>
<background_pool_size>16</background_pool_size><background_schedule_pool_size>4</background_schedule_pool_size>
<mark_cache_size>33554432</mark_cache_size><uncompressed_cache_size>16777216</uncompressed_cache_size>
<profiles><default><max_threads>2</max_threads><max_memory_usage>536870912</max_memory_usage></default></profiles>
<users><default><password>{secret['clickhouse']}</password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
<quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
</clickhouse>''')
        return [root / "storage/clickhouse/clickhouse", "server", "--config-file=" + str(conf)], environment
    if name == "rustfs":
        data = var / "data/rustfs"
        data.mkdir(parents=True, exist_ok=True)
        environment.update({"RUSTFS_ACCESS_KEY": secret["s3-access"], "RUSTFS_SECRET_KEY": secret["s3"],
            "RUSTFS_ADDRESS": "127.0.0.1:" + str(port), "RUSTFS_CONSOLE_ENABLE": "false", "RUSTFS_LOGS_DIR": str(var / "logs/rustfs"), "RUST_LOG": "warn"})
        # 官方 CLI 会拆分带空格的绝对 volume 参数；用数据目录作 cwd，传相对路径。
        return [root / "storage/rustfs/rustfs", "."], environment
    if name == "auth":
        environment.update({"GOTRUE_API_HOST": "127.0.0.1", "GOTRUE_API_PORT": str(port), "API_EXTERNAL_URL": url("gateway") + "/auth/v1",
            "GOTRUE_SITE_URL": url("gateway"), "GOTRUE_DB_DRIVER": "postgres", "GOTRUE_DB_DATABASE_URL": db("supabase_auth_admin", "auth-db"),
            "GOTRUE_JWT_SECRET": secret["jwt"], "GOTRUE_JWT_AUD": "authenticated", "GOTRUE_JWT_ADMIN_ROLES": "service_role",
            "GOTRUE_JWT_DEFAULT_GROUP_NAME": "authenticated", "GOTRUE_EXTERNAL_EMAIL_ENABLED": "true", "GOTRUE_MAILER_AUTOCONFIRM": "true",
            "GOTRUE_DB_MIGRATIONS_PATH": str(root / "platform/auth/migrations"), "GOMAXPROCS": "2"})
        return [root / "platform/auth/auth"], environment
    if name == "postgrest":
        environment.update({"DYLD_LIBRARY_PATH": str(root / "storage/postgres/lib"), "PGRST_DB_URI": db("anp_rest", "rest"),
            "PGRST_DB_SCHEMAS": "public,anp_poc", "PGRST_DB_EXTRA_SEARCH_PATH": "public,extensions", "PGRST_DB_ANON_ROLE": "anon",
            "PGRST_JWT_SECRET": secret["jwt"], "PGRST_SERVER_HOST": "127.0.0.1", "PGRST_SERVER_PORT": str(port), "PGRST_DB_POOL": "4"})
        return [root / "platform/postgrest/postgrest"], environment
    if name == "storage-api":
        c.execute([root / "tools/python/bin/python3", "-I", "-B", root / "runtime/s3_init.py"], input=json.dumps({"endpoint": url("rustfs"), "access": secret["s3-access"], "secret": secret["s3"]}))
        environment.update({"SERVER_PORT": str(port), "HOST": "127.0.0.1", "ANON_KEY": secret["anon-key"], "SERVICE_KEY": secret["service-key"],
            "AUTH_JWT_SECRET": secret["jwt"], "POSTGREST_URL": url("postgrest"), "DATABASE_URL": db("supabase_storage_admin", "storage-db"),
            "STORAGE_BACKEND": "s3", "GLOBAL_S3_BUCKET": "supabase", "GLOBAL_S3_ENDPOINT": url("rustfs"), "GLOBAL_S3_PROTOCOL": "http",
            "GLOBAL_S3_FORCE_PATH_STYLE": "true", "AWS_ACCESS_KEY_ID": secret["s3-access"], "AWS_SECRET_ACCESS_KEY": secret["s3"],
            "REGION": "us-east-1", "TENANT_ID": "anp", "FILE_SIZE_LIMIT": "52428800", "ENABLE_IMAGE_TRANSFORMATION": "false",
            "STORAGE_PUBLIC_URL": url("gateway"), "REQUEST_ALLOW_X_FORWARDED_PATH": "true", "DATABASE_CONNECTION_POOL_SIZE": "3"})
        return [root / "platform/storage-api/bin/storage"], environment
    if name == "meta":
        if sys.platform.startswith("linux"):
            # 宿主可能是 musl，但实际 Node 使用包内 glibc，不能按 /usr/bin/ldd 选插件。
            abi = c.execute([root / "platform/meta/node/bin/node", "-p", "process.versions.modules"]).stdout.strip()
            if not abi.isdecimal():
                raise RuntimeError("无法识别 Meta Node ABI")
            profiler = root / "platform/meta/app/node_modules/@sentry-internal/node-cpu-profiler/lib" / ("sentry_cpu_profiler-linux-x64-glibc-" + abi + ".node")
            if not profiler.is_file():
                raise RuntimeError("缺少匹配 Meta Node ABI 的 glibc 插件")
            environment["SENTRY_PROFILER_BINARY_PATH"] = str(profiler)
        environment.update({"PG_META_DB_HOST": "127.0.0.1", "PG_META_DB_PORT": str(ports["postgres"]), "PG_META_DB_NAME": "postgres",
            "PG_META_DB_USER": "anp", "PG_META_DB_PASSWORD": secret["postgres"], "CRYPTO_KEY": secret["meta-crypto"],
            "PG_META_PORT": str(port), "PG_META_ADMIN_PORT": str(ports["meta-admin"]), "PG_META_HOST": "127.0.0.1"})
        return [root / "platform/meta/bin/pgmeta"], environment
    if name == "studio":
        environment.update({"STUDIO_PG_META_URL": url("meta"), "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": str(ports["postgres"]),
            "POSTGRES_DB": "postgres", "POSTGRES_USER": "anp", "POSTGRES_PASSWORD": secret["postgres"],
            "POSTGRES_USER_READ_WRITE": "anp", "POSTGRES_USER_READ_ONLY": "anp_studio_reader",
            "PG_META_CRYPTO_KEY": secret["meta-crypto"], "SUPABASE_URL": url("gateway"), "SUPABASE_PUBLIC_URL": url("gateway"),
            "SUPABASE_ANON_KEY": secret["anon-key"], "SUPABASE_SERVICE_KEY": secret["service-key"], "AUTH_JWT_SECRET": secret["jwt"],
            "DEFAULT_PROJECT_NAME": "ANP", "DEFAULT_ORGANIZATION_NAME": "ANP", "ENABLED_FEATURES_LOGS_ALL": "false"})
        return [root / "platform/studio/bin/studio"], environment
    if name.startswith("langfuse-"):
        # Next 的完整服务加载超过 768 MiB；上限不代表预分配。
        environment["NODE_OPTIONS"] = "--max-old-space-size=2048"
        environment.update({"DATABASE_URL": db("langfuse", "langfuse-db", "langfuse") + "?connection_limit=4&pool_timeout=30",
            "DIRECT_URL": db("langfuse", "langfuse-db", "langfuse"), "SALT": secret["langfuse-salt"], "ENCRYPTION_KEY": secret["langfuse-encryption"],
            "NEXTAUTH_SECRET": secret["langfuse-auth"], "NEXTAUTH_URL": url("gateway") + "/langfuse/api/auth", "NEXT_PUBLIC_BASE_PATH": "/langfuse",
            "TELEMETRY_ENABLED": "false", "LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES": "false", "CLICKHOUSE_URL": url("clickhouse"),
            "LANGFUSE_INIT_ORG_ID": "anp-local", "LANGFUSE_INIT_ORG_NAME": "ANP", "LANGFUSE_INIT_PROJECT_ID": "anp-local",
            "LANGFUSE_INIT_PROJECT_NAME": "ANP", "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": secret["langfuse-public-key"],
            "LANGFUSE_INIT_PROJECT_SECRET_KEY": secret["langfuse-secret-key"], "LANGFUSE_INIT_USER_EMAIL": "admin@anp.local",
            "LANGFUSE_INIT_USER_NAME": "ANP", "LANGFUSE_INIT_USER_PASSWORD": secret["langfuse-user-password"],
            "CLICKHOUSE_MIGRATION_URL": "clickhouse://127.0.0.1:" + str(ports["clickhouse-native"]), "CLICKHOUSE_USER": "default",
            "CLICKHOUSE_PASSWORD": secret["clickhouse"], "CLICKHOUSE_CLUSTER_ENABLED": "false", "REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(ports["redis"]),
            "PATH": str(root / "tools/node/bin") + ":" + str(root / "platform/migrate") + ":" + str(root / "platform/langfuse/prisma/node_modules/.bin") + ":/usr/bin:/bin"})
        if sys.platform.startswith("linux"):
            engines = root / "platform/langfuse/prisma/node_modules/@prisma/engines"
            query = engines / "libquery_engine-debian-openssl-3.0.x.so.node"
            schema = engines / "schema-engine-debian-openssl-3.0.x"
            if not query.is_file() or not schema.is_file():
                raise RuntimeError("缺少包内 glibc Prisma 引擎")
            environment.update({"PRISMA_QUERY_ENGINE_LIBRARY": str(query), "PRISMA_SCHEMA_ENGINE_BINARY": str(schema)})
        for kind in ("EVENT", "MEDIA"):
            prefix = "LANGFUSE_S3_" + kind + "_UPLOAD_"
            environment.update({prefix + "BUCKET": "langfuse", prefix + "REGION": "us-east-1", prefix + "ENDPOINT": url("rustfs"),
                prefix + "FORCE_PATH_STYLE": "true", prefix + "ACCESS_KEY_ID": secret["s3-access"], prefix + "SECRET_ACCESS_KEY": secret["s3"]})
        c.execute([root / "tools/python/bin/python3", "-I", "-B", root / "runtime/s3_init.py"], input=json.dumps({"endpoint": url("rustfs"), "access": secret["s3-access"], "secret": secret["s3"]}))
        if name == "langfuse-web":
            return ["/bin/sh", root / "platform/langfuse/web/web/entrypoint.sh", node, root / "platform/langfuse/web/web/server.js", "--keepAliveTimeout", "110000"], environment
        return [node, root / "platform/langfuse/worker/dist/index.js"], environment
    if name == "gateway":
        conf = var / "run/Caddyfile"
        c.write(conf, f'''{{
    admin off
    auto_https off
}}
http://127.0.0.1:{port} {{
    bind 127.0.0.1
    @langfuse path /langfuse /langfuse/*
    handle /litellm/v1/* {{
        uri strip_prefix /litellm
        reverse_proxy 127.0.0.1:{ports['litellm']}
    }}
    @business path /workbench /workbench/* /api/auth/* /api/health /api/platforms /api/workbench /threads /threads/* /runs /runs/* /assistants /assistants/* /store/* /docs /openapi.json /dbx /dbx/* /supabase /supabase/* /litellm /litellm/*
    handle @business {{
        reverse_proxy 127.0.0.1:{ports['business']}
    }}
    handle_path /auth/v1/* {{
        reverse_proxy 127.0.0.1:{ports['auth']}
    }}
    handle_path /rest/v1/* {{
        reverse_proxy 127.0.0.1:{ports['postgrest']}
    }}
    handle_path /storage/v1/* {{
        reverse_proxy 127.0.0.1:{ports['storage-api']}
    }}
    handle @langfuse {{
        reverse_proxy 127.0.0.1:{ports['langfuse-web']}
    }}
    handle /realtime/* {{
        respond "Realtime 未包含在此版本" 404
    }}
    handle {{
        reverse_proxy 127.0.0.1:{ports['studio']}
    }}
}}
''')
        return [root / "platform/gateway/caddy", "run", "--config", conf, "--adapter", "caddyfile"], environment
    if name == "dbx":
        environment.update({"DBX_BIND_ADDR": "127.0.0.1", "DBX_PORT": str(port),
            "DBX_DATA_DIR": str(var / "data/dbx"), "DBX_PASSWORD": secret["dbx-password"], "DBX_PUBLIC_BASE_PATH": "/dbx"})
        return [root / "platform/dbx/dbx-web"], environment
    if name == "business":
        # 网关尚未启动时，初始化直接调用本包 Auth；不读取其他项目配置。
        from business_init import ensure_admin
        ensure_admin(url("auth"), secret, var)
        business_config = var / "run/config/business.json"
        c.write(business_config, json.dumps({"server": {"host": "127.0.0.1", "port": port},
            "supabase": {"url": url("gateway"), "anon_key": secret["anon-key"]},
            "platforms": {"dbx": url("dbx") + "/dbx/", "langfuse": url("langfuse-web") + "/langfuse/",
                          "litellm": url("litellm") + "/", "supabase": url("studio") + "/"}}))
        environment.update({"ANP_BUSINESS_CONFIG": str(business_config), "ANP_BUSINESS_DATA_ROOT": str(var / "data/business"),
                            "LANGSMITH_TRACING": "false"})
        return [root / "tools/python/bin/python3", "-I", "-B", root / "runtime/business_launcher.py"], environment
    raise RuntimeError("未知平台服务：" + name)
