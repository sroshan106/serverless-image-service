import uuid

from botocore.exceptions import ClientError
from helpers import api_event, create_pending, object_exists, table, upload_image

from image_service import handlers, repository, storage


def _delete(image_id, user):
    return handlers.delete_image(api_event("DELETE", user=user, path_params={"image_id": image_id}), None)


def test_owner_deletes_record_tag_copies_and_object():
    image_id = upload_image(tags=["beach"])
    item = repository.get_image(image_id)
    resp = _delete(image_id, "alice")
    assert resp["statusCode"] == 204
    assert resp["body"] == ""
    assert repository.get_image(image_id) is None
    sort_key = f"{item['created_at']}#{image_id}"
    assert "Item" not in table().get_item(Key={"pk": "TAG#beach", "sk": sort_key})
    assert not object_exists(item["s3_key"])


def test_non_owner_gets_403_for_public_image():
    image_id = upload_image()
    assert _delete(image_id, "bob")["statusCode"] == 403
    assert repository.get_image(image_id) is not None


def test_non_owner_gets_404_for_private_image():
    image_id = upload_image(visibility="private")
    assert _delete(image_id, "bob")["statusCode"] == 404
    assert repository.get_image(image_id) is not None


def test_owner_can_delete_pending_image():
    item = create_pending()
    assert _delete(item["image_id"], "alice")["statusCode"] == 204
    assert repository.get_image(item["image_id"]) is None


def test_non_owner_cannot_see_pending_image():
    item = create_pending()
    assert _delete(item["image_id"], "bob")["statusCode"] == 404


def test_delete_unknown_or_malformed_id_is_404():
    assert _delete(str(uuid.uuid4()), "alice")["statusCode"] == 404
    assert _delete("nope", "alice")["statusCode"] == 404


def test_delete_requires_user_header():
    image_id = upload_image()
    assert _delete(image_id, None)["statusCode"] == 401


def test_owner_deletes_private_tag_copies():
    image_id = upload_image(visibility="private", tags=["family"])
    item = repository.get_image(image_id)
    assert _delete(image_id, "alice")["statusCode"] == 204
    assert "Item" not in table().get_item(Key={"pk": "TAG#family#alice", "sk": f"{item['created_at']}#{image_id}"})


def test_s3_failure_after_record_delete_still_returns_204(monkeypatch, caplog):
    image_id = upload_image()

    def fail(_key):
        raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")

    monkeypatch.setattr(storage, "delete_object", fail)
    assert _delete(image_id, "alice")["statusCode"] == 204
    assert repository.get_image(image_id) is None
    assert "Orphaned S3 object" in caplog.text
