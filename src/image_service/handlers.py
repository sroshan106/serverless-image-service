"""Lambda entry points. One function per API route plus the S3 upload processor."""
import logging
import os
from urllib.parse import unquote_plus

from image_service import repository, storage
from image_service.config import MAX_UPLOAD_BYTES, UPLOAD_URL_TTL_SECONDS
from image_service.http import api_handler, caller_id, json_body, json_response
from image_service.repository import PENDING
from image_service.validation import is_uuid, parse_new_image, sniff_content_type

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


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
