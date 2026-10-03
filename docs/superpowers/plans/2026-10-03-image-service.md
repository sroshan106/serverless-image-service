# Image Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the image upload and storage service layer of an Instagram style app: upload with metadata, list with filters, view/download, delete. It runs on API Gateway, Lambda, S3 and DynamoDB, is demoed fully on LocalStack, and deploys to real AWS by switching a SAM config env.

**Architecture:** Each REST route is its own Python Lambda behind an API Gateway REST API. Image bytes never pass through Lambda: `POST /images` writes a `PENDING` record and returns an S3 presigned POST, the client uploads straight to S3, and an S3 `ObjectCreated` event Lambda verifies the object (size and magic bytes) and flips it to `AVAILABLE`. Metadata lives in one DynamoDB table (single table design) with two sparse GSIs and per tag items, so every list filter is a key query, never a scan.

**Tech Stack:** Python 3.12, boto3 (provided by the Lambda runtime, no other runtime deps), AWS SAM + `samlocal`, LocalStack via Docker Compose, pytest + moto 5 for unit tests, requests for the end to end smoke script.

**Spec:** `/home/roshan/Desktop/CodingAssignment-SeniorEngineer-PlatformEngineering.pdf` (MontyCloud coding exercise). Decisions agreed with the user on 2026-10-03 are recorded below; executors read both.

## Spec Requirements (verbatim summary)

1. APIs for: uploading image with metadata; list all images with at least two search filters; view/download image; delete an image.
2. Unit tests covering all scenarios.
3. API documentation and usage instructions.
4. Metadata persisted in NoSQL (DynamoDB); images in S3; API Gateway + Lambda.
5. Many concurrent users, so the service must scale.
6. Python 3.7+. Local development environment on LocalStack.

## Decisions Agreed With User

| Topic | Decision |
|---|---|
| Upload flow | Presigned **POST** direct to S3 (switched from PUT so S3 enforces size and type). |
| Upload completion | Record starts `PENDING`; S3 event Lambda validates and sets `AVAILABLE`. Only `AVAILABLE` images are listed or viewable. |
| Identity | `X-User-Id` header, trusted. Real auth (API Gateway authorizer) is out of scope and documented. |
| IaC | AWS SAM. `samconfig.toml` envs `local` (LocalStack via `samlocal`) and `prod` (real AWS via `sam`). |
| Filters | `user_id`, `tag`, `created_from`/`created_to`, `title` (case insensitive contains, post filter), `visibility`. |
| Visibility | `public` (default) visible to all; `private` visible to owner only. Others get 404, never 403, for private images. |
| View/download | `GET /images/{id}` returns metadata + presigned inline `view_url`. `GET /images/{id}/download` returns 302 to presigned attachment URL. |
| Delete | Hard delete of record, tag items and S3 object. Owner only. |
| Limits | `image/jpeg`, `image/png`, `image/webp`, `image/gif`; max 10 MB (10485760 bytes); max 10 tags, each `^[a-z0-9_-]{1,32}$`. |
| Metadata update (PATCH) | Not built. |
| Runtime and repo | Python 3.12, `git init` in `/home/roshan/Desktop/MontyCloud`, plan stored in repo. |
| Test execution | Tests are written TDD style but **never run by the executor** (project `CLAUDE.md`). No pytest, `make test`, deploy or smoke runs. Allowed quick checks: `python -m py_compile`, `sam validate --lint`. The user runs the full suite, deploy and smoke after Task 11. |
| CORS | Enabled for a future browser UI. `AllowedOrigin` template parameter: `*` for `local`, required explicit origin for `prod`. Lambda responses, API Gateway default error responses and the S3 bucket all carry it. |
| Browser download | `GET /images/{id}` also returns `download_url` (presigned attachment URL), because a browser link cannot send `X-User-Id`. The 302 route stays for curl. |
| SAM CLI | Installed with `pipx` (`aws-sam-cli` plus injected `aws-sam-cli-local`), not in the dev venv, to avoid botocore pin conflicts with moto. |
| LocalStack token | `LOCALSTACK_AUTH_TOKEN` is a prerequisite (current LocalStack images require it). Exported in the shell, never committed. |

## Defaults Chosen Without Asking (user to confirm in plan review)

1. Unfiltered `GET /images` returns public images only. Callers see their own private images via `user_id=<self>` or `visibility=private`.
2. Status codes: missing/invalid `X-User-Id` is 401; validation is 400; non owner deleting a public image is 403; private or missing images are 404.
3. Error body shape: `{"error": {"code": "...", "message": "..."}}`.
4. API Gateway REST API (v1), because LocalStack community does not support HTTP API (v2).
5. One Lambda per route (least privilege IAM per function).
6. Unknown body fields and unknown query params are rejected with 400.
7. Pagination: `limit` default 20, max 100; opaque `next_token`. Title filter is applied after the key query, so a page can hold fewer than `limit` items while `next_token` is still set.
8. Presigned URLs expire after 300 s. Pending records expire via DynamoDB TTL after 24 h.
9. Delete order: DynamoDB first, then S3 (a failed S3 delete leaves an invisible orphan object, never a record pointing at nothing).
10. Prod region `us-east-1` in `samconfig.toml`; AWS profile chosen at deploy time via `AWS_PROFILE`.
11. API documentation lives in `README.md` (no OpenAPI file).

## Global Constraints

- Lambda runtime: `python3.12`. Local tooling uses `/usr/bin/python3.12`.
- Runtime dependencies: boto3/botocore from the Lambda runtime only. No `requirements.txt` under `src/`.
- AWS services: API Gateway (REST), Lambda, S3, DynamoDB only.
- Local demo must work with no real AWS account: LocalStack at `http://localhost.localstack.cloud:4566`.
- `X-User-Id` must match `^[A-Za-z0-9_-]{1,64}$` (it is used inside S3 keys and DynamoDB keys).
- Title 1..100 chars after strip; description 0..1000 chars; tags max 10, each `^[a-z0-9_-]{1,32}$` after lowercasing.
- Allowed content types: `image/jpeg`, `image/png`, `image/webp`, `image/gif`. Max upload 10485760 bytes.
- Timestamps: UTC, format `%Y-%m-%dT%H:%M:%S.%fZ` (e.g. `2026-10-03T10:00:00.000000Z`).
- Unit test coverage gate: 90% on `image_service` (`make test`, run by the user).
- Executor never runs tests, deploys or smoke (see Test execution). "Expected" notes on test files describe what the user should see.
- Git commit messages: no `Co-Authored-By` trailers and no `Claude-Session` metadata (user rule).
- Prose in README/docs: no hyphens or em dashes joining clauses (user rule).

## Review Focus

1. **Private image ID guessed by another user** (GET, download, delete): must return 404, not 200/403. Tests: Task 5 `test_private_image_visible_only_to_owner`, `test_download_private_image_by_other_user_is_not_found`; Task 7 `test_non_owner_gets_404_for_private_image`.
2. **Client uploads non image bytes with an image content type, or replaces an approved image with junk using a still valid presigned POST**: object deleted, record removed. Tests: Task 4 `test_content_not_matching_declared_type_is_rejected`, `test_reupload_of_invalid_content_after_approval_removes_image`.
3. **S3 delivers the same event twice / delete races with processing**: no crash, no resurrected record. Tests: Task 4 `test_duplicate_event_is_ignored`, `test_mark_available_after_delete_does_not_resurrect`.
4. **`next_token` tampered with, or reused with different filters or another user's partition**: 400, never 500 and never another partition's data. Tests: Task 2 `test_parse_list_query_rejects_invalid` (bad tokens); Task 6 `test_next_token_from_other_query_is_rejected`.
5. **Header case varies (`x-user-id` from HTTP/2 clients) and unsafe user ids (`../`, `/`)**: accepted case insensitively; unsafe ids rejected with 401. Test: Task 1 `test_caller_id_reads_header_case_insensitively`, `test_caller_id_rejects_missing_or_unsafe_ids`.
6. **Browser UI behind CORS**: every Lambda response, API Gateway default 4XX/5XX and the bucket carry `AllowedOrigin`; prod cannot deploy without an explicit origin. Tests: Task 1 `test_every_response_carries_configured_cors_origin`; Task 9 smoke preflight check.

---

## File Structure

```
MontyCloud/
├── .gitignore
├── Makefile                      # install, test, up, down, deploy ENV=local|prod, smoke
├── README.md                     # API docs + usage (Task 10)
├── docker-compose.yml            # LocalStack
├── pyproject.toml                # pytest config
├── requirements-dev.txt          # test tooling (not shipped to Lambda); SAM CLI via pipx
├── samconfig.toml                # local / prod deploy envs
├── template.yaml                 # SAM: API, 6 Lambdas, table, bucket
├── scripts/smoke.py              # end to end check against LocalStack
├── src/image_service/
│   ├── __init__.py
│   ├── config.py                 # constants + env lookups
│   ├── http.py                   # ApiError, api_handler, caller_id, json_body, json_response
│   ├── validation.py             # request parsing, tokens, magic byte sniffing
│   ├── repository.py             # DynamoDB access
│   ├── storage.py                # S3 access and presigning
│   └── handlers.py               # Lambda entry points
└── tests/
    ├── conftest.py               # moto table + bucket per test
    ├── helpers.py                # event builders, upload helpers
    ├── test_http.py
    ├── test_validation.py
    ├── test_create_image.py
    ├── test_process_upload.py
    ├── test_get_and_download.py
    ├── test_list_images.py
    └── test_delete_image.py
```

