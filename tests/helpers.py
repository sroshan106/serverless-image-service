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
