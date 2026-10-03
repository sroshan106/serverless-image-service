from urllib.parse import quote_plus

import pytest
from botocore.exceptions import ClientError
from helpers import JPEG, PNG, create_pending, object_exists, put_object, s3_event, table, upload_image

from image_service import handlers, repository


def _process(key):
    handlers.process_upload(s3_event(key), None)


def _sort_key(item):
    return f"{item['created_at']}#{item['image_id']}"


def test_valid_upload_becomes_available_and_indexed():
    item = create_pending(tags=["beach", "sun"])
    put_object(item["s3_key"], JPEG)
    _process(item["s3_key"])

    stored = repository.get_image(item["image_id"])
    assert stored["status"] == "AVAILABLE"
    assert stored["size_bytes"] == len(JPEG)
    assert "expires_at" not in stored
    assert stored["gsi1pk"] == "USER#alice#public"
    assert stored["gsi1sk"] == _sort_key(item)
    assert stored["gsi2pk"] == "PUBLIC"
    assert stored["gsi2sk"] == _sort_key(item)
    for tag in ("beach", "sun"):
        copy = table().get_item(Key={"pk": f"TAG#{tag}", "sk": _sort_key(item)})["Item"]
        assert copy["image_id"] == item["image_id"]
        assert copy["visibility"] == "public"
        assert copy["status"] == "AVAILABLE"


def test_private_upload_is_kept_out_of_public_partitions():
    item = create_pending(visibility="private", tags=["family"])
    put_object(item["s3_key"], JPEG)
    _process(item["s3_key"])
    stored = repository.get_image(item["image_id"])
    assert stored["status"] == "AVAILABLE"
    assert stored["gsi1pk"] == "USER#alice#private"
    assert "gsi2pk" not in stored
    assert "Item" in table().get_item(Key={"pk": "TAG#family#alice", "sk": _sort_key(item)})
    assert "Item" not in table().get_item(Key={"pk": "TAG#family", "sk": _sort_key(item)})


def test_url_encoded_event_key_is_decoded():
    item = create_pending()
    put_object(item["s3_key"], JPEG)
    handlers.process_upload(s3_event(quote_plus(item["s3_key"])), None)
    assert repository.get_image(item["image_id"])["status"] == "AVAILABLE"


def test_empty_object_is_rejected():
    item = create_pending()
    put_object(item["s3_key"], b"")
    _process(item["s3_key"])
    assert repository.get_image(item["image_id"]) is None
    assert not object_exists(item["s3_key"])


def test_cancelled_publish_while_still_pending_is_retried(monkeypatch):
    item = create_pending()
    client = repository._table().meta.client

    def cancelled(**_kwargs):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(client, "transact_write_items", cancelled)
    with pytest.raises(ClientError):
        repository.mark_available(item, 10)  # still PENDING: throttle or conflict, so Lambda must retry


def test_duplicate_event_is_ignored():
    item = create_pending(tags=["beach"])
    put_object(item["s3_key"], JPEG)
    _process(item["s3_key"])
    _process(item["s3_key"])
    assert repository.get_image(item["image_id"])["status"] == "AVAILABLE"


@pytest.mark.parametrize("content", [b"%PDF-1.7 not an image at all", PNG])
def test_content_not_matching_declared_type_is_rejected(content):
    item = create_pending(content_type="image/jpeg")
    put_object(item["s3_key"], content)
    _process(item["s3_key"])
    assert repository.get_image(item["image_id"]) is None
    assert not object_exists(item["s3_key"])


def test_oversized_upload_is_rejected(monkeypatch):
    monkeypatch.setattr(handlers, "MAX_UPLOAD_BYTES", 10)
    item = create_pending()
    put_object(item["s3_key"], JPEG)
    _process(item["s3_key"])
    assert repository.get_image(item["image_id"]) is None
    assert not object_exists(item["s3_key"])


@pytest.mark.parametrize(
    "key", ["images/alice/3f2b8c1e-8d1a-4c5e-9f00-0a1b2c3d4e5f", "images/alice/not-a-uuid"]
)
def test_orphan_object_without_record_is_deleted(key):
    put_object(key, JPEG)
    _process(key)
    assert not object_exists(key)


def test_object_under_other_users_prefix_is_deleted():
    item = create_pending(user="alice")
    forged_key = f"images/mallory/{item['image_id']}"
    put_object(forged_key, JPEG)
    _process(forged_key)
    assert not object_exists(forged_key)
    assert repository.get_image(item["image_id"])["status"] == "PENDING"


def test_event_for_object_already_deleted_is_ignored():
    item = create_pending()
    _process(item["s3_key"])  # no object in the bucket: deleted between the event and processing
    assert repository.get_image(item["image_id"])["status"] == "PENDING"


def test_reupload_of_invalid_content_after_approval_removes_image():
    image_id = upload_image(tags=["beach"])
    item = repository.get_image(image_id)
    put_object(item["s3_key"], b"not an image anymore")
    _process(item["s3_key"])
    assert repository.get_image(image_id) is None
    assert "Item" not in table().get_item(Key={"pk": "TAG#beach", "sk": _sort_key(item)})
    assert not object_exists(item["s3_key"])


def test_mark_available_returns_false_when_already_available():
    item = create_pending()
    assert repository.mark_available(item, 10) is True
    assert repository.mark_available(item, 10) is False


def test_mark_available_after_delete_does_not_resurrect():
    item = create_pending(tags=["beach"])
    repository.delete_image(item)
    assert repository.mark_available(item, 10) is False
    assert repository.get_image(item["image_id"]) is None
    assert "Item" not in table().get_item(Key={"pk": "TAG#beach", "sk": _sort_key(item)})
