"""前台开发进程管理；只停止本工作区自己登记的进程。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "var/run"


def url_json(url):
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


def verify_frontend():
    try:
        value = url_json("http://127.0.0.1:51760/__anp_workspace")
        return value.get("project") == "anp3" and Path(value["root"]).resolve() == ROOT / "business/ui"
    except (OSError, ValueError, KeyError):
        return False


def stop_role(role):
    path = RUN / (role + ".json")
    if not path.exists():
        return
    info = json.loads(path.read_text())
    try:
        process = psutil.Process(info["pid"])
        command = process.cmdline()
        cwd = Path(process.cwd())
        owned_script = any(
            (Path(arg) if Path(arg).is_absolute() else cwd / arg).resolve() == Path(__file__).resolve()
            for arg in command[1:] if arg.replace("\\", "/").endswith("scripts/dev.py")
        )
        if (abs(process.create_time() - info["created"]) > 0.1
                or not owned_script):
            raise RuntimeError("进程身份不匹配，拒绝停止：" + role)
        children = process.children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        _, alive = psutil.wait_procs([process, *children], timeout=5)
        for item in alive:
            try:
                item.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    path.unlink(missing_ok=True)


def serve(role):
    RUN.mkdir(parents=True, exist_ok=True)
    if role == "frontend" and verify_frontend():
        print("ANP3_FRONTEND_READY", flush=True)
        return
    path = RUN / (role + ".json")
    if path.exists():
        stop_role(role)
    me = psutil.Process()
    path.write_text(json.dumps({"pid": os.getpid(), "created": me.create_time()}))
    if role == "frontend":
        print("ANP3_FRONTEND_START", flush=True)
        # 依赖仍由pnpm锁定安装。直接运行已安装的Vite，避免Windows包管理器包装进程
        # 在非交互任务中提前退出，连带关闭前端服务。
        command = [shutil.which("node.exe") or shutil.which("node") or "node",
                   str(ROOT / "business/ui/node_modules/vite/bin/vite.js"),
                   "--host", "127.0.0.1", "--port", "51760", "--strictPort"]
        cwd = ROOT / "business/ui"
    else:
        command = [sys.executable, str(ROOT / "scripts/agent_dev.py")]
        cwd = ROOT / "business"
    env = os.environ.copy()
    if role == "frontend":
        # Vite 在无交互 stdin 结束时会退出；IDE/后台任务仍需保持 HMR 服务。
        env["CI"] = "true"
    if role == "backend-debug":
        env["ANP_DEBUG_PORT"] = "56796"
        print("ANP3_BACKEND_START", flush=True)
    log_dir = ROOT / "var/logs/dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    windows_options = {}
    if os.name == "nt" and role != "frontend":
        # Uvicorn的Windows重载用CTRL_C_EVENT。独立隐藏控制台使该信号能可靠投递，
        # 同时避免影响宿主终端或前端；普通无控制台后台进程会卡在旧应用上。
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        windows_options = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
    child = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", **windows_options)
    def copy_output():
        with (log_dir / (role + ".log")).open("w", encoding="utf-8") as log:
            for line in child.stdout:
                log.write(line)
                log.flush()
                # 后台任务的终端无人读取时，持续转发日志会堵塞管道，进而卡住热重载。
                # 默认写本项目日志；交互终端需要完整输出时可显式开启。
                if env.get("ANP_DEV_LOG_TO_TERMINAL") == "1":
                    try:
                        print(line, end="", flush=True)
                    except OSError:
                        pass
    threading.Thread(target=copy_output, daemon=True).start()
    try:
        if role == "frontend":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and child.poll() is None:
                if verify_frontend():
                    print("ANP3_FRONTEND_READY", flush=True)
                    break
                time.sleep(0.3)
            else:
                raise RuntimeError("前端未就绪；检查端口占用和日志")
        if role == "backend-debug":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and child.poll() is None:
                try:
                    if url_json("http://127.0.0.1:20260/api/health").get("service") == "anp3":
                        print("ANP3_BACKEND_READY", flush=True)
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("后端未就绪；检查端口占用及启动日志")
        return child.wait()
    finally:
        if child.poll() is None:
            process = psutil.Process(child.pid)
            for item in reversed(process.children(recursive=True)):
                try:
                    item.terminate()
                except psutil.NoSuchProcess:
                    pass
            child.terminate()
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["dev", "frontend", "backend", "backend-debug", "stop", "stop-frontend"])
    args = parser.parse_args()
    if args.command == "stop":
        for role in ("frontend", "backend", "backend-debug"):
            stop_role(role)
    elif args.command == "stop-frontend":
        stop_role("frontend")
    elif args.command == "dev":
        frontend = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "frontend"], cwd=ROOT)
        try:
            serve("backend")
        finally:
            stop_role("frontend")
            if frontend.poll() is None:
                frontend.terminate()
    else:
        serve(args.command)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
