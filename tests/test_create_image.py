import base64
import json

from helpers import api_event, body

from image_service import handlers, repository, storage


def _create(user="alice", **fields):
    payload = {"title": "Sunset", "content_type": "image/png", **fields}
    return handlers.create_image(api_event("POST", user=user, body=payload), None)


def test_create_image_stores_pending_record_and_returns_presigned_post():
    resp = _create(tags=["Beach"], description="Goa")
    assert resp["statusCode"] == 201
    created = body(resp)
    image_id = created["image_id"]
    assert resp["headers"]["Location"] == f"/images/{image_id}"
    assert created["status"] == "PENDING"
    assert created["upload"]["expires_in"] == 300

    fields = created["upload"]["fields"]
    assert fields["key"] == f"images/alice/{image_id}"
    assert fields["Content-Type"] == "image/png"
    policy = json.loads(base64.b64decode(fields["policy"]))
    assert ["content-length-range", 1, 10 * 1024 * 1024] in policy["conditions"]
    assert {"Content-Type": "image/png"} in policy["conditions"]

    item = repository.get_image(image_id)
    assert item["status"] == "PENDING"
    assert item["owner_id"] == "alice"
    assert item["title_lower"] == "sunset"
    assert item["tags"] == ["beach"]
    assert item["description"] == "Goa"
    assert item["s3_key"] == f"images/alice/{image_id}"
    assert "expires_at" in item
    assert "gsi1pk" not in item and "gsi2pk" not in item


def test_create_image_uses_public_endpoint_when_configured(monkeypatch):
    monkeypatch.setenv("PUBLIC_S3_ENDPOINT", "http://localhost.localstack.cloud:4566")
    storage._presign_s3.cache_clear()
    url = body(_create())["upload"]["url"]
    assert url.startswith("http://localhost.localstack.cloud:4566/images-test-bucket")


def test_create_image_requires_user_header():
    assert _create(user=None)["statusCode"] == 401


def test_create_image_rejects_invalid_body():
    resp = _create(content_type="application/pdf")
    assert resp["statusCode"] == 400
    assert body(resp)["error"]["code"] == "validation_error"


def test_create_image_rejects_non_json_body():
    event = api_event("POST")
    event["body"] = "not json"
    assert handlers.create_image(event, None)["statusCode"] == 400