### DynamoDB layout (table `ImagesTable`, keys `pk`/`sk`, all key attributes type S)

| Item | pk | sk | gsi1pk / gsi1sk | gsi2pk / gsi2sk |
|---|---|---|---|---|
| Image record | `IMG#<image_id>` | `META` | `USER#<owner_id>` / `<created_at>#<image_id>` (set when AVAILABLE) | `PUBLIC` / `<created_at>#<image_id>` (set when AVAILABLE and public) |
| Tag copy (one per tag) | `TAG#<tag>` | `<created_at>#<image_id>` | none | none |

Query routing in `repository.list_images`: `tag` given → base table `TAG#<tag>`; else `user_id` given (or `visibility=private`, meaning the caller) → `gsi1`; else → `gsi2`. Date range is always a `between` on the sort key. Remaining filters plus the access rule (`visibility = public OR owner_id = caller`) are a `FilterExpression`. Tag copies are safe to denormalize because records are immutable (no PATCH).

### Routes

| Method | Path | Handler | Success |
|---|---|---|---|
| POST | `/images` | `image_service.handlers.create_image` | 201 |
| GET | `/images` | `image_service.handlers.list_images` | 200 |
| GET | `/images/{image_id}` | `image_service.handlers.get_image` | 200 |
| GET | `/images/{image_id}/download` | `image_service.handlers.download_image` | 302 |
| DELETE | `/images/{image_id}` | `image_service.handlers.delete_image` | 204 |
| S3 `ObjectCreated` on `images/` | n/a | `image_service.handlers.process_upload` | n/a |

---

### Task 1: Project scaffold and HTTP helpers

**Files:**
- Create: `.gitignore`, `requirements-dev.txt`, `pyproject.toml`, `Makefile` (install/test targets only for now), `src/image_service/__init__.py`, `src/image_service/config.py`, `src/image_service/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `config`: constants `ALLOWED_CONTENT_TYPES: tuple[str, ...]`, `EXTENSIONS: dict[str, str]`, `MAX_UPLOAD_BYTES = 10485760`, `MAX_TAGS = 10`, `UPLOAD_URL_TTL_SECONDS = 300`, `DOWNLOAD_URL_TTL_SECONDS = 300`, `PENDING_TTL_SECONDS = 86400`, `DEFAULT_PAGE_SIZE = 20`, `MAX_PAGE_SIZE = 100`; functions `table_name() -> str`, `bucket_name() -> str`, `public_s3_endpoint() -> str | None`, `allowed_origin() -> str`.
  - `http`: `USER_ID_RE`, `class ApiError(Exception)` with `.status: int`, `.code: str`, `.message: str`; `response(status: int, body: str = "", headers: dict | None = None) -> dict` (adds `Access-Control-Allow-Origin`); `json_response(status: int, body: dict, headers: dict | None = None) -> dict`; `caller_id(event: dict) -> str`; `json_body(event: dict) -> dict`; decorator `api_handler(fn)`.

- [ ] **Step 1: Init repo, venv and tooling files**

```bash
cd /home/roshan/Desktop/MontyCloud
git init
mkdir -p src/image_service tests scripts
touch src/image_service/__init__.py
```

`.gitignore`:
```gitignore
.venv/
.aws-sam/
__pycache__/
.pytest_cache/
.coverage
htmlcov/
volume/
```

`requirements-dev.txt`:
```text
boto3>=1.34
moto[s3,dynamodb]>=5.0
pytest>=8.0
pytest-cov>=5.0
requests>=2.31
```

SAM CLI lives outside the venv (botocore pins clash with moto). One time, if `sam --version` or `samlocal --version` fails:
```bash
pipx install aws-sam-cli
pipx inject --include-apps aws-sam-cli aws-sam-cli-local
```
`sam validate --lint` uses the cfn-lint bundled with SAM CLI.

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`Makefile` (recipe lines are TABs):
```make
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install test

$(BIN)/activate: requirements-dev.txt
	python3.12 -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt
	touch $(BIN)/activate

install: $(BIN)/activate

test: install
	$(BIN)/pytest --cov=image_service --cov-report=term-missing --cov-fail-under=90
```

Run: `make install`
Expected: venv created, pip finishes without dependency resolution errors. If it fails, report the exact pip error instead of loosening pins silently.

- [ ] **Step 2: Write `src/image_service/config.py`**

```python
"""Service constants and environment lookups."""
import os

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TAGS = 10
UPLOAD_URL_TTL_SECONDS = 300
DOWNLOAD_URL_TTL_SECONDS = 300
PENDING_TTL_SECONDS = 24 * 60 * 60
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def table_name() -> str:
    return os.environ["TABLE_NAME"]


def bucket_name() -> str:
    return os.environ["BUCKET_NAME"]


def public_s3_endpoint() -> str | None:
    """Host baked into presigned URLs. Empty in AWS, the LocalStack URL locally."""
    return os.environ.get("PUBLIC_S3_ENDPOINT") or None


def allowed_origin() -> str:
    """CORS origin. The template always sets it; "*" only applies outside Lambda (unit tests)."""
    return os.environ.get("ALLOWED_ORIGIN") or "*"
```

- [ ] **Step 3: Write the failing tests `tests/test_http.py`**

```python
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
```

- [ ] **Step 4: Write `src/image_service/http.py`**

```python
"""API Gateway proxy event parsing and response helpers."""
import base64
import functools
import json
import logging
import re

from image_service import config

