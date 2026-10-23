"""复制可独立开发的业务目录，并冻结安装本机 Python 运行依赖。"""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var/native"


def install(bundle, copy_tree):
    python = bundle / "tools/python/bin/python3"
    uv = bundle / "tools/uv/uv"
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(BASE / "venvs/business"), "UV_PYTHON_DOWNLOADS": "0"}
    node = bundle / "tools/node/bin"
    environment.update({"PATH": str(node) + ":" + os.environ["PATH"], "NPM_CONFIG_USERCONFIG": "/dev/null",
                        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/"})
    subprocess.run([str(node / "npm"), "ci", "--prefix", str(ROOT / "native/packages/business-tools"), "--ignore-scripts"], env=environment, check=True)
    pnpm = ROOT / "native/packages/business-tools/node_modules/pnpm/bin/pnpm.cjs"
    subprocess.run([str(node / "node"), str(pnpm), "install", "--frozen-lockfile"], cwd=ROOT / "business/ui", env=environment, check=True)
    subprocess.run([str(node / "node"), str(pnpm), "build"], cwd=ROOT / "business/ui", env=environment, check=True)
    from build import install_python
    install_python(ROOT / "business", uv, python, environment)
    packages = next((BASE / "venvs/business/lib").glob("python*/site-packages"))
    copy_tree(packages, bundle / "platform/business/site-packages")
    target = bundle / "business"
    target.mkdir()
    # 白名单复制源码、冻结锁和构建后的 UI，排除开发机配置、数据与秘密。
    for name in ("src", "tests", "supabase"):
        if (ROOT / "business" / name).is_dir():
            copy_tree(ROOT / "business" / name, target / name)
    for name in ("pyproject.toml", "uv.lock", "langgraph.json", "config.example.json", "Makefile", ".gitignore"):
        shutil.copy2(ROOT / "business" / name, target / name)
    ui = target / "ui"
    ui.mkdir()
    for name in ("src", "dist"):
        copy_tree(ROOT / "business/ui" / name, ui / name)
    for path in (ROOT / "business/ui").iterdir():
        if path.is_file() and path.suffix in (".json", ".yaml", ".ts", ".html") and "tsbuildinfo" not in path.name:
            shutil.copy2(path, ui / path.name)
    readme = ROOT / "business/README.md"
    if readme.exists():
        shutil.copy2(readme, target / "README.md")
