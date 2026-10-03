import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from botocore.config import Config


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


endpoint = required_env("B2_ENDPOINT").strip()
bucket = required_env("B2_BUCKET_NAME").strip()
key_id = required_env("B2_KEY_ID").strip()
application_key = required_env("B2_APPLICATION_KEY").strip()

if not endpoint.startswith(("http://", "https://")):
    endpoint = "https://" + endpoint

host = urlparse(endpoint).hostname or ""
parts = host.split(".")
region = parts[1] if len(parts) >= 4 and parts[0] == "s3" else "us-west-004"

s3 = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=key_id,
    aws_secret_access_key=application_key,
    region_name=region,
    config=Config(signature_version="s3v4"),
)

now = datetime.now(timezone.utc)
object_key = f"smoke-tests/{now:%Y/%m/%d}/github-actions.txt"
body = (
    "japan-stock-pit B2 smoke test\n"
    f"uploaded_at_utc={now.isoformat()}\n"
    f"github_run_id={os.getenv('GITHUB_RUN_ID', 'unknown')}\n"
).encode("utf-8")

print(f"Testing bucket: {bucket}")
print(f"Endpoint: {endpoint}")
print(f"Object key: {object_key}")

s3.put_object(
    Bucket=bucket,
    Key=object_key,
    Body=body,
    ContentType="text/plain; charset=utf-8",
)

head = s3.head_object(Bucket=bucket, Key=object_key)
size = head.get("ContentLength")

if size != len(body):
    raise RuntimeError(f"Upload verification failed: expected {len(body)} bytes, got {size}")

print(f"B2 smoke test succeeded ({size} bytes).")
