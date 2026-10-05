"""Parse and validate client input. Every rejection is a 400/404 ApiError."""
import base64
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone

from image_service.config import ALLOWED_CONTENT_TYPES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MAX_TAGS
from image_service.http import USER_ID_RE, ApiError
from image_service.models import Visibility

TAG_RE = re.compile(r"[a-z0-9_-]{1,32}")
# Extended ISO dates only. Basic "20261002" would slip past the date only (end of day) check below.
ISO_DATE_PREFIX_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CREATE_FIELDS = {"title", "description", "tags", "visibility", "content_type"}
LIST_PARAMS = {"user_id", "tag", "created_from", "created_to", "title", "visibility", "limit", "next_token"}
# A page cursor is the sort key of an image the caller was allowed to read: "<created_at>#<image_id>".
CURSOR_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z#[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class NewImage:
    title: str
    description: str
    tags: list[str]
    visibility: Visibility
    content_type: str


@dataclass(frozen=True)
class ListQuery:
    user_id: str | None = None
    tag: str | None = None
    created_from: str | None = None
    created_to: str | None = None
    title: str | None = None
    visibility: Visibility | None = None
    limit: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None


def _invalid(message: str) -> ApiError:
    return ApiError(400, "validation_error", message)


def format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_uuid(value) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _string(body: dict, field: str, required: bool, max_len: int) -> str:
    value = body.get(field)
    if value is None:
        if required:
            raise _invalid(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise _invalid(f"{field} must not be blank")
    if len(value) > max_len:
        raise _invalid(f"{field} must be at most {max_len} characters")
    return value


def _tag(raw) -> str:
    tag = raw.strip().lower() if isinstance(raw, str) else ""
    if not TAG_RE.fullmatch(tag):
        raise _invalid("each tag must be 1 to 32 characters of a-z, 0-9, _ or -")
    return tag


def parse_new_image(body: dict) -> NewImage:
    unknown = set(body) - CREATE_FIELDS
    if unknown:
        raise _invalid(f"unknown fields: {', '.join(sorted(unknown))}")
    raw_tags = body.get("tags", [])
    if not isinstance(raw_tags, list):
        raise _invalid("tags must be a list of strings")
    tags = list(dict.fromkeys(_tag(raw) for raw in raw_tags))
    if len(tags) > MAX_TAGS:
        raise _invalid(f"at most {MAX_TAGS} tags are allowed")
    visibility = body.get("visibility", Visibility.PUBLIC)
    if visibility not in list(Visibility):
        raise _invalid("visibility must be public or private")
    content_type = body.get("content_type")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise _invalid(f"content_type must be one of {', '.join(ALLOWED_CONTENT_TYPES)}")
    return NewImage(
        title=_string(body, "title", required=True, max_len=100),
        description=_string(body, "description", required=False, max_len=1000),
        tags=tags,
        visibility=Visibility(visibility),
        content_type=content_type,
    )


def _timestamp(value: str | None, field: str, end_of_day: bool) -> str | None:
    if value is None:
        return None
    if not ISO_DATE_PREFIX_RE.match(value):
        raise _invalid(f"{field} must be an ISO 8601 date (YYYY-MM-DD) or datetime")
    try:
        dt = datetime.fromisoformat(value)
        if end_of_day and len(value) == 10:  # date only: include the whole day
            dt = datetime.combine(dt.date(), time.max)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_ts(dt)
    except (ValueError, OverflowError) as exc:  # OverflowError: offsets that push year 9999 past the range
        raise _invalid(f"{field} must be an ISO 8601 date or datetime") from exc


def _limit(value: str | None) -> int:
    if value is None:
        return DEFAULT_PAGE_SIZE
    try:
        limit = int(value)
    except ValueError as exc:
        raise _invalid("limit must be an integer") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise _invalid(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return limit


def encode_next_token(cursor: str) -> str:
    return base64.urlsafe_b64encode(cursor.encode()).decode().rstrip("=")


def decode_next_token(token: str) -> str:
    try:
        cursor = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("ascii")
    except ValueError as exc:  # binascii.Error and UnicodeDecodeError are both ValueErrors
        raise _invalid("next_token is invalid") from exc
    if not CURSOR_RE.fullmatch(cursor):
        raise _invalid("next_token is invalid")
    return cursor


def parse_list_query(params: dict | None) -> ListQuery:
    params = params or {}
    unknown = set(params) - LIST_PARAMS
    if unknown:
        raise _invalid(f"unknown query parameters: {', '.join(sorted(unknown))}")
    user_id = params.get("user_id")
    if user_id is not None and not USER_ID_RE.fullmatch(user_id):
        raise _invalid("user_id is invalid")
    title = params.get("title")
    if title is not None:
        title = title.strip().lower()
        if not title or len(title) > 100:
            raise _invalid("title must be 1 to 100 characters")
    visibility = params.get("visibility")
    if visibility is not None and visibility not in list(Visibility):
        raise _invalid("visibility must be public or private")
    created_from = _timestamp(params.get("created_from"), "created_from", end_of_day=False)
    created_to = _timestamp(params.get("created_to"), "created_to", end_of_day=True)
    if created_from and created_to and created_from > created_to:
        raise _invalid("created_from must not be after created_to")
    token = params.get("next_token")
    return ListQuery(
        user_id=user_id,
        tag=_tag(params["tag"]) if "tag" in params else None,
        created_from=created_from,
        created_to=created_to,
        title=title,
        visibility=Visibility(visibility) if visibility else None,
        limit=_limit(params.get("limit")),
        cursor=decode_next_token(token) if token else None,
    )


def parse_image_id(event: dict) -> str:
    image_id = (event.get("pathParameters") or {}).get("image_id")
    if not is_uuid(image_id):
        raise ApiError(404, "not_found", "Image not found")
    return image_id


def sniff_content_type(head: bytes) -> str | None:
    """Content type from the file signature; the client's declared type is not trusted."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
