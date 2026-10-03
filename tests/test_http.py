import base64
import json

import pytest

from image_service.http import ApiError, api_handler, caller_id, json_body, json_response, response


def test_json_response_serializes_body_and_merges_headers():
    resp = json_response(201, {"a": 1}, headers={"Location": "/x"})
    assert resp == {
        "statusCode": 201,
        "headers": {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json", "Location": "/x"},
        "body": '{"a": 1}',
    }


def test_every_response_carries_configured_cors_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGIN", "https://app.example.com")
    assert response(204) == {
        "statusCode": 204,
        "headers": {"Access-Control-Allow-Origin": "https://app.example.com"},
        "body": "",
    }
    assert json_response(400, {})["headers"]["Access-Control-Allow-Origin"] == "https://app.example.com"


def test_caller_id_reads_header_case_insensitively():
    assert caller_id({"headers": {"x-user-id": "alice_1"}}) == "alice_1"
    assert caller_id({"headers": {"X-User-Id": "bob-2"}}) == "bob-2"


@pytest.mark.parametrize(
    "headers",
    [None, {}, {"X-User-Id": ""}, {"X-User-Id": "a/b"}, {"X-User-Id": "../x"}, {"X-User-Id": "x" * 65}],
)
def test_caller_id_rejects_missing_or_unsafe_ids(headers):
    with pytest.raises(ApiError) as exc:
        caller_id({"headers": headers})
    assert exc.value.status == 401


def test_json_body_parses_plain_and_base64_bodies():
    assert json_body({"body": '{"a": 1}'}) == {"a": 1}
    encoded = base64.b64encode(b'{"a": 2}').decode()
    assert json_body({"body": encoded, "isBase64Encoded": True}) == {"a": 2}


@pytest.mark.parametrize(
    "event",
    [{"body": None}, {"body": "not json"}, {"body": "[1, 2]"}, {"body": "!!", "isBase64Encoded": True}],
)
def test_json_body_rejects_invalid_bodies(event):
    with pytest.raises(ApiError) as exc:
        json_body(event)
    assert exc.value.status == 400


def test_api_handler_maps_api_errors_and_hides_unexpected_ones():
    @api_handler
    def bad_request(event, context):
        raise ApiError(400, "validation_error", "nope")

    @api_handler
    def boom(event, context):
        raise RuntimeError("secret detail")

    resp = bad_request({}, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"]) == {"error": {"code": "validation_error", "message": "nope"}}

    resp = boom({}, None)
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"]["code"] == "internal_error"
    assert "secret detail" not in resp["body"]
