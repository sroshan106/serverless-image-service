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
    """Caller from the API Gateway authorizer context; X-User-Id only when TRUST_USER_HEADER is set (local).

    An authorizer does not strip client headers, so the header is never read once identity comes from it.
    """
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    # Cognito user pool authorizers put claims under "claims"; Lambda authorizers set principalId.
    user_id = (authorizer.get("claims") or {}).get("sub") or authorizer.get("principalId")
    if not user_id and config.trust_user_header():
        headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
        user_id = headers.get("x-user-id")
    if not isinstance(user_id, str) or not USER_ID_RE.fullmatch(user_id):
        raise ApiError(401, "unauthorized", "Caller identity is missing or invalid")
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
