"""Lambda entry points. One function per API route plus the S3 upload processor."""
import logging
import os

from image_service import repository, storage
from image_service.config import UPLOAD_URL_TTL_SECONDS
from image_service.http import api_handler, caller_id, json_body, json_response
from image_service.validation import parse_new_image

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
