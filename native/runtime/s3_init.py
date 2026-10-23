"""通过随包 boto3 初始化本目录 S3，秘密只经 stdin 传递。"""
import json
from pathlib import Path
import site
import sys

ROOT = Path(__file__).resolve().parents[1]
site.addsitedir(str(ROOT / "platform/litellm/site-packages"))
import boto3
from botocore.exceptions import ClientError

options = json.load(sys.stdin)
client = boto3.client("s3", endpoint_url=options["endpoint"], aws_access_key_id=options["access"],
                      aws_secret_access_key=options["secret"], region_name="us-east-1")
for bucket in ("supabase", "langfuse"):
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise RuntimeError("S3 bucket 检查失败") from None
        client.create_bucket(Bucket=bucket)