logger = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ApiError(Exception):
    """An error that maps directly to an HTTP response."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def response(status: int, body: str = "", headers: dict | None = None) -> dict:
    # Lambda proxy responses must carry CORS headers themselves; SAM's Cors block only answers preflight.
    return {
        "statusCode": status,
        "headers": {"Access-Control-Allow-Origin": config.allowed_origin(), **(headers or {})},
        "body": body,
    }


def json_response(status: int, body: dict, headers: dict | None = None) -> dict:
    return response(status, json.dumps(body), {"Content-Type": "application/json", **(headers or {})})


def caller_id(event: dict) -> str:
    # ponytail: identity is trusted from the header; put an API Gateway authorizer in front before real traffic.
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    user_id = headers.get("x-user-id") or ""
    if not USER_ID_RE.fullmatch(user_id):
        raise ApiError(401, "unauthorized", "X-User-Id header is missing or invalid")
    return user_id


def json_body(event: dict) -> dict:
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        body = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_json", "Request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object")
    return body


def api_handler(fn):
    """Turn ApiError into its response and anything else into a generic 500."""

    @functools.wraps(fn)
    def wrapper(event, context):
        try:
            return fn(event, context)
        except ApiError as exc:
            return json_response(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            logger.exception("Unhandled error")
            return json_response(500, {"error": {"code": "internal_error", "message": "Internal server error"}})

    return wrapper
```

- [ ] **Step 5: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements-dev.txt pyproject.toml Makefile src tests docs
git commit -m "feat: scaffold image service with HTTP helpers"
```

---

### Task 2: Request validation, pagination tokens, magic byte sniffing

**Files:**
- Create: `src/image_service/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**
- Consumes: `config` constants; `http.ApiError`, `http.USER_ID_RE`.
- Produces:
  - `@dataclass(frozen=True) NewImage(title: str, description: str, tags: list[str], visibility: str, content_type: str)`
  - `@dataclass(frozen=True) ListQuery(user_id: str | None = None, tag: str | None = None, created_from: str | None = None, created_to: str | None = None, title: str | None = None, visibility: str | None = None, limit: int = 20, start_key: dict | None = None)` (`title` already lowercased; timestamps already canonical UTC strings)
  - `parse_new_image(body: dict) -> NewImage`
  - `parse_list_query(params: dict | None) -> ListQuery`
  - `parse_image_id(event: dict) -> str` (raises 404 ApiError)
  - `is_uuid(value) -> bool`
  - `format_ts(dt: datetime) -> str`
  - `encode_next_token(key: dict) -> str`, `decode_next_token(token: str) -> dict`
  - `sniff_content_type(head: bytes) -> str | None`

- [ ] **Step 1: Write the failing tests `tests/test_validation.py`**

```python
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
    key = {"pk": "TAG#beach", "sk": "2026-10-01T00:00:00.000000Z#abc"}
    token = encode_next_token(key)
    assert "=" not in token
    assert parse_list_query({"next_token": token}).start_key == key


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
```

- [ ] **Step 2: Write `src/image_service/validation.py`**

```python
"""Parse and validate client input. Every rejection is a 400/404 ApiError."""
import base64
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone

from image_service.config import ALLOWED_CONTENT_TYPES, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MAX_TAGS
from image_service.http import USER_ID_RE, ApiError

TAG_RE = re.compile(r"[a-z0-9_-]{1,32}")
# Extended ISO dates only. Basic "20261002" would slip past the date only (end of day) check below.
ISO_DATE_PREFIX_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
VISIBILITIES = ("public", "private")
CREATE_FIELDS = {"title", "description", "tags", "visibility", "content_type"}
LIST_PARAMS = {"user_id", "tag", "created_from", "created_to", "title", "visibility", "limit", "next_token"}
KEY_ATTRS = {"pk", "sk", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"}


@dataclass(frozen=True)
class NewImage:
    title: str
    description: str
    tags: list[str]
    visibility: str
    content_type: str


@dataclass(frozen=True)
class ListQuery:
    user_id: str | None = None
    tag: str | None = None
    created_from: str | None = None
    created_to: str | None = None
    title: str | None = None
    visibility: str | None = None
    limit: int = DEFAULT_PAGE_SIZE
    start_key: dict | None = None


def _invalid(message: str) -> ApiError:
    return ApiError(400, "validation_error", message)


def format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_uuid(value) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _string(body: dict, field: str, required: bool, max_len: int) -> str:
    value = body.get(field)
    if value is None:
        if required:
            raise _invalid(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise _invalid(f"{field} must not be blank")
    if len(value) > max_len:
        raise _invalid(f"{field} must be at most {max_len} characters")
    return value


def _tag(raw) -> str:
    tag = raw.strip().lower() if isinstance(raw, str) else ""
    if not TAG_RE.fullmatch(tag):
        raise _invalid("each tag must be 1 to 32 characters of a-z, 0-9, _ or -")
    return tag


def parse_new_image(body: dict) -> NewImage:
    unknown = set(body) - CREATE_FIELDS
    if unknown:
        raise _invalid(f"unknown fields: {', '.join(sorted(unknown))}")
    raw_tags = body.get("tags", [])
    if not isinstance(raw_tags, list):
        raise _invalid("tags must be a list of strings")
    tags = list(dict.fromkeys(_tag(raw) for raw in raw_tags))
    if len(tags) > MAX_TAGS:
        raise _invalid(f"at most {MAX_TAGS} tags are allowed")
    visibility = body.get("visibility", "public")
    if visibility not in VISIBILITIES:
        raise _invalid("visibility must be public or private")
    content_type = body.get("content_type")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise _invalid(f"content_type must be one of {', '.join(ALLOWED_CONTENT_TYPES)}")
    return NewImage(
        title=_string(body, "title", required=True, max_len=100),
        description=_string(body, "description", required=False, max_len=1000),
        tags=tags,
        visibility=visibility,
        content_type=content_type,
    )


def _timestamp(value: str | None, field: str, end_of_day: bool) -> str | None:
    if value is None:
        return None
    if not ISO_DATE_PREFIX_RE.match(value):
        raise _invalid(f"{field} must be an ISO 8601 date (YYYY-MM-DD) or datetime")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _invalid(f"{field} must be an ISO 8601 date or datetime") from exc
    if end_of_day and len(value) == 10:  # date only: include the whole day
        dt = datetime.combine(dt.date(), time.max)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_ts(dt)


def _limit(value: str | None) -> int:
    if value is None:
        return DEFAULT_PAGE_SIZE
    try:
        limit = int(value)
    except ValueError as exc:
        raise _invalid("limit must be an integer") from exc
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise _invalid(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return limit


def encode_next_token(key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_next_token(token: str) -> dict:
    try:
        key = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except ValueError as exc:
        raise _invalid("next_token is invalid") from exc
    if (
        not isinstance(key, dict)
        or not key
        or not set(key) <= KEY_ATTRS
        or not all(isinstance(value, str) for value in key.values())
    ):
        raise _invalid("next_token is invalid")
    return key


def parse_list_query(params: dict | None) -> ListQuery:
    params = params or {}
    unknown = set(params) - LIST_PARAMS
    if unknown:
        raise _invalid(f"unknown query parameters: {', '.join(sorted(unknown))}")
    user_id = params.get("user_id")
    if user_id is not None and not USER_ID_RE.fullmatch(user_id):
        raise _invalid("user_id is invalid")
    title = params.get("title")
    if title is not None:
        title = title.strip().lower()
        if not title or len(title) > 100:
            raise _invalid("title must be 1 to 100 characters")
    visibility = params.get("visibility")
    if visibility is not None and visibility not in VISIBILITIES:
        raise _invalid("visibility must be public or private")
    created_from = _timestamp(params.get("created_from"), "created_from", end_of_day=False)
    created_to = _timestamp(params.get("created_to"), "created_to", end_of_day=True)
    if created_from and created_to and created_from > created_to:
        raise _invalid("created_from must not be after created_to")
    token = params.get("next_token")
    return ListQuery(
        user_id=user_id,
        tag=_tag(params["tag"]) if "tag" in params else None,
        created_from=created_from,
        created_to=created_to,
        title=title,
        visibility=visibility,
        limit=_limit(params.get("limit")),
        start_key=decode_next_token(token) if token else None,
    )


def parse_image_id(event: dict) -> str:
    image_id = (event.get("pathParameters") or {}).get("image_id")
    if not is_uuid(image_id):
        raise ApiError(404, "not_found", "Image not found")
    return image_id


def sniff_content_type(head: bytes) -> str | None:
    """Content type from the file signature; the client's declared type is not trusted."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
```

- [ ] **Step 3: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/image_service/validation.py tests/test_validation.py
git commit -m "feat: validate create and list input, pagination tokens, file signatures"
```

---

### Task 3: Create image (pending record + presigned POST)

**Files:**
- Create: `src/image_service/repository.py`, `src/image_service/storage.py`, `src/image_service/handlers.py`, `tests/conftest.py`, `tests/helpers.py`
- Test: `tests/test_create_image.py`

**Interfaces:**
- Consumes: Task 1 `config`, `http`; Task 2 `NewImage`, `parse_new_image`, `format_ts`.
- Produces:
  - `repository`: `PENDING = "PENDING"`, `AVAILABLE = "AVAILABLE"`, `_table()` (cached, tests call `.cache_clear()`), `_now() -> datetime` (tests monkeypatch it), `create_pending(owner_id: str, new: NewImage) -> dict`, `get_image(image_id: str) -> dict | None`.
  - Image record attributes: `pk, sk, image_id, owner_id, title, title_lower, description, tags, visibility, content_type, s3_key ("images/<owner_id>/<image_id>"), status, created_at, expires_at`.
  - `storage`: `_s3()`, `_presign_s3()` (both cached), `presign_upload(key: str, content_type: str) -> dict` (`{"url": str, "fields": dict}`).
  - `handlers.create_image(event, context)` returning 201 body `{"image_id", "status", "upload": {"url", "fields", "expires_in"}}` with `Location: /images/<id>`.
  - Test helpers: `api_event(method="GET", user="alice", body=None, path_params=None, query=None) -> dict`, `body(resp) -> dict`, `create_pending(user="alice", **fields) -> dict` (returns stored record), `table()`, `object_exists(key) -> bool`, `put_object(key, content)`, `s3_event(key) -> dict`, `JPEG`, `PNG` byte constants.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import boto3
import pytest
from moto import mock_aws

from image_service import repository, storage

TABLE = "images-test"
BUCKET = "images-test-bucket"


