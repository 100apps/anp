"""私有配置；宿主与容器共用相同契约，不读取旧项目运行数据。"""
import os
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("ANP_DUB_ROOT", Path(__file__).resolve().parents[4] / "var/dubbing")).resolve()


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"缺少私有配置 {name}，请先执行 make dub-config")
    return value


def local_input(value: str) -> Path:
    from urllib.parse import unquote, urlparse
    if value.startswith("file://"):
        value = unquote(urlparse(value).path).lstrip("/") if os.name == "nt" else unquote(urlparse(value).path)
    path = Path(value).resolve(strict=True)
    allowed = [root(), *[Path(p).resolve() for p in os.getenv("ANP_DUB_INPUT_ROOTS", "").split("|") if p]]
    if not path.is_file() or not any(path.is_relative_to(p) for p in allowed):
        raise ValueError("文件不在 ANP_DUB_INPUT_ROOTS 或本项目译配目录中")
    return path
