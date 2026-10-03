import uuid

from helpers import PNG, api_event, body, create_pending, upload_image

from image_service import handlers

INTERNAL = {"pk", "sk", "s3_key", "title_lower", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "expires_at"}


def _get(image_id, user="bob"):
    return handlers.get_image(api_event(user=user, path_params={"image_id": image_id}), None)


def _download(image_id, user="bob"):
    return handlers.download_image(api_event(user=user, path_params={"image_id": image_id}), None)


def test_get_image_returns_metadata_and_inline_view_url():
    image_id = upload_image(title="Sunset", tags=["beach"], description="Goa")
    resp = _get(image_id)
    assert resp["statusCode"] == 200
    meta = body(resp)
    assert meta["image_id"] == image_id
    assert meta["owner_id"] == "alice"
    assert meta["title"] == "Sunset"
    assert meta["description"] == "Goa"
    assert meta["tags"] == ["beach"]
    assert meta["visibility"] == "public"
    assert meta["content_type"] == "image/jpeg"
    assert isinstance(meta["size_bytes"], int)
    assert meta["created_at"].endswith("Z")
    assert meta["status"] == "AVAILABLE"
    assert meta["url_expires_in"] == 300
    assert "X-Amz-Signature" in meta["view_url"]
    assert "attachment" not in meta["view_url"]
    assert "response-content-disposition=attachment" in meta["download_url"]
    assert f"{image_id}.jpg" in meta["download_url"]
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    assert not INTERNAL & set(meta)


def test_private_image_visible_only_to_owner():
    image_id = upload_image(visibility="private")
    assert _get(image_id, user="alice")["statusCode"] == 200
    assert _get(image_id, user="bob")["statusCode"] == 404


def test_owner_sees_pending_status_without_urls():
    item = create_pending()
    resp = _get(item["image_id"], user="alice")
    assert resp["statusCode"] == 200
    meta = body(resp)
    assert meta["status"] == "PENDING"
    assert "view_url" not in meta and "download_url" not in meta
    assert _get(item["image_id"], user="bob")["statusCode"] == 404


def test_download_of_pending_image_is_not_found_even_for_owner():
    item = create_pending()
    assert _download(item["image_id"], user="alice")["statusCode"] == 404


def test_unknown_or_malformed_id_is_not_found():
    assert _get(str(uuid.uuid4()))["statusCode"] == 404
    assert _get("nope")["statusCode"] == 404


def test_get_requires_user_header():
    image_id = upload_image()
    assert _get(image_id, user=None)["statusCode"] == 401


def test_download_redirects_to_attachment_url():
    image_id = upload_image(content_type="image/png", content=PNG)
    resp = _download(image_id)
    assert resp["statusCode"] == 302
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    location = resp["headers"]["Location"]
    assert "response-content-disposition=attachment" in location
    assert f"{image_id}.png" in location


def test_download_private_image_by_other_user_is_not_found():
    image_id = upload_image(visibility="private")
    assert _download(image_id, user="bob")["statusCode"] == 404
    assert _download(image_id, user="alice")["statusCode"] == 302
