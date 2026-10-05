"""Lambda entry points. One function per API route plus the S3 upload processor.

Each handler parses the event, calls one use case in service, and shapes the response.
"""
import logging
import os
from urllib.parse import unquote_plus

from image_service import service
from image_service.config import DOWNLOAD_URL_TTL_SECONDS, UPLOAD_URL_TTL_SECONDS
from image_service.http import api_handler, caller_id, json_body, json_response, response
from image_service.models import Status
from image_service.validation import encode_next_token, parse_image_id, parse_list_query, parse_new_image

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

PUBLIC_FIELDS = (
    "image_id", "owner_id", "title", "description", "tags", "visibility", "content_type", "size_bytes", "created_at",
)


def to_public(item: dict) -> dict:
    out = {field: item.get(field) for field in PUBLIC_FIELDS}
    if out["size_bytes"] is not None:
        out["size_bytes"] = int(out["size_bytes"])  # DynamoDB returns Decimal
    return out


@api_handler
def create_image(event, _context):
    item, upload = service.create_image(caller_id(event), parse_new_image(json_body(event)))
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
    """S3 ObjectCreated handler."""
    for record in event.get("Records", []):
        service.process_upload(unquote_plus(record["s3"]["object"]["key"]))


@api_handler
def get_image(event, _context):
    item = service.get_image(caller_id(event), parse_image_id(event))
    body = {**to_public(item), "status": item["status"]}
    # No URLs until the file is verified; only the owner sees a non AVAILABLE image.
    if item["status"] == Status.AVAILABLE:
        body |= {
            "view_url": service.view_url(item),
            "download_url": service.download_url(item),
            "url_expires_in": DOWNLOAD_URL_TTL_SECONDS,
        }
    return json_response(200, body)


@api_handler
def download_image(event, _context):
    item = service.get_available_image(caller_id(event), parse_image_id(event))
    return response(302, headers={"Location": service.download_url(item)})


@api_handler
def list_images(event, _context):
    caller = caller_id(event)
    items, cursor = service.list_images(caller, parse_list_query(event.get("queryStringParameters")))
    return json_response(
        200,
        {"items": [to_public(item) for item in items], "next_token": encode_next_token(cursor) if cursor else None},
    )


@api_handler
def delete_image(event, _context):
    service.delete_image(caller_id(event), parse_image_id(event))
    return response(204)
