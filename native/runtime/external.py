"""显式外部资源配置；不建库、建角色，不启动本地存储。"""
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode

STORAGE = {"postgres", "redis", "clickhouse", "rustfs"}


def load(config):
    filename = config.get("external_resources_file")
    if not filename:
        return None
    path = Path(filename)
    if not path.is_absolute() or path.stat().st_mode & 0o077:
        raise RuntimeError("外部资源配置必须是绝对路径，权限为 0600")
    resources = json.loads(path.read_text())
    for name in ("postgres", "redis", "clickhouse", "s3"):
        if not isinstance(resources.get(name), dict):
            raise RuntimeError("外部资源配置缺少：" + name)
    required = {"postgres": ("host", "username", "password", "supabase_database", "langfuse_database", "litellm_database"),
                "redis": ("host", "password"), "clickhouse": ("endpoint", "migration_url", "username", "password", "database"),
                "s3": ("endpoint", "bucket", "region", "access_key", "secret_key")}
    for name, fields in required.items():
        for field in fields:
            if not resources[name].get(field):
                raise RuntimeError("外部资源配置缺少字段：" + name + "." + field)
    namespace = resources.get("namespace", "")
    if resources["s3"].get("request_checksum_calculation", "WHEN_SUPPORTED") not in ("WHEN_SUPPORTED", "WHEN_REQUIRED"):
        raise RuntimeError("s3.request_checksum_calculation 必须为 WHEN_SUPPORTED 或 WHEN_REQUIRED")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,48}", namespace):
        raise RuntimeError("外部资源必须配置独立 namespace")
    for role in resources.get("roles", {}).values():
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
            raise RuntimeError("外部数据库角色格式无效")
    return resources


def pgurl(resources, database, role=None):
    pg = resources["postgres"]
    query = {"application_name": resources["namespace"], "sslmode": pg.get("sslmode", "prefer")}
    if role:
        query["options"] = "-c role=" + role
        configured = roles(resources)
        for service, schema in (("auth", "auth"), ("storage", "storage")):
            if role == configured[service]:
                # SET ROLE 不继承 ALTER ROLE 的默认 GUC；必须显式选择既有迁移 schema。
                query["options"] += " -c search_path=" + schema + ",public,extensions"
    if database in (pg.get("langfuse_database"), pg.get("litellm_database")):
        # Prisma 在既有专用数据库中使用本次独立 schema，避免升级其他项目表。
        query["schema"] = resources["namespace"].replace("-", "_") + ("_litellm" if database == pg.get("litellm_database") else "")
    return ("postgresql://" + quote(pg["username"], safe="") + ":" + quote(pg["password"], safe="")
            + "@" + pg["host"] + ":" + str(pg.get("port", 5432)) + "/" + quote(database, safe="") + "?" + urlencode(query))


def roles(resources):
    return {"anon": "anon", "authenticated": "authenticated", "service": "service_role",
            "auth": "supabase_auth_admin", "storage": "supabase_storage_admin", "langfuse": "langfuse", "litellm": "litellm",
            **resources.get("roles", {})}


