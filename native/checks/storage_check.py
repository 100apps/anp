"""原生包真实 TUS 验收；只创建并清理本次独立桶，不输出密钥。"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid


def check(root):
    root = Path(root).resolve()
    config_path = root / "config/anp.json"
    if not config_path.exists():
        config_path = root / "defaults/anp.json"
    config = json.loads(config_path.read_text())
    origin = "http://127.0.0.1:" + str(config["ports"].get("gateway", config["ports"]["business"]))
    secret = json.loads((root / "var/secrets/runtime.json").read_text())
    auth = {"Authorization": "Bearer " + secret["service-key"], "apikey": secret["service-key"]}
    checks = {}

    def request(method, path, data=None, headers=None):
        target = path if path.startswith(origin + "/") else origin + "/storage/v1" + path
        with urlopen(Request(target, data=data, method=method, headers={**auth, **(headers or {})}), timeout=90) as response:
            return response.status, response.headers, response.read()

    bucket = "anp-tus-check-" + uuid.uuid4().hex
    request("POST", "/bucket", json.dumps({"id": bucket, "name": bucket, "public": False}).encode(), {"Content-Type": "application/json"})
    try:
        ordinary = "ANP 普通上传验收".encode()
        status, _, _ = request("POST", "/object/" + bucket + "/ordinary.txt", ordinary, {"Content-Type": "text/plain"})
        assert status in (200, 201)
        status, _, actual = request("GET", "/object/authenticated/" + bucket + "/ordinary.txt")
        assert status == 200 and actual == ordinary
        checks["ordinary_upload_readback"] = True
        status, headers, _ = request("OPTIONS", "/upload/resumable", headers={
            "Origin": "http://127.0.0.1:" + str(config["ports"]["studio"]), "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,apikey,content-type,tus-resumable,upload-length,upload-metadata,x-source,x-client-info"})
        allowed = ",".join(headers.get_all("Access-Control-Allow-Headers", [])).lower()
        assert status == 204 and all(h in allowed for h in ("x-source", "x-client-info", "upload-metadata"))
        checks["studio_cors_preflight"] = True
        payload = b"ANP-TUS-" * (1024 * 1024)
        metadata = {"bucketName": bucket, "objectName": "resume.bin", "contentType": "application/octet-stream"}
        status, headers, _ = request("POST", "/upload/resumable", payload[:6*1024*1024], {
            "Tus-Resumable": "1.0.0", "Upload-Length": str(len(payload)),
            "Upload-Metadata": ",".join(k + " " + base64.b64encode(v.encode()).decode() for k, v in metadata.items()),
            "Content-Type": "application/offset+octet-stream", "x-source": "anp-acceptance"})
        assert status == 201
        location = headers["Location"]
        assert urlsplit(location).netloc == urlsplit(origin).netloc and urlsplit(location).scheme == "http"
        assert urlsplit(location).path.startswith("/storage/v1/upload/resumable/")
        checks["location_scheme_port_prefix"] = True
        status, headers, _ = request("HEAD", location, headers={"Tus-Resumable": "1.0.0"})
        offset = int(headers["Upload-Offset"])
        assert status == 200 and offset == 6*1024*1024
        checks["head_resume_offset"] = True
        status, headers, _ = request("PATCH", location, payload[offset:], {
            "Tus-Resumable": "1.0.0", "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"})
        assert status == 204 and int(headers["Upload-Offset"]) == len(payload)
        checks["patch_complete"] = True
        status, _, actual = request("GET", "/object/authenticated/" + bucket + "/resume.bin")
        assert status == 200 and actual == payload
        checks["readback_8mib_sha256"] = hashlib.sha256(actual).hexdigest()
    finally:
        request("POST", "/bucket/" + bucket + "/empty", b"{}", {"Content-Type": "application/json"})
        request("DELETE", "/bucket/" + bucket)
    checks["own_bucket_cleaned"] = True
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="运行中的原生包绝对路径")
    args = parser.parse_args()
    print(json.dumps(check(args.root), ensure_ascii=False, indent=2))
