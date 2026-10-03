from datetime import datetime

import pytest
from helpers import api_event, body, upload_image

from image_service import handlers, repository


@pytest.fixture
def gallery(monkeypatch):
    def make(when, **fields):
        monkeypatch.setattr(repository, "_now", lambda: datetime.fromisoformat(when))
        return upload_image(**fields)

    return {
        "alice_beach": make("2026-10-01T10:00:00+00:00", user="alice", title="Beach Sunset", tags=["beach", "sunset"]),
        "alice_private": make(
            "2026-10-02T10:00:00+00:00", user="alice", title="Family dinner", tags=["family"], visibility="private"
        ),
        "bob_beach": make("2026-10-03T10:00:00+00:00", user="bob", title="Surf day", tags=["beach"]),
        "bob_city": make("2026-10-04T10:00:00+00:00", user="bob", title="City lights", tags=["city"]),
    }


def list_as(user, **query):
    return handlers.list_images(api_event(user=user, query=query or None), None)


def ids(resp):
    assert resp["statusCode"] == 200, resp["body"]
    return [item["image_id"] for item in body(resp)["items"]]


def test_unfiltered_list_returns_public_images_newest_first(gallery):
    g = gallery
    assert ids(list_as("carol")) == [g["bob_city"], g["bob_beach"], g["alice_beach"]]


def test_filter_by_user_shows_private_only_to_owner(gallery):
    g = gallery
    assert ids(list_as("alice", user_id="alice")) == [g["alice_private"], g["alice_beach"]]
    assert ids(list_as("bob", user_id="alice")) == [g["alice_beach"]]


def test_filter_by_tag(gallery):
    g = gallery
    assert ids(list_as("carol", tag="Beach")) == [g["bob_beach"], g["alice_beach"]]


def test_filter_by_tag_and_user(gallery):
    assert ids(list_as("carol", tag="beach", user_id="bob")) == [gallery["bob_beach"]]


def test_private_tag_hidden_from_others(gallery):
    assert ids(list_as("bob", tag="family")) == []
    assert ids(list_as("alice", tag="family")) == [gallery["alice_private"]]


def test_filter_by_date_range(gallery):
    assert ids(list_as("carol", created_from="2026-10-02", created_to="2026-10-03")) == [gallery["bob_beach"]]


def test_filter_by_date_range_within_tag(gallery):
    assert ids(list_as("carol", tag="beach", created_to="2026-10-02")) == [gallery["alice_beach"]]


def test_filter_by_title_contains_case_insensitive(gallery):
    assert ids(list_as("carol", title="SUN")) == [gallery["alice_beach"]]


def test_visibility_private_lists_callers_own_private_images(gallery):
    assert ids(list_as("alice", visibility="private")) == [gallery["alice_private"]]
    assert ids(list_as("bob", visibility="private")) == []


def test_visibility_public_with_user(gallery):
    assert ids(list_as("alice", user_id="alice", visibility="public")) == [gallery["alice_beach"]]


def test_pagination_walks_all_pages(gallery):
    g = gallery
    first = body(list_as("carol", limit="2"))
    assert len(first["items"]) == 2
    assert first["next_token"]
    second = body(list_as("carol", limit="2", next_token=first["next_token"]))
    assert [i["image_id"] for i in first["items"] + second["items"]] == [g["bob_city"], g["bob_beach"], g["alice_beach"]]
    assert second["next_token"] is None


def test_next_token_from_other_query_is_rejected(gallery):
    feed_token = body(list_as("carol", limit="1"))["next_token"]
    assert list_as("carol", tag="beach", next_token=feed_token)["statusCode"] == 400
    alice_token = body(list_as("alice", user_id="alice", limit="1"))["next_token"]
    assert list_as("carol", user_id="bob", next_token=alice_token)["statusCode"] == 400


def test_list_items_hide_internal_fields(gallery):
    item = body(list_as("carol", tag="beach"))["items"][0]
    assert set(item) == set(handlers.PUBLIC_FIELDS)


def test_invalid_query_returns_400():
    assert list_as("carol", limit="0")["statusCode"] == 400


def test_list_requires_user_header():
    assert handlers.list_images(api_event(user=None), None)["statusCode"] == 401