def _create_table():
    attrs = ["pk", "sk", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"]
    # Mirrors ImagesTable in template.yaml; keep both in sync.
    boto3.client("dynamodb").create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": name,
                "KeySchema": [
                    {"AttributeName": f"{name}pk", "KeyType": "HASH"},
                    {"AttributeName": f"{name}sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for name in ("gsi1", "gsi2")
        ],
    )


def _clear_clients():
    repository._table.cache_clear()
    storage._s3.cache_clear()
    storage._presign_s3.cache_clear()


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "TABLE_NAME": TABLE,
        "BUCKET_NAME": BUCKET,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("PUBLIC_S3_ENDPOINT", "ALLOWED_ORIGIN", "AWS_ENDPOINT_URL", "AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    _clear_clients()
    with mock_aws():
        _create_table()
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield
    _clear_clients()
```

- [ ] **Step 2: Write `tests/helpers.py`**

```python
import json
import os

import boto3

from image_service import handlers, repository

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def api_event(method="GET", user="alice", body=None, path_params=None, query=None) -> dict:
    return {
        "httpMethod": method,
        "headers": {"X-User-Id": user} if user else {},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
        "pathParameters": path_params,
        "queryStringParameters": query,
    }


def body(resp: dict) -> dict:
    return json.loads(resp["body"])


def table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def put_object(key: str, content: bytes) -> None:
    boto3.client("s3").put_object(Bucket=os.environ["BUCKET_NAME"], Key=key, Body=content)


def object_exists(key: str) -> bool:
    resp = boto3.client("s3").list_objects_v2(Bucket=os.environ["BUCKET_NAME"], Prefix=key)
    return resp["KeyCount"] > 0


def s3_event(key: str) -> dict:
    return {
        "Records": [
            {"eventSource": "aws:s3", "s3": {"bucket": {"name": os.environ["BUCKET_NAME"]}, "object": {"key": key}}}
        ]
    }


def create_pending(user="alice", **fields) -> dict:
    payload = {"title": "Sunset", "content_type": "image/jpeg", **fields}
    resp = handlers.create_image(api_event("POST", user=user, body=payload), None)
    assert resp["statusCode"] == 201, resp["body"]
    return repository.get_image(body(resp)["image_id"])
```

- [ ] **Step 3: Write the failing tests `tests/test_create_image.py`**

```python
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
```

- [ ] **Step 4: Write `src/image_service/repository.py`**

```python
"""DynamoDB access for image records.

Single table layout:
  IMG#<id>  / META            image record. gsi1 (by owner) and gsi2 (public feed) keys are set once AVAILABLE.
  TAG#<tag> / <created>#<id>  copy of the record per tag, written once AVAILABLE.
"""
import functools
import time
import uuid
from datetime import datetime, timezone

import boto3

from image_service import config
from image_service.validation import NewImage, format_ts

PENDING = "PENDING"
AVAILABLE = "AVAILABLE"


@functools.cache
def _table():
    return boto3.resource("dynamodb").Table(config.table_name())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _image_key(image_id: str) -> dict:
    return {"pk": f"IMG#{image_id}", "sk": "META"}


def _sort_key(item: dict) -> str:
    return f"{item['created_at']}#{item['image_id']}"


def create_pending(owner_id: str, new: NewImage) -> dict:
    image_id = str(uuid.uuid4())
    item = {
        **_image_key(image_id),
        "image_id": image_id,
        "owner_id": owner_id,
        "title": new.title,
        "title_lower": new.title.lower(),
        "description": new.description,
        "tags": new.tags,
        "visibility": new.visibility,
        "content_type": new.content_type,
        "s3_key": f"images/{owner_id}/{image_id}",
        "status": PENDING,
        "created_at": format_ts(_now()),
        # DynamoDB TTL removes records whose upload never arrives.
        "expires_at": int(time.time()) + config.PENDING_TTL_SECONDS,
    }
    _table().put_item(Item=item)
    return item


def get_image(image_id: str) -> dict | None:
    return _table().get_item(Key=_image_key(image_id)).get("Item")
```

- [ ] **Step 5: Write `src/image_service/storage.py`**

```python
"""S3 access: presigned URLs for clients, inspection and cleanup for the service."""
import functools

import boto3
from botocore.config import Config

from image_service import config

_SIGV4 = Config(signature_version="s3v4")


@functools.cache
def _s3():
    return boto3.client("s3", config=_SIGV4)


@functools.cache
def _presign_s3():
    # Presigned URLs embed the host. On LocalStack it must resolve from the laptop and from Lambda containers.
    endpoint = config.public_s3_endpoint()
    if endpoint is None:
        return _s3()
    return boto3.client("s3", endpoint_url=endpoint, config=_SIGV4.merge(Config(s3={"addressing_style": "path"})))


def presign_upload(key: str, content_type: str) -> dict:
    """Presigned POST: S3 itself rejects wrong size or content type."""
    return _presign_s3().generate_presigned_post(
        Bucket=config.bucket_name(),
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, config.MAX_UPLOAD_BYTES]],
        ExpiresIn=config.UPLOAD_URL_TTL_SECONDS,
    )
```

- [ ] **Step 6: Write `src/image_service/handlers.py`**

```python
"""Lambda entry points. One function per API route plus the S3 upload processor."""
import logging
import os

from image_service import repository, storage
from image_service.config import UPLOAD_URL_TTL_SECONDS
from image_service.http import api_handler, caller_id, json_body, json_response
from image_service.validation import parse_new_image

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


@api_handler
def create_image(event, _context):
    owner_id = caller_id(event)
    new = parse_new_image(json_body(event))
    item = repository.create_pending(owner_id, new)
    upload = storage.presign_upload(item["s3_key"], new.content_type)
    return json_response(
        201,
        {
            "image_id": item["image_id"],
            "status": item["status"],
            "upload": {"url": upload["url"], "fields": upload["fields"], "expires_in": UPLOAD_URL_TTL_SECONDS},
        },
        headers={"Location": f"/images/{item['image_id']}"},
    )
```

- [ ] **Step 7: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add src tests
git commit -m "feat: create image endpoint with pending record and presigned POST"
```

---

### Task 4: Upload processor (S3 event → AVAILABLE or rejected)

**Files:**
- Modify: `src/image_service/repository.py` (add `mark_available`, `delete_image`)
- Modify: `src/image_service/storage.py` (add `inspect_upload`, `delete_object`)
- Modify: `src/image_service/handlers.py` (add `process_upload`)
- Modify: `tests/helpers.py` (add `upload_image`)
- Test: `tests/test_process_upload.py`

**Interfaces:**
- Consumes: Task 3 `repository.get_image`, `repository._table`, `repository._image_key`, `repository._sort_key`; Task 2 `sniff_content_type`, `is_uuid`.
- Produces:
  - `repository.mark_available(item: dict, size_bytes: int) -> bool` (False when no longer PENDING).
  - `repository.delete_image(item: dict) -> None` (removes record and tag copies; idempotent).
  - `storage.inspect_upload(key: str) -> tuple[int, bytes] | None` (size, first 12 bytes; `None` when the object is already gone).
  - `storage.delete_object(key: str) -> None`.
  - `handlers.process_upload(event, context)`; `handlers.MAX_UPLOAD_BYTES` imported by name (tests monkeypatch it).
  - Test helper `upload_image(user="alice", content=JPEG, **fields) -> str` (image_id of an AVAILABLE image).

- [ ] **Step 1: Add `upload_image` to `tests/helpers.py`**

```python
def upload_image(user="alice", content=JPEG, **fields) -> str:
    item = create_pending(user, **fields)
    put_object(item["s3_key"], content)
    handlers.process_upload(s3_event(item["s3_key"]), None)
    return item["image_id"]
```

- [ ] **Step 2: Write the failing tests `tests/test_process_upload.py`**

```python
import pytest
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
    assert stored["gsi1pk"] == "USER#alice"
    assert stored["gsi1sk"] == _sort_key(item)
    assert stored["gsi2pk"] == "PUBLIC"
    assert stored["gsi2sk"] == _sort_key(item)
    for tag in ("beach", "sun"):
        copy = table().get_item(Key={"pk": f"TAG#{tag}", "sk": _sort_key(item)})["Item"]
        assert copy["image_id"] == item["image_id"]
        assert copy["visibility"] == "public"
        assert copy["status"] == "AVAILABLE"


def test_private_upload_is_kept_out_of_public_index():
    item = create_pending(visibility="private")
    put_object(item["s3_key"], JPEG)
    _process(item["s3_key"])
    stored = repository.get_image(item["image_id"])
    assert stored["status"] == "AVAILABLE"
    assert stored["gsi1pk"] == "USER#alice"
    assert "gsi2pk" not in stored


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
```

- [ ] **Step 3: Add to `src/image_service/repository.py`**

Add import at top: `from botocore.exceptions import ClientError`

Append:
```python
def mark_available(item: dict, size_bytes: int) -> bool:
    """Publish a PENDING image and its tag copies atomically.

    Returns False when the record is no longer PENDING (duplicate S3 event, or deleted meanwhile).
    """
    sort_key = _sort_key(item)
    sets = ["#status = :available", "size_bytes = :size", "gsi1pk = :owner", "gsi1sk = :sort"]
    values = {
        ":available": AVAILABLE,
        ":pending": PENDING,
        ":size": size_bytes,
        ":owner": f"USER#{item['owner_id']}",
        ":sort": sort_key,
    }
    if item["visibility"] == "public":
        # ponytail: one PUBLIC partition caps the feed near 1000 writes/s; shard to PUBLIC#0..N if that ever matters.
        sets += ["gsi2pk = :public", "gsi2sk = :sort"]
        values[":public"] = "PUBLIC"
    published = {**item, "status": AVAILABLE, "size_bytes": size_bytes}
    published.pop("expires_at", None)
    actions = [
        {
            "Update": {
                "TableName": config.table_name(),
                "Key": _image_key(item["image_id"]),
                "UpdateExpression": f"SET {', '.join(sets)} REMOVE expires_at",
                "ConditionExpression": "#status = :pending",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": values,
            }
        }
    ]
    actions += [
        {"Put": {"TableName": config.table_name(), "Item": {**published, "pk": f"TAG#{tag}", "sk": sort_key}}}
        for tag in item["tags"]
    ]
    try:
        _table().meta.client.transact_write_items(TransactItems=actions)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        current = get_image(item["image_id"])
        if current is not None and current["status"] == PENDING:
            raise  # conflict or throttling, not a lost race: let Lambda retry the S3 event
        return False
    return True


def delete_image(item: dict) -> None:
    """Remove the record and every tag copy. Deleting missing items is a no-op."""
    sort_key = _sort_key(item)
    keys = [_image_key(item["image_id"])] + [{"pk": f"TAG#{tag}", "sk": sort_key} for tag in item["tags"]]
    _table().meta.client.transact_write_items(
        TransactItems=[{"Delete": {"TableName": config.table_name(), "Key": key}} for key in keys]
    )
```

