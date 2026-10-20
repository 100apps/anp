"""真实模型适配器；不以确定性演示输出替代翻译或音频。"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI

from anp_business.dubbing import media
from anp_business.dubbing.settings import required


def model(review=False):
    return ChatOpenAI(model=os.getenv("ANP_DUB_REVIEW_MODEL" if review else "ANP_DUB_MODEL", "auto"),
                      base_url=required("ANP_DUB_MODEL_BASE_URL"), api_key=required("ANP_DUB_MODEL_API_KEY"),
                      temperature=0, max_retries=0, timeout=180)


@contextmanager
def gpu_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + int(os.getenv("ANP_DUB_GPU_WAIT", "900"))
        while True:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("GPU 正被其他任务使用，保留任务等待重试；未停止外部进程") from None
                time.sleep(1)
        yield


def audio_task(run_id, task, request):
    """已有远程API或本项目持有的原生模型进程。共享GPU使用旧路由同一文件锁。"""
    config = json.loads(Path(required("ANP_DUB_AUDIO_CONFIG")).read_text(encoding="utf-8"))
    entry = config["models"][task]
    body = {"model": entry["id"], "busy_timeout_ms": 1000, "request": request}
    if config.get("transport") == "bridge":
        body = {"task": task, "request": dict(request), "files": {}}
        for name in ("audio", "voice_ref"):
            if isinstance(request.get(name), str) and Path(request[name]).is_file():
                raw = Path(request[name]).read_bytes()
                if len(raw) > 64 * 1024 ** 2:
                    raise ValueError("远端音频输入超过64MiB，应先分块")
                body["files"][name] = base64.b64encode(raw).decode()
                body["request"].pop(name)
        with httpx.Client(timeout=1200, trust_env=False) as client:
            response = client.post(config["endpoint"].rstrip("/") + "/tasks", json=body,
                                    headers={"Authorization": "Bearer " + required("ANP_DUB_AUDIO_TOKEN")})
            if response.status_code != 200:
                raise RuntimeError(f"本项目音频计算端请求失败（HTTP {response.status_code}）")
            return response.json()
    if config.get("transport") == "http":
        with httpx.Client(timeout=900, trust_env=False) as client:
            response = client.post(config["endpoint"].rstrip("/") + "/v1/tasks/run", json=body)
            response.raise_for_status()
            return response.json()
    with gpu_lock(config["coordination_lock"]):
        port = config["port"]
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("本项目模型端口已占用，拒绝接管")
        server_config = {"host": "127.0.0.1", "port": port, "backend": config.get("backend", "cuda"),
                         "device": config.get("device", 0), "threads": 4, "lazy_load": True, "models": [entry]}
        config_path = media.write_json(run_id, f"audio-{task}-server.json", server_config)
        log_path = media.folder(run_id) / f"audio-{task}-{time.time_ns()}.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen([config["executable"], "--config", str(config_path), "--max-loaded-models", "1"],
                cwd=str(Path(config["executable"]).parent), stdout=log, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0)
            try:
                endpoint = f"http://127.0.0.1:{port}"
                with httpx.Client(timeout=900, trust_env=False) as client:
                    for _ in range(150):
                        if process.poll() is not None:
                            raise RuntimeError("本项目模型进程启动失败，参见 audio 日志")
                        try:
                            if client.get(endpoint + "/health", timeout=1).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(.2)
                    else:
                        raise TimeoutError("本项目模型进程未就绪")
                    response = client.post(endpoint + "/v1/tasks/run", json=body)
                    if response.status_code != 200:
                        media.write_json(run_id, f"audio-{task}-error.json", {"status": response.status_code, "body": response.text[:8000]})
                        raise RuntimeError(f"音频模型 {task} 请求失败，参见本轮错误回执")
                    return response.json()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)


def decode_audio(result, destination):
    raw = base64.b64decode(result["audio"], validate=True)
    if not 44 < len(raw) < 256 * 1024 ** 2:
        raise ValueError("音频响应大小异常")
    Path(destination).write_bytes(raw)
    if media.duration(destination) <= 0:
        raise ValueError("模型未返回有效音频")
    return destination
