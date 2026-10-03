"""DynamoDB access for image records.

Single table layout:
  IMG#<id>  / META            image record. Index keys are set once AVAILABLE:
                              gsi1 USER#<owner>#<visibility>, gsi2 PUBLIC (public only).
  TAG#<tag> / <created>#<id>  copy of a public record per tag, written once AVAILABLE.
  TAG#<tag>#<owner> / ...     same for private records, one partition per owner.

Public and private images never share a partition, and each private partition has one owner. A list
query therefore only reads items the caller may see, so no cursor can point at another user's private image.
"""
import functools
import operator
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from image_service import config
from image_service.validation import ListQuery, NewImage, format_ts

PENDING = "PENDING"
AVAILABLE = "AVAILABLE"


# (index, partition attribute, sort attribute) per key layout.
_BY_TAG = (None, "pk", "sk")
_BY_OWNER = ("gsi1", "gsi1pk", "gsi1sk")
_FEED = ("gsi2", "gsi2pk", "gsi2sk")


@functools.cache
def _table():
    return boto3.resource("dynamodb", config=config.AWS_CLIENT_CONFIG).Table(config.table_name())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _image_key(image_id: str) -> dict:
    return {"pk": f"IMG#{image_id}", "sk": "META"}


def _sort_key(item: dict) -> str:
    return f"{item['created_at']}#{item['image_id']}"


def _owner_partition(owner_id: str, visibility: str) -> str:
    return f"USER#{owner_id}#{visibility}"


def _tag_partition(tag: str, owner_id: str, visibility: str) -> str:
    # Tags and user ids cannot contain "#", so the two shapes never collide.
    return f"TAG#{tag}" if visibility == "public" else f"TAG#{tag}#{owner_id}"


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
        ":owner": _owner_partition(item["owner_id"], item["visibility"]),
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
        {
            "Put": {
                "TableName": config.table_name(),
                "Item": {**published, "pk": _tag_partition(tag, item["owner_id"], item["visibility"]), "sk": sort_key},
            }
        }
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
    keys = [_image_key(item["image_id"])] + [
        {"pk": _tag_partition(tag, item["owner_id"], item["visibility"]), "sk": sort_key} for tag in item["tags"]
    ]
    _table().meta.client.transact_write_items(
        TransactItems=[{"Delete": {"TableName": config.table_name(), "Key": key}} for key in keys]
    )


def _partitions(caller_id: str, q: ListQuery) -> list[tuple]:
    """(index, partition attribute, sort attribute, partition value) for every partition this query reads.

    Only partitions whose images the caller may see: public ones, plus the caller's own private ones.
    """
    visibilities = [q.visibility] if q.visibility else ["public", "private"]
    owner = q.user_id or (caller_id if q.visibility == "private" else None)
    if owner and owner != caller_id:
        visibilities = [v for v in visibilities if v == "public"]
    if q.tag:
        parts = []
        if "public" in visibilities:
            parts.append((*_BY_TAG, _tag_partition(q.tag, "", "public")))
        if "private" in visibilities and owner in (None, caller_id):
            parts.append((*_BY_TAG, _tag_partition(q.tag, caller_id, "private")))
        return parts
    if owner:
        return [(*_BY_OWNER, _owner_partition(owner, v)) for v in visibilities]
    return [(*_FEED, "PUBLIC")]


def list_images(caller_id: str, q: ListQuery) -> tuple[list[dict], str | None]:
    """Newest first, merged across the partitions from _partitions. Key queries only, never scans.

    Returns the page and a cursor (sort key to continue below, exclusive), or None at the end.
    Filters run as a FilterExpression, so queries repeat until the page is full, up to MAX_QUERY_ROUNDS.
    """
    parts = _partitions(caller_id, q)
    low = q.created_from or "0"
    high = (q.created_to or "9999") + "#~"  # "~" sorts after every uuid character
    if q.cursor is not None:
        high = min(high, q.cursor)
    filters = []
    if q.tag and q.user_id:
        filters.append(Attr("owner_id").eq(q.user_id))
    if q.title:
        # ponytail: contains is a post filter; move title search to OpenSearch if it becomes a primary use case.
        filters.append(Attr("title_lower").contains(q.title))

    items: list[dict] = []
    for _ in range(config.MAX_QUERY_ROUNDS):
        if not parts or low > high:
            return items, None
        found, frontier = [], None
        for index, part_attr, sort_attr, part_value in parts:
            kwargs = {
                "KeyConditionExpression": Key(part_attr).eq(part_value) & Key(sort_attr).between(low, high),
                # BETWEEN includes the cursor item itself; one extra read keeps the frontier strictly below it.
                "Limit": q.limit + 1,
                "ScanIndexForward": False,
            }
            if index:
                kwargs["IndexName"] = index
            if filters:
                kwargs["FilterExpression"] = functools.reduce(operator.and_, filters)
            resp = _table().query(**kwargs)
            found += resp["Items"]
            if "LastEvaluatedKey" in resp:
                last = resp["LastEvaluatedKey"][sort_attr]
                frontier = last if frontier is None else max(frontier, last)
        # Every partition has been read down to the frontier, so the items at or above it are complete.
        found = [i for i in found if _sort_key(i) != high and (frontier is None or _sort_key(i) >= frontier)]
        items += sorted(found, key=_sort_key, reverse=True)
        if len(items) >= q.limit:
            items = items[: q.limit]
            return items, _sort_key(items[-1])
        if frontier is None:
            return items, None
        high = frontier
    return items, high


if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
    _table()  # build the client in the init phase, which runs at full CPU, not on the first request
