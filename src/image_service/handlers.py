"""Lambda entry points. One function per API route plus the S3 upload processor."""
import logging
import os
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

from image_service import repository, storage
from image_service.config import DOWNLOAD_URL_TTL_SECONDS, EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_URL_TTL_SECONDS
from image_service.http import ApiError, api_handler, caller_id, json_body, json_response, response
from image_service.repository import AVAILABLE, PENDING
from image_service.validation import (
    encode_next_token,
    is_uuid,
    parse_image_id,
    parse_list_query,
    parse_new_image,
    sniff_content_type,
)

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

PUBLIC_FIELDS = (
    "image_id", "owner_id", "title", "description", "tags", "visibility", "content_type", "size_bytes", "created_at",
)


def to_public(item: dict) -> dict:
    out = {field: item.get(field) for field in PUBLIC_FIELDS}
    if out["size_bytes"] is not None:
        out["size_bytes"] = int(out["size_bytes"])  # DynamoDB returns Decimal
    return out


def visible_to(item: dict, caller: str) -> bool:
    return item["owner_id"] == caller or (item["visibility"] == "public" and item["status"] == AVAILABLE)


def _viewable_image(event) -> dict:
    caller = caller_id(event)
    item = repository.get_image(parse_image_id(event))
    # 404 (not 403) for other users' private images, so ids cannot be probed.
    if item is None or item["status"] != AVAILABLE or not visible_to(item, caller):
        raise ApiError(404, "not_found", "Image not found")
    return item


def _filename(item: dict) -> str:
    return f"{item['image_id']}{EXTENSIONS[item['content_type']]}"


@api_handler
def create_image(event, _context):
    owner_id = caller_id(event)
    new = parse_new_image(json_body(event))
    item = repository.create_pending(owner_id, new)
    upload = storage.presign_upload(item["s3_key"], new.content_type)
    return json_response(
        201,
        {
            "image_id": item["image_id"],
            "status": item["status"],
            "upload": {"url": upload["url"], "fields": upload["fields"], "expires_in": UPLOAD_URL_TTL_SECONDS},
        },
        headers={"Location": f"/images/{item['image_id']}"},
    )


def process_upload(event, _context):
    """S3 ObjectCreated handler: verify the object, then publish or reject it."""
    for record in event.get("Records", []):
        _process_object(unquote_plus(record["s3"]["object"]["key"]))


def _process_object(key: str) -> None:
    image_id = key.rsplit("/", 1)[-1]
    item = repository.get_image(image_id) if is_uuid(image_id) else None
    if item is None or item["s3_key"] != key:
        logger.warning("Deleting object with no matching record: %s", key)
        storage.delete_object(key)
        return
    upload = storage.inspect_upload(key)
    if upload is None:
        logger.info("Object already gone: %s", key)
        return
    size, head = upload
    # Checked on every event, so replacing an approved image through a still valid presigned POST is caught too.
    if size > MAX_UPLOAD_BYTES or sniff_content_type(head) != item["content_type"]:
        logger.warning("Rejecting upload %s (size=%s)", key, size)
        repository.delete_image(item)
        storage.delete_object(key)
        return
    if item["status"] == PENDING and not repository.mark_available(item, size):
        logger.info("Upload already processed: %s", key)


@api_handler
def get_image(event, _context):
    caller = caller_id(event)
    item = repository.get_image(parse_image_id(event))
    if item is None or not visible_to(item, caller):
        raise ApiError(404, "not_found", "Image not found")
    if item["status"] != AVAILABLE:
        # Only the owner gets here: lets a client poll its upload. No URLs until the file is verified.
        return json_response(200, {**to_public(item), "status": item["status"]})
    return json_response(
        200,
        {
            **to_public(item),
            "status": item["status"],
            "view_url": storage.presign_download(item["s3_key"]),
            "download_url": storage.presign_download(item["s3_key"], _filename(item)),
            "url_expires_in": DOWNLOAD_URL_TTL_SECONDS,
        },
    )


@api_handler
def download_image(event, _context):
    item = _viewable_image(event)
    return response(302, headers={"Location": storage.presign_download(item["s3_key"], _filename(item))})


@api_handler
def list_images(event, _context):
    caller = caller_id(event)
    query = parse_list_query(event.get("queryStringParameters"))
    items, cursor = repository.list_images(caller, query)
    return json_response(
        200,
        {"items": [to_public(item) for item in items], "next_token": encode_next_token(cursor) if cursor else None},
    )


@api_handler
def delete_image(event, _context):
    caller = caller_id(event)
    item = repository.get_image(parse_image_id(event))
    if item is None or not visible_to(item, caller):
        raise ApiError(404, "not_found", "Image not found")
    if item["owner_id"] != caller:
        raise ApiError(403, "forbidden", "Only the owner can delete this image")
    # Record first: a failed S3 delete leaves an invisible orphan object, never a record pointing at nothing.
    repository.delete_image(item)
    try:
        storage.delete_object(item["s3_key"])
    except ClientError:
        # The image is already gone for every caller; only the bytes leak. Alarm on this line and sweep.
        logger.exception("Orphaned S3 object after delete: %s", item["s3_key"])
    return response(204)
