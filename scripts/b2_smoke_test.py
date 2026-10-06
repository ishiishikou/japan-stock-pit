import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def is_b2_cap_exceeded(exc):
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or "").lower()
    return (
        code == "AccessDenied"
        and "cap exceeded" in message
        and ("download" in message or "class b" in message or "transaction" in message)
    )


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
    config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        ),
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

try:
    downloaded = s3.get_object(Bucket=bucket, Key=object_key)["Body"].read()
except ClientError as exc:
    if is_b2_cap_exceeded(exc):
        print(json.dumps({
            "status": "paused",
            "reason": "b2_download_or_class_b_cap_exceeded",
            "write_succeeded": True,
            "error": str(exc)[:500],
        }, ensure_ascii=False, indent=2))
    else:
        raise
else:
    if downloaded != body:
        raise RuntimeError(
            f"Upload verification failed: expected {len(body)} bytes, got {len(downloaded)}"
        )
    print(f"B2 smoke test succeeded ({len(downloaded)} bytes).")
