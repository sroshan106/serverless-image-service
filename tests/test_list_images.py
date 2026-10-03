from datetime import datetime

import pytest
from helpers import api_event, body, upload_image

from image_service import config, handlers, repository
from image_service.validation import decode_next_token


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


def walk(user, **query):
    """Every page of a query: (all item ids, every cursor handed out)."""
    found, cursors, token = [], [], None
    while True:
        page = body(list_as(user, **query, **({"next_token": token} if token else {})))
        found += [item["image_id"] for item in page["items"]]
        token = page["next_token"]
        if token is None:
            return found, cursors
        cursors.append(decode_next_token(token))


def test_owner_listing_merges_public_and_private_pages(gallery):
    g = gallery
    found, _ = walk("alice", user_id="alice", limit="1")
    assert found == [g["alice_private"], g["alice_beach"]]


def test_tag_listing_pages_across_public_and_own_private(gallery, monkeypatch):
    g = gallery
    monkeypatch.setattr(repository, "_now", lambda: datetime.fromisoformat("2026-10-05T10:00:00+00:00"))
    own_private_beach = upload_image(user="carol", tags=["beach"], visibility="private")
    found, _ = walk("carol", tag="beach", limit="1")
    assert found == [own_private_beach, g["bob_beach"], g["alice_beach"]]
    assert walk("bob", tag="beach", limit="1")[0] == [g["bob_beach"], g["alice_beach"]]


def test_cursor_never_points_at_hidden_private_image(gallery):
    # alice_private is newer than alice_beach, so a filter based design would stop on it first.
    found, cursors = walk("bob", user_id="alice", limit="1")
    assert found == [gallery["alice_beach"]]
    assert not any(gallery["alice_private"] in cursor for cursor in cursors)
    found, cursors = walk("bob", tag="family", limit="1")
    assert found == [] and cursors == []


def test_filtered_page_is_filled_across_query_rounds(gallery):
    # The only match is the oldest public image; one call still returns it.
    page = body(list_as("carol", title="beach", limit="1"))
    assert [item["image_id"] for item in page["items"]] == [gallery["alice_beach"]]


def test_round_budget_returns_cursor_to_continue(gallery, monkeypatch):
    monkeypatch.setattr(config, "MAX_QUERY_ROUNDS", 1)
    first = body(list_as("carol", title="beach", limit="1"))
    assert first["items"] == [] and first["next_token"]
    assert walk("carol", title="beach", limit="1")[0] == [gallery["alice_beach"]]


def test_next_token_reused_with_other_filters_is_a_position_not_an_error(gallery):
    g = gallery
    token = body(list_as("carol", limit="1"))["next_token"]  # cursor at bob_city (2026-10-04)
    assert ids(list_as("carol", tag="beach", next_token=token)) == [g["bob_beach"], g["alice_beach"]]
    # Cursor below created_from: empty, not a DynamoDB error.
    assert ids(list_as("carol", created_from="2026-10-05", next_token=token)) == []
    assert ids(list_as("carol", created_to="2026-10-02", next_token=token)) == [g["alice_beach"]]


def test_other_users_private_listing_is_empty(gallery):
    assert ids(list_as("bob", user_id="alice", visibility="private")) == []
    assert ids(list_as("bob", tag="family", user_id="alice", visibility="private")) == []


def test_list_items_hide_internal_fields(gallery):
    item = body(list_as("carol", tag="beach"))["items"][0]
    assert set(item) == set(handlers.PUBLIC_FIELDS)


def test_invalid_query_returns_400():
    assert list_as("carol", limit="0")["statusCode"] == 400


def test_list_requires_user_header():
    assert handlers.list_images(api_event(user=None), None)["statusCode"] == 401
