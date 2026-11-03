"""上海运行/展示时区；Langfuse 的固定 UTC 数据编码需显式列类型。"""
import re
from pathlib import Path

DEFAULT_TIMEZONE = "Asia/Shanghai"


def explicit_langfuse_utc(sql):
    """只补齐未标时区的类型；保留函数、已有显式时区和精度。"""
    sql = re.sub(r"\bDateTime64\((\d+)\)", r"DateTime64(\1, 'UTC')", sql)
    return re.sub(r"\bDateTime\b(?!\s*\()", "DateTime('UTC')", sql)


def prepare_langfuse_migrations(root):
    """只修改构建产物的建表 SQL，不连接或迁移已有数据库。"""
    root = Path(root)
    folder = root / "web/packages/shared/clickhouse/migrations"
    if not folder.is_dir():
        raise RuntimeError("Missing locked Langfuse ClickHouse migrations")
    changed = []
    for path in sorted(folder.rglob("*.sql")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Unexpected Langfuse migration symlink")
        before = path.read_text()
        after = explicit_langfuse_utc(before)
        if before != after:
            temporary = path.with_suffix(path.suffix + ".timezone-tmp")
            temporary.write_text(after)
            temporary.chmod(path.stat().st_mode & 0o777)
            temporary.replace(path)
            changed.append(str(path.relative_to(root)))
    return changed
