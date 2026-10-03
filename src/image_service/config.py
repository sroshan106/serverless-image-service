"""Service constants and environment lookups."""
import os

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TAGS = 10
UPLOAD_URL_TTL_SECONDS = 300
DOWNLOAD_URL_TTL_SECONDS = 300
PENDING_TTL_SECONDS = 24 * 60 * 60
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def table_name() -> str:
    return os.environ["TABLE_NAME"]


def bucket_name() -> str:
    return os.environ["BUCKET_NAME"]


def public_s3_endpoint() -> str | None:
    """Host baked into presigned URLs. Empty in AWS, the LocalStack URL locally."""
    return os.environ.get("PUBLIC_S3_ENDPOINT") or None


def allowed_origin() -> str:
    """CORS origin. The template always sets it; "*" only applies outside Lambda (unit tests)."""
    return os.environ.get("ALLOWED_ORIGIN") or "*"