def environment(name, resources, public_url):
    pg, redis, ch, s3 = [resources[key] for key in ("postgres", "redis", "clickhouse", "s3")]
    role = roles(resources)
    database = pg["supabase_database"]
    public_url = public_url.rstrip("/")
    overrides = {
        "auth": {"GOTRUE_DB_DATABASE_URL": pgurl(resources, database, role["auth"]),
                 "GOTRUE_JWT_ADMIN_ROLES": role["service"], "GOTRUE_JWT_DEFAULT_GROUP_NAME": role["authenticated"],
                 "API_EXTERNAL_URL": public_url + "/auth/v1", "GOTRUE_SITE_URL": public_url + "/workbench/"},
        "postgrest": {"PGRST_DB_URI": pgurl(resources, database), "PGRST_DB_SCHEMAS": "public",
                      "PGRST_DB_ANON_ROLE": role["anon"]},
        "meta": {"PG_META_DB_HOST": pg["host"], "PG_META_DB_PORT": str(pg.get("port", 5432)),
                 "PG_META_DB_NAME": database, "PG_META_DB_USER": pg["username"], "PG_META_DB_PASSWORD": pg["password"]},
        "studio": {"POSTGRES_HOST": pg["host"], "POSTGRES_PORT": str(pg.get("port", 5432)),
                   # 当前官方 Studio 直接拼连接 URI；环境中的密码须先做 URI 转义。
                   "POSTGRES_DB": database, "POSTGRES_USER": pg["username"], "POSTGRES_PASSWORD": quote(pg["password"], safe=""),
                   "POSTGRES_USER_READ_WRITE": pg["username"], "POSTGRES_USER_READ_ONLY": role.get("studio_read_only", "anp_studio_reader")},
        "storage-api": {"DATABASE_URL": pgurl(resources, database, role["storage"]),
                        "TENANT_ID": resources["namespace"],
                        "DB_ANON_ROLE": role["anon"], "DB_AUTHENTICATED_ROLE": role["authenticated"],
                        "DB_SERVICE_ROLE": role["service"], "DB_INSTALL_ROLES": "false", "DB_SUPER_USER": role["storage"],
                        "GLOBAL_S3_BUCKET": s3["bucket"], "GLOBAL_S3_ENDPOINT": s3["endpoint"],
                        "GLOBAL_S3_PROTOCOL": "https", "GLOBAL_S3_FORCE_PATH_STYLE": str(s3.get("force_path_style", False)).lower(),
                        "REGION": s3["region"], "AWS_DEFAULT_REGION": s3["region"],
                        "AWS_REQUEST_CHECKSUM_CALCULATION": s3.get("request_checksum_calculation", "WHEN_SUPPORTED"),
                        "AWS_ACCESS_KEY_ID": s3["access_key"], "AWS_SECRET_ACCESS_KEY": s3["secret_key"]},
    }
    if name.startswith("langfuse-"):
        prefix = "{" + resources["namespace"] + "}:"
        result = {"DATABASE_URL": pgurl(resources, pg["langfuse_database"], role["langfuse"]),
                  "DIRECT_URL": pgurl(resources, pg["langfuse_database"], role["langfuse"]),
                  "CLICKHOUSE_URL": ch["endpoint"], "CLICKHOUSE_MIGRATION_URL": ch["migration_url"],
                  "CLICKHOUSE_USER": ch["username"], "CLICKHOUSE_PASSWORD": ch["password"], "CLICKHOUSE_DB": ch["database"],
                  "REDIS_HOST": redis["host"], "REDIS_PORT": str(redis.get("port", 6379)), "REDIS_AUTH": redis["password"],
                  "REDIS_KEY_PREFIX": prefix, "REDIS_CLUSTER_ENABLED": str(redis.get("cluster", False)).lower(),
                  "REDIS_CLUSTER_NODES": redis["host"] + ":" + str(redis.get("port", 6379)),
                  "LANGFUSE_INIT_ORG_ID": resources["namespace"], "LANGFUSE_INIT_PROJECT_ID": resources["namespace"],
                  "LANGFUSE_INIT_USER_EMAIL": resources["namespace"] + "@anp.local",
                  "LANGFUSE_S3_DELETE_OBJECTS_CHECKSUM_ALGORITHM": "MD5",
                  "AWS_REQUEST_CHECKSUM_CALCULATION": s3.get("request_checksum_calculation", "WHEN_SUPPORTED"),
                  "NEXTAUTH_URL": public_url + "/langfuse/api/auth"}
        for kind in ("EVENT", "MEDIA", "BATCH_EXPORT"):
            base = "LANGFUSE_S3_" + kind + "_UPLOAD_"
            result.update({base + "BUCKET": s3["bucket"], base + "REGION": s3["region"], base + "ENDPOINT": s3["endpoint"],
                           base + "FORCE_PATH_STYLE": str(s3.get("force_path_style", False)).lower(),
                           base + "ACCESS_KEY_ID": s3["access_key"], base + "SECRET_ACCESS_KEY": s3["secret_key"],
                           base + "PREFIX": resources["namespace"] + "/langfuse/" + kind.lower() + "/"})
        return result
    return overrides.get(name, {})


def redact(text, resources):
    for item in resources.values():
        if isinstance(item, dict):
            for key, value in item.items():
                if any(part in key for part in ("password", "secret", "access_key")) and isinstance(value, str) and value:
                    text = text.replace(value, "[REDACTED]")
                    text = text.replace(quote(value, safe=""), "[REDACTED]")
    return text
