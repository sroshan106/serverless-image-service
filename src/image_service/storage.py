"""S3 access: presigned URLs for clients, inspection and cleanup for the service."""
import functools

import boto3
from botocore.config import Config

from image_service import config

_SIGV4 = Config(signature_version="s3v4")


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
