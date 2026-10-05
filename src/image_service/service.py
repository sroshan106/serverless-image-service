"""Use cases: access rules and orchestration over the repository and storage.

Handlers own the API Gateway event and response shapes; this module never sees them.
"""
import logging

from botocore.exceptions import ClientError

from image_service import repository, storage
from image_service.config import EXTENSIONS, MAX_UPLOAD_BYTES
from image_service.http import ApiError
from image_service.models import Status, Visibility
from image_service.validation import ListQuery, NewImage, is_uuid, sniff_content_type

logger = logging.getLogger(__name__)


def _not_found() -> ApiError:
    # ponytail: ApiError doubles as the domain error, as in validation; split out domain errors if a non HTTP caller appears.
    return ApiError(404, "not_found", "Image not found")


def visible_to(item: dict, caller: str) -> bool:
    return item["owner_id"] == caller or (item["visibility"] == Visibility.PUBLIC and item["status"] == Status.AVAILABLE)


def _filename(item: dict) -> str:
    return f"{item['image_id']}{EXTENSIONS[item['content_type']]}"


def create_image(owner_id: str, new: NewImage) -> tuple[dict, dict]:
    """PENDING record plus the presigned POST the client uploads the file with."""
    item = repository.create_pending(owner_id, new)
    return item, storage.presign_upload(item["s3_key"], new.content_type)


def get_image(caller: str, image_id: str) -> dict:
    """Any status for the owner (lets a client poll its upload); AVAILABLE public images for everyone else.

    404 (not 403) for other users' private images, so ids cannot be probed.
    """
    item = repository.get_image(image_id)
    if item is None or not visible_to(item, caller):
        raise _not_found()
    return item


def get_available_image(caller: str, image_id: str) -> dict:
    item = get_image(caller, image_id)
    if item["status"] != Status.AVAILABLE:
        raise _not_found()
    return item


def view_url(item: dict) -> str:
    return storage.presign_download(item["s3_key"])


def download_url(item: dict) -> str:
    return storage.presign_download(item["s3_key"], _filename(item))


def list_images(caller: str, query: ListQuery) -> tuple[list[dict], str | None]:
    return repository.list_images(caller, query)


def delete_image(caller: str, image_id: str) -> None:
    item = get_image(caller, image_id)
    if item["owner_id"] != caller:
        raise ApiError(403, "forbidden", "Only the owner can delete this image")
    # Record first: a failed S3 delete leaves an invisible orphan object, never a record pointing at nothing.
    repository.delete_image(item)
    try:
        storage.delete_object(item["s3_key"])
    except ClientError:
        # The image is already gone for every caller; only the bytes leak. Alarm on this line and sweep.
        logger.exception("Orphaned S3 object after delete: %s", item["s3_key"])


def process_upload(key: str) -> None:
    """Verify an uploaded object, then publish or reject it."""
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
    if item["status"] == Status.PENDING and not repository.mark_available(item, size):
        logger.info("Upload already processed: %s", key)
