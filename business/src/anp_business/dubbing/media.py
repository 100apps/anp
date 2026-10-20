"""FFmpeg 与私有对象存储；命令使用 argv，输出绑定实际文件摘要。"""
import hashlib
import json
import mimetypes
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config

from anp_business.dubbing import repository as db
from anp_business.dubbing.settings import required, root


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def folder(run_id):
    path = root() / "runs" / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def command(run_id, args, timeout=1800, cwd=None):
    logs = folder(run_id) / "commands"
    logs.mkdir(exist_ok=True)
    prefix = logs / str(uuid4())
    with prefix.with_suffix(".log").open("wb") as log:
        result = subprocess.run([str(a) for a in args], stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout, cwd=cwd, creationflags=0x08000000 if os.name == "nt" else 0)
    prefix.with_suffix(".json").write_text(json.dumps({"argv": [str(a) for a in args], "exit_code": result.returncode},
                                                   ensure_ascii=False), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"媒体命令失败（{Path(args[0]).name}）；参见本轮 commands 日志")
    return prefix.with_suffix(".log").read_text(encoding="utf-8", errors="replace")


def ffmpeg(run_id, *args, timeout=1800, cwd=None):
    return command(run_id, [os.getenv("ANP_DUB_FFMPEG", "ffmpeg"), "-nostdin", "-hide_banner", "-y", *args], timeout, cwd)


def probe(path):
    result = subprocess.run([os.getenv("ANP_DUB_FFPROBE", "ffprobe"), "-v", "error", "-show_format",
                             "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("文件不是可识别的媒体")
    return json.loads(result.stdout)


def duration(path):
    return float(probe(path)["format"]["duration"])


def s3():
    return boto3.client("s3", endpoint_url=required("ANP_DUB_S3_ENDPOINT"),
                        aws_access_key_id=required("ANP_DUB_S3_ACCESS_KEY"),
                        aws_secret_access_key=required("ANP_DUB_S3_SECRET_KEY"), region_name="us-east-1",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def save_asset(run, path, kind, **metadata):
    path = Path(path)
    sha = digest(path)
    key = f"{run['workspace_id']}/{run['project_id']}/{run['id']}/{sha}{path.suffix}"
    bucket = os.getenv("ANP_DUB_S3_BUCKET", "anp2-dubbing")
    existing = db.rows("assets", bucket=bucket, object_key=key)
    if existing:
        return existing[0]
    s3().upload_file(str(path), bucket, key, ExtraArgs={"ContentType": mimetypes.guess_type(path)[0] or "application/octet-stream"})
    length = duration(path) if path.suffix.lower() in (".wav", ".mp4", ".mkv", ".mp3", ".webm") else None
    return db.insert("assets", workspace_id=run["workspace_id"], project_id=run["project_id"], kind=kind,
                     bucket=bucket, object_key=key, sha256=sha, size_bytes=path.stat().st_size,
                     duration_seconds=length, metadata=metadata)


def materialize(asset_id):
    asset = db.get("assets", asset_id)
    path = root() / "cache" / (asset["sha256"] + Path(asset["object_key"]).suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or digest(path) != asset["sha256"]:
        temporary = path.with_name(path.name + "." + str(uuid4()) + ".tmp")
        s3().download_file(asset["bucket"], asset["object_key"], str(temporary))
        if digest(temporary) != asset["sha256"]:
            raise ValueError("私有对象摘要不符")
        temporary.replace(path)
    return path


def write_json(run_id, name, value):
    path = folder(run_id) / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def srt_time(seconds):
    value = round(seconds * 1000)
    return f"{value//3600000:02}:{value//60000%60:02}:{value//1000%60:02},{value%1000:03}"


def subtitles(rows, texts):
    return "\n\n".join(f"{i+1}\n{srt_time(r['start_seconds'])} --> {srt_time(r['end_seconds'])}\n{texts[str(r['id'])]}"
                         for i, r in enumerate(rows)) + "\n"