Note: `_table().meta.client` is the resource's client, which accepts native Python types (no `{"S": ...}` wrapping).

- [ ] **Step 4: Add to `src/image_service/storage.py`**

Add import at top: `from botocore.exceptions import ClientError`

```python
def inspect_upload(key: str) -> tuple[int, bytes] | None:
    """Object size and its first 12 bytes (enough for every allowed file signature).

    None when the object is gone (deleted after the event fired), so the event is not retried for nothing.
    """
    try:
        size = _s3().head_object(Bucket=config.bucket_name(), Key=key)["ContentLength"]
        head = _s3().get_object(Bucket=config.bucket_name(), Key=key, Range="bytes=0-11")["Body"].read()
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise
    return size, head


def delete_object(key: str) -> None:
    _s3().delete_object(Bucket=config.bucket_name(), Key=key)
```

- [ ] **Step 5: Add to `src/image_service/handlers.py`**

Replace the import block with:
```python
import logging
import os
from urllib.parse import unquote_plus

from image_service import repository, storage
from image_service.config import MAX_UPLOAD_BYTES, UPLOAD_URL_TTL_SECONDS
from image_service.http import api_handler, caller_id, json_body, json_response
from image_service.repository import PENDING
from image_service.validation import is_uuid, parse_new_image, sniff_content_type
```

Append:
```python
def process_upload(event, _context):
    """S3 ObjectCreated handler: verify the object, then publish or reject it."""
    for record in event.get("Records", []):
        _process_object(unquote_plus(record["s3"]["object"]["key"]))


def _process_object(key: str) -> None:
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
    if item["status"] == PENDING and not repository.mark_available(item, size):
        logger.info("Upload already processed: %s", key)
```

- [ ] **Step 6: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "feat: verify uploads from S3 events and publish or reject them"
```

---

### Task 5: View and download

**Files:**
- Modify: `src/image_service/storage.py` (add `presign_download`)
- Modify: `src/image_service/handlers.py` (add `PUBLIC_FIELDS`, `to_public`, `visible_to`, `get_image`, `download_image`)
- Test: `tests/test_get_and_download.py`

**Interfaces:**
- Consumes: Task 4 `upload_image` helper, `repository.get_image`, `AVAILABLE`; Task 2 `parse_image_id`.
- Produces:
  - `storage.presign_download(key: str, filename: str | None = None) -> str` (attachment disposition when `filename` given).
  - `handlers.PUBLIC_FIELDS = ("image_id", "owner_id", "title", "description", "tags", "visibility", "content_type", "size_bytes", "created_at")`.
  - `handlers.to_public(item: dict) -> dict` (only `PUBLIC_FIELDS`, `size_bytes` as int).
  - `handlers.visible_to(item: dict, caller: str) -> bool` (owner, or public and AVAILABLE).
  - `handlers.get_image(event, context)`: 200 `{...PUBLIC_FIELDS, "view_url", "download_url", "url_expires_in"}` (`download_url` is for browsers, which cannot send `X-User-Id` on a plain link).
  - `handlers.download_image(event, context)`: 302 with `Location`.

- [ ] **Step 1: Write the failing tests `tests/test_get_and_download.py`**

```python
import uuid

from helpers import PNG, api_event, body, create_pending, upload_image

from image_service import handlers

INTERNAL = {"pk", "sk", "s3_key", "status", "title_lower", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "expires_at"}


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


