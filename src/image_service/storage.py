"""S3 access: presigned URLs for clients, inspection and cleanup for the service."""
import functools
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from image_service import config

_SIGV4 = Config(signature_version="s3v4").merge(config.AWS_CLIENT_CONFIG)


@functools.cache
def _s3():
    return boto3.client("s3", config=_SIGV4)


@functools.cache
def _presign_s3():
    # Presigned URLs embed the host. On LocalStack it must resolve from the laptop and from Lambda containers.
    endpoint = config.public_s3_endpoint()
    if endpoint is None:
        return _s3()
    return boto3.client("s3", endpoint_url=endpoint, config=_SIGV4.merge(Config(s3={"addressing_style": "path"})))


def presign_upload(key: str, content_type: str) -> dict:
    """Presigned POST: S3 itself rejects wrong size or content type."""
    return _presign_s3().generate_presigned_post(
        Bucket=config.bucket_name(),
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, config.MAX_UPLOAD_BYTES]],
        ExpiresIn=config.UPLOAD_URL_TTL_SECONDS,
    )


def inspect_upload(key: str) -> tuple[int, bytes] | None:
    """Object size and its first 12 bytes (enough for every allowed file signature), in one ranged GET.

    None when the object is gone (deleted after the event fired), so the event is not retried for nothing.
    """
    try:
        resp = _s3().get_object(Bucket=config.bucket_name(), Key=key, Range="bytes=0-11")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            return None
        if code == "InvalidRange":  # empty object: no byte 0 to return
            return 0, b""
        raise
    head = resp["Body"].read()
    content_range = resp.get("ContentRange")  # "bytes 0-11/<total size>"
    size = int(content_range.rsplit("/", 1)[1]) if content_range else resp["ContentLength"]
    return size, head


def delete_object(key: str) -> None:
    _s3().delete_object(Bucket=config.bucket_name(), Key=key)


def presign_download(key: str, filename: str | None = None) -> str:
    """Presigned GET. With a filename the browser downloads instead of displaying."""
    params = {"Bucket": config.bucket_name(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _presign_s3().generate_presigned_url("get_object", Params=params, ExpiresIn=config.DOWNLOAD_URL_TTL_SECONDS)


if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
    _s3()  # build the client in the init phase, which runs at full CPU, not on the first request
