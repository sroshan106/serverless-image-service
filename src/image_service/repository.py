"""DynamoDB access for image records.

Single table layout:
  IMG#<id>  / META            image record. gsi1 (by owner) and gsi2 (public feed) keys are set once AVAILABLE.
  TAG#<tag> / <created>#<id>  copy of the record per tag, written once AVAILABLE.
"""
import functools
import time
import uuid
from datetime import datetime, timezone

import boto3

from image_service import config
from image_service.validation import NewImage, format_ts

PENDING = "PENDING"
AVAILABLE = "AVAILABLE"


@functools.cache
def _table():
    return boto3.resource("dynamodb").Table(config.table_name())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _image_key(image_id: str) -> dict:
    return {"pk": f"IMG#{image_id}", "sk": "META"}


def _sort_key(item: dict) -> str:
    return f"{item['created_at']}#{item['image_id']}"


def create_pending(owner_id: str, new: NewImage) -> dict:
    image_id = str(uuid.uuid4())
    item = {
        **_image_key(image_id),
        "image_id": image_id,
        "owner_id": owner_id,
        "title": new.title,
        "title_lower": new.title.lower(),
        "description": new.description,
        "tags": new.tags,
        "visibility": new.visibility,
        "content_type": new.content_type,
        "s3_key": f"images/{owner_id}/{image_id}",
        "status": PENDING,
        "created_at": format_ts(_now()),
        # DynamoDB TTL removes records whose upload never arrives.
        "expires_at": int(time.time()) + config.PENDING_TTL_SECONDS,
    }
    _table().put_item(Item=item)
    return item


def get_image(image_id: str) -> dict | None:
    return _table().get_item(Key=_image_key(image_id)).get("Item")
