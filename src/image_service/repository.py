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
from botocore.exceptions import ClientError

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


def mark_available(item: dict, size_bytes: int) -> bool:
    """Publish a PENDING image and its tag copies atomically.

    Returns False when the record is no longer PENDING (duplicate S3 event, or deleted meanwhile).
    """
    sort_key = _sort_key(item)
    sets = ["#status = :available", "size_bytes = :size", "gsi1pk = :owner", "gsi1sk = :sort"]
    values = {
        ":available": AVAILABLE,
        ":pending": PENDING,
        ":size": size_bytes,
        ":owner": f"USER#{item['owner_id']}",
        ":sort": sort_key,
    }
    if item["visibility"] == "public":
        # ponytail: one PUBLIC partition caps the feed near 1000 writes/s; shard to PUBLIC#0..N if that ever matters.
        sets += ["gsi2pk = :public", "gsi2sk = :sort"]
        values[":public"] = "PUBLIC"
    published = {**item, "status": AVAILABLE, "size_bytes": size_bytes}
    published.pop("expires_at", None)
    actions = [
        {
            "Update": {
                "TableName": config.table_name(),
                "Key": _image_key(item["image_id"]),
                "UpdateExpression": f"SET {', '.join(sets)} REMOVE expires_at",
                "ConditionExpression": "#status = :pending",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": values,
            }
        }
    ]
    actions += [
        {"Put": {"TableName": config.table_name(), "Item": {**published, "pk": f"TAG#{tag}", "sk": sort_key}}}
        for tag in item["tags"]
    ]
    try:
        _table().meta.client.transact_write_items(TransactItems=actions)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        current = get_image(item["image_id"])
        if current is not None and current["status"] == PENDING:
            raise  # conflict or throttling, not a lost race: let Lambda retry the S3 event
        return False
    return True


def delete_image(item: dict) -> None:
    """Remove the record and every tag copy. Deleting missing items is a no-op."""
    sort_key = _sort_key(item)
    keys = [_image_key(item["image_id"])] + [{"pk": f"TAG#{tag}", "sk": sort_key} for tag in item["tags"]]
    _table().meta.client.transact_write_items(
        TransactItems=[{"Delete": {"TableName": config.table_name(), "Key": key}} for key in keys]
    )