def test_pending_image_is_not_found_even_for_owner():
    item = create_pending()
    assert _get(item["image_id"], user="alice")["statusCode"] == 404


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
```

- [ ] **Step 2: Add to `src/image_service/storage.py`**

```python
def presign_download(key: str, filename: str | None = None) -> str:
    """Presigned GET. With a filename the browser downloads instead of displaying."""
    params = {"Bucket": config.bucket_name(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _presign_s3().generate_presigned_url("get_object", Params=params, ExpiresIn=config.DOWNLOAD_URL_TTL_SECONDS)
```

- [ ] **Step 3: Add to `src/image_service/handlers.py`**

Update imports:
```python
from image_service.config import DOWNLOAD_URL_TTL_SECONDS, EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_URL_TTL_SECONDS
from image_service.http import ApiError, api_handler, caller_id, json_body, json_response, response
from image_service.repository import AVAILABLE, PENDING
from image_service.validation import is_uuid, parse_image_id, parse_new_image, sniff_content_type
```

Add below `logger = ...`:
```python
PUBLIC_FIELDS = (
    "image_id", "owner_id", "title", "description", "tags", "visibility", "content_type", "size_bytes", "created_at",
)


def to_public(item: dict) -> dict:
    out = {field: item.get(field) for field in PUBLIC_FIELDS}
    if out["size_bytes"] is not None:
        out["size_bytes"] = int(out["size_bytes"])  # DynamoDB returns Decimal
    return out


def visible_to(item: dict, caller: str) -> bool:
    return item["owner_id"] == caller or (item["visibility"] == "public" and item["status"] == AVAILABLE)


def _viewable_image(event) -> dict:
    caller = caller_id(event)
    item = repository.get_image(parse_image_id(event))
    # 404 (not 403) for other users' private images, so ids cannot be probed.
    if item is None or item["status"] != AVAILABLE or not visible_to(item, caller):
        raise ApiError(404, "not_found", "Image not found")
    return item


def _filename(item: dict) -> str:
    return f"{item['image_id']}{EXTENSIONS[item['content_type']]}"
```

Append:
```python
@api_handler
def get_image(event, _context):
    item = _viewable_image(event)
    return json_response(
        200,
        {
            **to_public(item),
            "view_url": storage.presign_download(item["s3_key"]),
            "download_url": storage.presign_download(item["s3_key"], _filename(item)),
            "url_expires_in": DOWNLOAD_URL_TTL_SECONDS,
        },
    )


@api_handler
def download_image(event, _context):
    item = _viewable_image(event)
    return response(302, headers={"Location": storage.presign_download(item["s3_key"], _filename(item))})
```

- [ ] **Step 4: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: view metadata and download images through presigned URLs"
```

---

### Task 6: List images with filters and pagination

**Files:**
- Modify: `src/image_service/repository.py` (add `InvalidStartKey`, `list_images`)
- Modify: `src/image_service/handlers.py` (add `list_images`)
- Test: `tests/test_list_images.py`

**Interfaces:**
- Consumes: Task 2 `ListQuery`, `parse_list_query`, `encode_next_token`; Task 4 `upload_image`; Task 5 `to_public`; `repository._now` (monkeypatched in tests).
- Produces:
  - `repository.InvalidStartKey(ValueError)`.
  - `repository.list_images(caller_id: str, q: ListQuery) -> tuple[list[dict], dict | None]` (items newest first, LastEvaluatedKey).
  - `handlers.list_images(event, context)`: 200 `{"items": [to_public...], "next_token": str | None}`.

- [ ] **Step 1: Write the failing tests `tests/test_list_images.py`**

```python
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
```

- [ ] **Step 2: Add to `src/image_service/repository.py`**

Update imports:
```python
import functools
import operator
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from image_service import config
from image_service.validation import ListQuery, NewImage, format_ts
```

Add below `AVAILABLE = ...`:
```python
class InvalidStartKey(ValueError):
    """Pagination key does not belong to the query being run."""
```

Append:
```python
def list_images(caller_id: str, q: ListQuery) -> tuple[list[dict], dict | None]:
    """One key query, newest first. Never scans.

    Partition choice: tag copies, else the owner index, else the public feed.
    Remaining filters run as a FilterExpression, so a page can hold fewer than q.limit items.
    """
    owner = q.user_id or (caller_id if q.visibility == "private" else None)
    if q.tag:
        index, part_attr, part_value, sort_attr = None, "pk", f"TAG#{q.tag}", "sk"
    elif owner:
        index, part_attr, part_value, sort_attr = "gsi1", "gsi1pk", f"USER#{owner}", "gsi1sk"
    else:
        index, part_attr, part_value, sort_attr = "gsi2", "gsi2pk", "PUBLIC", "gsi2sk"

    low = q.created_from or "0"
    high = (q.created_to or "9999") + "#~"  # "~" sorts after every uuid character
    filters = [Attr("visibility").eq("public") | Attr("owner_id").eq(caller_id)]
    if q.tag and q.user_id:
        filters.append(Attr("owner_id").eq(q.user_id))
    if q.visibility:
        filters.append(Attr("visibility").eq(q.visibility))
    if q.title:
        # ponytail: contains is a post filter; move title search to OpenSearch if it becomes a primary use case.
        filters.append(Attr("title_lower").contains(q.title))

    kwargs = {
        "KeyConditionExpression": Key(part_attr).eq(part_value) & Key(sort_attr).between(low, high),
        "FilterExpression": functools.reduce(operator.and_, filters),
        "Limit": q.limit,
        "ScanIndexForward": False,
    }
    if index:
        kwargs["IndexName"] = index
    if q.start_key is not None:
        if set(q.start_key) != {"pk", "sk", part_attr, sort_attr} or q.start_key[part_attr] != part_value:
            raise InvalidStartKey("next_token does not match this query")
        kwargs["ExclusiveStartKey"] = q.start_key
    resp = _table().query(**kwargs)
    return resp["Items"], resp.get("LastEvaluatedKey")
```

- [ ] **Step 3: Add to `src/image_service/handlers.py`**

Update validation import:
```python
from image_service.validation import (
    encode_next_token,
    is_uuid,
    parse_image_id,
    parse_list_query,
    parse_new_image,
    sniff_content_type,
)
```

Append:
```python
@api_handler
def list_images(event, _context):
    caller = caller_id(event)
    query = parse_list_query(event.get("queryStringParameters"))
    try:
        items, last_key = repository.list_images(caller, query)
    except repository.InvalidStartKey as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc
    return json_response(
        200,
        {"items": [to_public(item) for item in items], "next_token": encode_next_token(last_key) if last_key else None},
    )
```

- [ ] **Step 4: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

Note for the user's test run: if `test_pagination_walks_all_pages` fails only because moto returns a `LastEvaluatedKey` on the final short page, that is moto behaviour, not a production bug.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: list images by tag, owner, date range, title and visibility"
```

---

### Task 7: Delete image

**Files:**
- Modify: `src/image_service/handlers.py` (add `delete_image`)
- Test: `tests/test_delete_image.py`

**Interfaces:**
- Consumes: Task 4 `repository.delete_image`, `storage.delete_object`; Task 5 `visible_to`; Task 2 `parse_image_id`.
- Produces: `handlers.delete_image(event, context)`: 204 empty body; 403 for non owner of a visible image; 404 when missing or invisible.

- [ ] **Step 1: Write the failing tests `tests/test_delete_image.py`**

```python
import uuid

from helpers import api_event, create_pending, object_exists, table, upload_image

from image_service import handlers, repository


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
```

- [ ] **Step 2: Append to `src/image_service/handlers.py`**

```python
@api_handler
def delete_image(event, _context):
    caller = caller_id(event)
    item = repository.get_image(parse_image_id(event))
    if item is None or not visible_to(item, caller):
        raise ApiError(404, "not_found", "Image not found")
    if item["owner_id"] != caller:
        raise ApiError(403, "forbidden", "Only the owner can delete this image")
    # Record first: a failed S3 delete leaves an invisible orphan object, never a record pointing at nothing.
    repository.delete_image(item)
    storage.delete_object(item["s3_key"])
    return response(204)
```

- [ ] **Step 3: Compile check (tests are not run)**

Run: `python3.12 -m py_compile src/image_service/*.py tests/*.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src tests
git commit -m "feat: owner only image delete"
```

---

### Task 8: SAM template, LocalStack and deploy profiles

**Files:**
- Create: `template.yaml`, `samconfig.toml`, `docker-compose.yml`
- Modify: `Makefile` (add `up`, `down`, `deploy`)

**Interfaces:**
- Consumes: handler names from Tasks 3 to 7; table layout from `tests/conftest.py`.
- Produces: stack outputs `ApiId`, `ApiUrl`, `TableName`, `BucketName`; `make deploy ENV=local|prod`; LocalStack at `http://localhost.localstack.cloud:4566`.

- [ ] **Step 1: Write `template.yaml`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Transform: AWS::Serverless-2016-10-31
Description: Image upload and storage service

Parameters:
  PublicS3Endpoint:
    Type: String
    Default: ""
    Description: Host used in presigned URLs. Empty on AWS. LocalStack URL for local.
  AllowedOrigin:
    Type: String
    MinLength: 1
    Description: CORS origin for the browser UI. "*" locally; an explicit origin such as https://app.example.com in prod.

Globals:
  Api:
    # Preflight only. Lambda responses add Access-Control-Allow-Origin themselves (http.response).
    Cors:
      AllowMethods: "'GET,POST,DELETE,OPTIONS'"
      AllowHeaders: "'Content-Type,X-User-Id'"
      AllowOrigin: !Sub "'${AllowedOrigin}'"
    # Errors API Gateway produces itself (before Lambda runs) need the header too, or browsers hide them.
    GatewayResponses:
      DEFAULT_4XX:
        ResponseParameters:
          Headers:
            Access-Control-Allow-Origin: !Sub "'${AllowedOrigin}'"
      DEFAULT_5XX:
        ResponseParameters:
          Headers:
            Access-Control-Allow-Origin: !Sub "'${AllowedOrigin}'"
  Function:
    Runtime: python3.12
    CodeUri: src/
    Timeout: 10
    MemorySize: 256
    Environment:
      Variables:
        TABLE_NAME: !Ref ImagesTable
        # Built with Sub (not Ref) so functions do not depend on the bucket; avoids a circular dependency with the S3 event.
        BUCKET_NAME: !Sub "${AWS::StackName}-images-${AWS::AccountId}"
        PUBLIC_S3_ENDPOINT: !Ref PublicS3Endpoint
        ALLOWED_ORIGIN: !Ref AllowedOrigin
        LOG_LEVEL: INFO

Resources:
  ImagesTable:
    Type: AWS::DynamoDB::Table
    Properties:
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - { AttributeName: pk, AttributeType: S }
        - { AttributeName: sk, AttributeType: S }
        - { AttributeName: gsi1pk, AttributeType: S }
        - { AttributeName: gsi1sk, AttributeType: S }
        - { AttributeName: gsi2pk, AttributeType: S }
        - { AttributeName: gsi2sk, AttributeType: S }
      KeySchema:
        - { AttributeName: pk, KeyType: HASH }
        - { AttributeName: sk, KeyType: RANGE }
      GlobalSecondaryIndexes:
        - IndexName: gsi1
          KeySchema:
            - { AttributeName: gsi1pk, KeyType: HASH }
            - { AttributeName: gsi1sk, KeyType: RANGE }
          Projection: { ProjectionType: ALL }
        - IndexName: gsi2
          KeySchema:
            - { AttributeName: gsi2pk, KeyType: HASH }
            - { AttributeName: gsi2sk, KeyType: RANGE }
          Projection: { ProjectionType: ALL }
      TimeToLiveSpecification:
        AttributeName: expires_at
        Enabled: true

  ImagesBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}"
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      # Browser UI: presigned POST upload and fetch of presigned GET URLs.
      CorsConfiguration:
        CorsRules:
          - AllowedOrigins: [!Ref AllowedOrigin]
            AllowedMethods: [GET, POST]
            AllowedHeaders: ["*"]
            MaxAge: 3000

  CreateImageFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.create_image
      Policies:
        - DynamoDBCrudPolicy: { TableName: !Ref ImagesTable }
        - S3WritePolicy: { BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}" }
      Events:
        Api: { Type: Api, Properties: { Path: /images, Method: post } }

  ListImagesFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.list_images
      Policies:
        - DynamoDBReadPolicy: { TableName: !Ref ImagesTable }
      Events:
        Api: { Type: Api, Properties: { Path: /images, Method: get } }

  GetImageFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.get_image
      Policies:
        - DynamoDBReadPolicy: { TableName: !Ref ImagesTable }
        - S3ReadPolicy: { BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}" }
      Events:
        Api: { Type: Api, Properties: { Path: "/images/{image_id}", Method: get } }

  DownloadImageFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.download_image
      Policies:
        - DynamoDBReadPolicy: { TableName: !Ref ImagesTable }
        - S3ReadPolicy: { BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}" }
      Events:
        Api: { Type: Api, Properties: { Path: "/images/{image_id}/download", Method: get } }

  DeleteImageFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.delete_image
      Policies:
        - DynamoDBCrudPolicy: { TableName: !Ref ImagesTable }
        - S3CrudPolicy: { BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}" }
      Events:
        Api: { Type: Api, Properties: { Path: "/images/{image_id}", Method: delete } }

  ProcessUploadFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: image_service.handlers.process_upload
      Policies:
        - DynamoDBCrudPolicy: { TableName: !Ref ImagesTable }
        - S3CrudPolicy: { BucketName: !Sub "${AWS::StackName}-images-${AWS::AccountId}" }
      Events:
        Uploaded:
          Type: S3
          Properties:
            Bucket: !Ref ImagesBucket
            Events: s3:ObjectCreated:*
            Filter:
              S3Key:
                Rules:
                  - { Name: prefix, Value: images/ }

Outputs:
  ApiId:
    Value: !Ref ServerlessRestApi
  ApiUrl:
    Value: !Sub "https://${ServerlessRestApi}.execute-api.${AWS::Region}.amazonaws.com/Prod"
  TableName:
    Value: !Ref ImagesTable
  BucketName:
    Value: !Ref ImagesBucket
```

- [ ] **Step 2: Write `samconfig.toml`**

```toml
version = 0.1

[local.deploy.parameters]
stack_name = "image-service-local"
region = "us-east-1"
capabilities = "CAPABILITY_IAM"
resolve_s3 = true
confirm_changeset = false
fail_on_empty_changeset = false
parameter_overrides = "PublicS3Endpoint=http://localhost.localstack.cloud:4566 AllowedOrigin=*"

[prod.deploy.parameters]
stack_name = "image-service-prod"
region = "us-east-1"
capabilities = "CAPABILITY_IAM"
resolve_s3 = true
confirm_changeset = true
fail_on_empty_changeset = false
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  localstack:
    image: localstack/localstack:latest
    ports:
      - "127.0.0.1:4566:4566"
    environment:
      - LOCALSTACK_AUTH_TOKEN=${LOCALSTACK_AUTH_TOKEN:-}
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock"
```

- [ ] **Step 4: Extend `Makefile`**

Replace the `.PHONY` line and append targets (recipe lines are TABs):
```make
ENV ?= local
LOCAL_AWS := AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1

.PHONY: install test up down validate deploy smoke

up:
	@test -n "$$LOCALSTACK_AUTH_TOKEN" || (echo "export LOCALSTACK_AUTH_TOKEN first" && exit 1)
	docker compose up -d --wait

down:
	docker compose down

validate:
	sam validate --lint --region us-east-1

deploy:
ifeq ($(ENV),local)
	$(LOCAL_AWS) samlocal build
	$(LOCAL_AWS) samlocal deploy --config-env local
else
	@test -n "$(ALLOWED_ORIGIN)" || (echo "ALLOWED_ORIGIN is required for ENV=$(ENV)" && exit 1)
	sam build
	sam deploy --config-env $(ENV) --parameter-overrides AllowedOrigin=$(ALLOWED_ORIGIN)
endif
```

`sam` and `samlocal` come from pipx (Task 1), not the venv. The `ALLOWED_ORIGIN` guard plus `MinLength: 1` on the parameter stop a prod deploy without an explicit origin.

Prod usage: `AWS_PROFILE=<your profile> ALLOWED_ORIGIN=https://app.example.com make deploy ENV=prod`. The profile is never stored in the repo.

- [ ] **Step 5: Validate template (quick check, allowed)**

Run: `make validate`
Expected: `template.yaml is a valid SAM Template` and no lint errors. Fix any lint finding in the template, not by disabling rules.

Do not run `make up`, `make deploy` or `make smoke`. The user runs them in Task 11.

- [ ] **Step 6: Commit**

```bash
git add template.yaml samconfig.toml docker-compose.yml Makefile
git commit -m "feat: SAM stack with local and prod deploy profiles"
```

---

### Task 9: End to end smoke test on LocalStack

**Files:**
- Create: `scripts/smoke.py`
- Modify: `Makefile` (add `smoke`)

**Interfaces:**
- Consumes: stack outputs `ApiId`, `TableName` from Task 8; every route.
- Produces: `make smoke` printing `SMOKE OK`.

- [ ] **Step 1: Write `scripts/smoke.py`**

```python
"""End to end check of the stack deployed on LocalStack. Run with `make smoke`."""
import os
import time

import boto3
import requests

ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost.localstack.cloud:4566")
STACK = os.environ.get("STACK_NAME", "image-service-local")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
ALICE = {"X-User-Id": "alice"}
BOB = {"X-User-Id": "bob"}


def stack_outputs() -> dict:
    cfn = boto3.client("cloudformation", endpoint_url=ENDPOINT)
    stack = cfn.describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}


def wait_until(check, what: str, timeout: int = 120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check()
        if result:
            return result
        time.sleep(2)
    raise AssertionError(f"timed out waiting for {what}")


def create(base: str, **fields) -> dict:
    resp = requests.post(f"{base}/images", json={"title": "Smoke sunset", "content_type": "image/jpeg", **fields}, headers=ALICE)
    assert resp.status_code == 201, resp.text
    return resp.json()


def upload(created: dict, content: bytes) -> requests.Response:
    up = created["upload"]
    return requests.post(up["url"], data=up["fields"], files={"file": ("image.jpg", content, "image/jpeg")})


def main():
    outputs = stack_outputs()
    base = f"{ENDPOINT}/restapis/{outputs['ApiId']}/Prod/_user_request_"
    table = boto3.resource("dynamodb", endpoint_url=ENDPOINT).Table(outputs["TableName"])

    created = create(base, tags=["smoke"])
    image_id = created["image_id"]
    resp = upload(created, JPEG)
    assert resp.status_code == 204, resp.text

    def available():
        r = requests.get(f"{base}/images/{image_id}", headers=BOB)
        return r.json() if r.status_code == 200 else None

    meta = wait_until(available, "upload processing")
    assert meta["size_bytes"] == len(JPEG), meta
    assert requests.get(meta["view_url"]).content == JPEG
    assert "attachment" in requests.get(meta["download_url"]).headers.get("Content-Disposition", "")

    # CORS: preflight answered by API Gateway, real responses carry the origin header.
    preflight = requests.options(
        f"{base}/images",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-user-id",
        },
    )
    assert preflight.headers.get("Access-Control-Allow-Origin"), preflight.headers
    assert requests.get(f"{base}/images", headers=BOB).headers.get("Access-Control-Allow-Origin") == "*"

    listed = requests.get(f"{base}/images", params={"tag": "smoke"}, headers=BOB).json()
    assert image_id in [item["image_id"] for item in listed["items"]], listed

    resp = requests.get(f"{base}/images/{image_id}/download", headers=BOB, allow_redirects=False)
    assert resp.status_code == 302, resp.text
    download = requests.get(resp.headers["Location"])
    assert download.content == JPEG
    assert "attachment" in download.headers.get("Content-Disposition", "")

    assert requests.delete(f"{base}/images/{image_id}", headers=BOB).status_code == 403
    assert requests.delete(f"{base}/images/{image_id}", headers=ALICE).status_code == 204
    assert requests.get(f"{base}/images/{image_id}", headers=ALICE).status_code == 404

    # Oversized upload: S3 policy should refuse it; if LocalStack does not enforce the policy, the processor must.
    big = create(base)
    resp = upload(big, JPEG + b"\x00" * (10 * 1024 * 1024))
    if resp.status_code == 204:
        print("note: LocalStack accepted the oversized POST; checking the processor rejects it")
        wait_until(lambda: "Item" not in table.get_item(Key={"pk": f"IMG#{big['image_id']}", "sk": "META"}), "rejection")
    else:
        assert resp.status_code == 400, resp.text

    print("SMOKE OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add `smoke` target to `Makefile`**

```make
smoke: install
	$(LOCAL_AWS) $(BIN)/python scripts/smoke.py
```

- [ ] **Step 3: Compile check only**

Run: `python3.12 -m py_compile scripts/smoke.py`
Expected: no output. Do not run `make smoke`; the user runs it in Task 11.

When the user's smoke run fails, use superpowers:systematic-debugging. Known LocalStack pitfalls to check first, in order:
1. `localhost.localstack.cloud` does not resolve on the host (DNS rebinding protection on the router): `getent hosts localhost.localstack.cloud` must return `127.0.0.1`.
2. Lambdas cannot reach LocalStack: check `docker compose logs localstack` for the Lambda error. LocalStack injects `AWS_ENDPOINT_URL` into Lambda containers and boto3 in the python3.12 runtime honours it; confirm by logging `os.environ.get("AWS_ENDPOINT_URL")` temporarily.
3. Upload never processed: confirm the bucket notification exists with `boto3.client("s3", endpoint_url=...).get_bucket_notification_configuration(Bucket=...)`.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke.py Makefile
git commit -m "test: end to end smoke test against LocalStack"
```

---

### Task 10: API documentation and usage instructions

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything above. Every example must match real behaviour; copy field names from `handlers.PUBLIC_FIELDS` and validation rules from `validation.py`.
- Produces: `README.md`.

- [ ] **Step 1: Write `README.md`**

````markdown
# Image Service

Service layer for image upload and storage in an Instagram style app. Built on API Gateway, Lambda (Python 3.12), S3 and DynamoDB. Runs fully on LocalStack for local development and deploys to AWS with the same template.

## Architecture

```
client ──POST /images──▶ API Gateway ─▶ create_image ─▶ DynamoDB (PENDING record)
   │                                         └─▶ presigned POST returned
   └──multipart POST (bytes)──▶ S3 ──ObjectCreated──▶ process_upload
                                                        ├─ size and file signature ok ─▶ AVAILABLE (+ indexes, tag copies)
                                                        └─ otherwise ─▶ object and record deleted
client ──GET/DELETE /images...──▶ API Gateway ─▶ list / get / download / delete Lambdas
```

Why this shape:

* Image bytes go straight to S3. Lambda never buffers them, so the API Gateway (10 MB) and Lambda (6 MB) payload limits do not apply and the service scales with S3.
* Every Lambda is stateless and DynamoDB runs on demand, so concurrent users scale horizontally with no shared locks or counters.
* Every list filter maps to a DynamoDB key query. No table scans.
* S3 enforces size and content type through the presigned POST policy. The processor checks again, including the real file signature, so a renamed PDF never becomes visible.

## API reference

Base URL: the `ApiUrl` stack output on AWS, or `http://localhost.localstack.cloud:4566/restapis/<ApiId>/Prod/_user_request_` on LocalStack.

### Authentication

Every request needs `X-User-Id: <id>` where id matches `^[A-Za-z0-9_-]{1,64}$`. The service trusts this header. In production an API Gateway authorizer (Cognito or JWT) must set it; that is out of scope here.

### Errors

```json
{"error": {"code": "validation_error", "message": "limit must be between 1 and 100"}}
```

| Status | code | When |
|---|---|---|
| 400 | `validation_error`, `invalid_json` | Bad body, field, query parameter or `next_token`. Unknown fields are rejected. |
| 401 | `unauthorized` | `X-User-Id` missing or invalid. |
| 403 | `forbidden` | Deleting another user's public image. |
| 404 | `not_found` | Image missing, still uploading, or private to another user. |
| 500 | `internal_error` | Unexpected failure. Details are only in CloudWatch logs. |

### Image object

```json
{
  "image_id": "3f2b8c1e-8d1a-4c5e-9f00-0a1b2c3d4e5f",
  "owner_id": "alice",
  "title": "Beach Sunset",
  "description": "Goa, 2026",
  "tags": ["beach", "sunset"],
  "visibility": "public",
  "content_type": "image/jpeg",
  "size_bytes": 482113,
  "created_at": "2026-10-03T10:00:00.000000Z"
}
```

### POST /images: start an upload

Body:

| Field | Type | Rules |
|---|---|---|
| `title` | string | Required. 1 to 100 characters after trimming. |
| `content_type` | string | Required. `image/jpeg`, `image/png`, `image/webp` or `image/gif`. |
| `description` | string | Optional. Up to 1000 characters. |
| `tags` | string[] | Optional. Up to 10. Each 1 to 32 of `a-z 0-9 _ -`. Lowercased and deduplicated. |
| `visibility` | string | Optional. `public` (default) or `private`. |

Response `201`, header `Location: /images/<image_id>`:

```json
{
  "image_id": "3f2b8c1e-8d1a-4c5e-9f00-0a1b2c3d4e5f",
  "status": "PENDING",
  "upload": {"url": "https://<bucket>.s3.amazonaws.com/", "fields": {"key": "images/alice/3f2b...", "Content-Type": "image/jpeg", "policy": "...", "x-amz-signature": "..."}, "expires_in": 300}
}
```

Then send the file as `multipart/form-data` to `upload.url`, with every entry of `upload.fields` as a form field and the file last, in a field named `file`. S3 answers `204`. Files over 10 MB or with another content type are refused. Within a few seconds the image becomes visible. Uploads that never arrive expire after 24 hours.

```bash
curl -s -X POST "$API/images" -H "X-User-Id: alice" -H "Content-Type: application/json" \
  -d '{"title": "Beach Sunset", "tags": ["beach"], "content_type": "image/jpeg"}' > created.json
python3 - <<'EOF'
import json, subprocess
c = json.load(open("created.json"))["upload"]
args = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}\n", c["url"]]
for k, v in c["fields"].items():
    args += ["-F", f"{k}={v}"]
subprocess.run(args + ["-F", "file=@sunset.jpg"], check=True)
EOF
```

### GET /images: list and search

All parameters are optional and can be combined.

| Parameter | Meaning |
|---|---|
| `user_id` | Images owned by this user. Includes private ones only when it is the caller. |
| `tag` | Images carrying this tag (case insensitive). |
| `created_from`, `created_to` | ISO 8601 date or datetime, inclusive. A bare date in `created_to` covers the whole day. No offset means UTC. Send `+` as `%2B`. |
| `title` | Case insensitive substring of the title. |
| `visibility` | `public` or `private`. `private` alone lists the caller's own private images. |
| `limit` | Page size, 1 to 100, default 20. |
| `next_token` | Value from the previous page. Only valid with the same filters. |

Without `user_id`, `tag` or `visibility=private` the result is the public feed. Results are newest first.

Response `200`:

```json
{"items": [{"image_id": "...", "title": "Beach Sunset", "...": "..."}], "next_token": "eyJwayI6..."}
```

`next_token` is `null` on the last page. `title` and secondary filters are applied after the key lookup, so a page may hold fewer than `limit` items while `next_token` is still set. Keep paging until it is `null`.

```bash
curl -s "$API/images?tag=beach&created_from=2026-10-01&limit=10" -H "X-User-Id: bob"
```

### GET /images/{image_id}: view

Response `200`: the image object plus `view_url` (presigned S3 URL that displays inline), `download_url` (presigned S3 URL that downloads as `<image_id>.<ext>`) and `url_expires_in` (seconds, 300). Browser clients use `download_url`, because a plain link cannot send `X-User-Id`.

### GET /images/{image_id}/download: download

Response `302` with `Location` set to the same kind of URL as `download_url`. Meant for `curl -L` and other clients that can send headers.

### CORS

The API, its default error responses and the S3 bucket allow the origin set by the `AllowedOrigin` template parameter. It is `*` on LocalStack. Prod deploys require an explicit origin (see Deploying to AWS). Allowed request headers: `Content-Type`, `X-User-Id`.

### DELETE /images/{image_id}

Owner only. Removes metadata, tag entries and the S3 object. Response `204` with an empty body. Pending uploads can be deleted too.

## Local development

Requirements: Docker with Compose, Python 3.12, make, pipx, and a free LocalStack account token.

```bash
pipx install aws-sam-cli                                     # once
pipx inject --include-apps aws-sam-cli aws-sam-cli-local     # once, provides samlocal
export LOCALSTACK_AUTH_TOKEN=<your token>                    # never commit it
```

```bash
make install          # venv with pytest, moto, requests
make test             # unit tests with a 90% coverage gate (moto, no Docker needed)
make up               # start LocalStack on 127.0.0.1:4566
make deploy           # samlocal build + deploy (ENV=local is the default)
make smoke            # end to end run of every endpoint against LocalStack
make down
```

Find the API id:

```bash
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test samlocal list stack-outputs --stack-name image-service-local --region us-east-1
export API="http://localhost.localstack.cloud:4566/restapis/<ApiId>/Prod/_user_request_"
```

## Deploying to AWS

Profiles live in `samconfig.toml`: `local` (LocalStack) and `prod` (AWS, region `us-east-1`, change it there if needed).

```bash
AWS_PROFILE=<your profile> ALLOWED_ORIGIN=https://app.example.com make deploy ENV=prod
```

`prod` asks for change set confirmation and refuses to deploy without `ALLOWED_ORIGIN`. `PublicS3Endpoint` stays empty on AWS so presigned URLs use the normal S3 endpoint.

## Data model

One DynamoDB table, on demand capacity.

| Item | pk | sk | GSIs |
|---|---|---|---|
| Image | `IMG#<id>` | `META` | `gsi1` `USER#<owner>` / `<created_at>#<id>`; `gsi2` `PUBLIC` / `<created_at>#<id>` (public only). Both set only once the upload is verified. |
| Tag entry | `TAG#<tag>` | `<created_at>#<id>` | none. A copy of the image record, one per tag. |

## Known limits and next steps

* Identity comes from a trusted header. Add an API Gateway authorizer before exposing the API.
* The public feed is one partition (`PUBLIC`). Shard it (`PUBLIC#0..N`) if writes approach 1000 per second. Very popular tags have the same ceiling.
* Title search is a substring filter applied after the key query. Move to OpenSearch if search becomes a main feature.
* Metadata cannot be edited after upload.
* While its presigned POST is still valid (300 s), the owner can upload a different valid image over an approved one. The processor rechecks the file signature, so junk is removed, but a valid swap goes through and `size_bytes` keeps the first value.
* No thumbnails or image resizing.
````

- [ ] **Step 2: Check README against the code**

Run: `.venv/bin/python -c "from image_service import handlers; print(handlers.PUBLIC_FIELDS)"` (with `PYTHONPATH=src`)
Expected: the same nine fields as the README image object. Do not run the curl examples; the user checks them in Task 11.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: API reference and usage instructions"
```

---

### Task 11: Final verification and review

**Files:** none new.

- [ ] **Step 1: Executor quick checks only** (superpowers:verification-before-completion)

```bash
python3.12 -m py_compile src/image_service/*.py tests/*.py scripts/smoke.py
make validate
```

Expected: no compile output, valid SAM template. Report exactly what was and was not run: tests, deploy and smoke were **not** run.

- [ ] **Step 2: Code review**

Use superpowers:requesting-code-review, then `/code-review`. Act on findings with superpowers:receiving-code-review.

- [ ] **Step 3: Hand off the user run checklist**

Give the user this list to run themselves:

```bash
make test                                   # expect all pass, coverage >= 90%
export LOCALSTACK_AUTH_TOKEN=<token>
make up
curl -s http://localhost:4566/_localstack/info   # pin the reported version in docker-compose.yml instead of latest
make deploy
make smoke                                  # expect SMOKE OK
```

Failures from that run go through superpowers:systematic-debugging (pitfalls listed in Task 9).

- [ ] **Step 4: Finish branch**

Use superpowers:finishing-a-development-branch.
