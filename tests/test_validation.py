import base64
import json

import pytest

from image_service.http import ApiError
from image_service.validation import (
    ListQuery,
    encode_next_token,
    parse_image_id,
    parse_list_query,
    parse_new_image,
    sniff_content_type,
)

VALID = {"title": "  Sunset  ", "content_type": "image/jpeg"}
IMAGE_ID = "3f2b8c1e-8d1a-4c5e-9f00-0a1b2c3d4e5f"


def _raw_token(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def test_parse_new_image_applies_defaults_and_normalizes():
    image = parse_new_image({**VALID, "tags": ["Beach", "beach", " sun "]})
    assert image.title == "Sunset"
    assert image.description == ""
    assert image.tags == ["beach", "sun"]
    assert image.visibility == "public"
    assert image.content_type == "image/jpeg"


def test_parse_new_image_keeps_optional_fields():
    image = parse_new_image({**VALID, "description": " Goa ", "visibility": "private"})
    assert image.description == "Goa"
    assert image.visibility == "private"


@pytest.mark.parametrize(
    "body",
    [
        {"content_type": "image/jpeg"},
        {**VALID, "title": "   "},
        {**VALID, "title": 5},
        {**VALID, "title": "x" * 101},
        {**VALID, "description": "x" * 1001},
        {**VALID, "content_type": "application/pdf"},
        {**VALID, "content_type": ["image/jpeg"]},
        {"title": "Sunset"},
        {**VALID, "visibility": "friends"},
        {**VALID, "tags": "beach"},
        {**VALID, "tags": ["has space"]},
        {**VALID, "tags": [7]},
        {**VALID, "tags": ["x" * 33]},
        {**VALID, "tags": [f"t{i}" for i in range(11)]},
        {**VALID, "owner_id": "mallory"},
    ],
)
def test_parse_new_image_rejects_invalid(body):
    with pytest.raises(ApiError) as exc:
        parse_new_image(body)
    assert exc.value.status == 400


def test_parse_list_query_defaults():
    assert parse_list_query(None) == ListQuery()
    assert parse_list_query({}).limit == 20


def test_parse_list_query_normalizes_filters():
    q = parse_list_query(
        {
            "user_id": "alice",
            "tag": "Beach",
            "title": " SUN ",
            "visibility": "private",
            "limit": "5",
            "created_from": "2026-10-01",
            "created_to": "2026-10-02",
        }
    )
    assert q.user_id == "alice"
    assert q.tag == "beach"
    assert q.title == "sun"
    assert q.visibility == "private"
    assert q.limit == 5
    assert q.created_from == "2026-10-01T00:00:00.000000Z"
    assert q.created_to == "2026-10-02T23:59:59.999999Z"


def test_parse_list_query_converts_offsets_to_utc():
    q = parse_list_query({"created_from": "2026-10-01T05:30:00+05:30", "created_to": "2026-10-01T12:00:00Z"})
    assert q.created_from == "2026-10-01T00:00:00.000000Z"
    assert q.created_to == "2026-10-01T12:00:00.000000Z"


def test_next_token_round_trips_without_padding():
    cursor = f"2026-10-01T00:00:00.000000Z#{IMAGE_ID}"
    token = encode_next_token(cursor)
    assert "=" not in token
    assert parse_list_query({"next_token": token}).cursor == cursor


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"limit": "ten"},
        {"created_from": "yesterday"},
        {"created_from": "20261001"},
        {"created_to": "20261002"},
        {"created_from": "2026-10-02", "created_to": "2026-10-01"},
        {"visibility": "friends"},
        {"user_id": "bad id"},
        {"tag": "bad tag"},
        {"title": "   "},
        {"title": "x" * 101},
        {"sort": "asc"},
        {"next_token": "%%%"},
        {"next_token": _raw_token([1, 2])},
        {"next_token": _raw_token({})},
        {"next_token": _raw_token({"evil": "x"})},
        {"next_token": _raw_token({"pk": 1, "sk": "x"})},
        {"next_token": encode_next_token("2026-10-01T00:00:00.000000Z")},
        {"next_token": encode_next_token(f"2026-10-01T00:00:00.000000Z#{IMAGE_ID}" + "x" * 500)},
        {"next_token": encode_next_token(f"2026-10-01T00:00:00.000000Z#{IMAGE_ID.upper()}")},
        {"next_token": base64.urlsafe_b64encode(b"\xff\xfe").decode()},
        {"created_to": "9999-12-31T23:59:59-23:00"},
        {"created_from": "0001-01-01T00:00:00+23:00"},
    ],
)
def test_parse_list_query_rejects_invalid(params):
    with pytest.raises(ApiError) as exc:
        parse_list_query(params)
    assert exc.value.status == 400


def test_parse_image_id_accepts_canonical_uuid():
    assert parse_image_id({"pathParameters": {"image_id": IMAGE_ID}}) == IMAGE_ID


@pytest.mark.parametrize("params", [None, {}, {"image_id": "nope"}, {"image_id": IMAGE_ID.upper()}])
def test_parse_image_id_returns_404_for_unknown_shapes(params):
    with pytest.raises(ApiError) as exc:
        parse_image_id({"pathParameters": params})
    assert exc.value.status == 404


@pytest.mark.parametrize(
    "head, expected",
    [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\r", "image/png"),
        (b"GIF89a\x01\x00\x01\x00\x00\x00", "image/gif"),
        (b"GIF87a\x01\x00\x01\x00\x00\x00", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"%PDF-1.7\n%\xe2\xe3", None),
        (b"", None),
    ],
)
def test_sniff_content_type(head, expected):
    assert sniff_content_type(head) == expected
