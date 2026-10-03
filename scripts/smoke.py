"""End to end check of the stack deployed on LocalStack. Run with `make smoke`."""
import os
import time

from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError

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


def listed_ids(base: str, headers: dict, **params) -> list:
    """Every image id across all pages."""
    found, token = [], None
    while True:
        page = requests.get(f"{base}/images", params={**params, **({"next_token": token} if token else {})}, headers=headers)
        assert page.status_code == 200, page.text
        found += [item["image_id"] for item in page.json()["items"]]
        token = page.json()["next_token"]
        if token is None:
            return found


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def main():
    outputs = stack_outputs()
    base = f"{ENDPOINT}/restapis/{outputs['ApiId']}/Prod/_user_request_"
    table = boto3.resource("dynamodb", endpoint_url=ENDPOINT).Table(outputs["TableName"])
    s3 = boto3.client("s3", endpoint_url=ENDPOINT)
    today = datetime.now(timezone.utc).date().isoformat()

    assert requests.get(f"{base}/images").status_code == 401

    created = create(base, tags=["smoke"])
    image_id = created["image_id"]
    pending = requests.get(f"{base}/images/{image_id}", headers=ALICE)
    assert pending.status_code == 200 and pending.json()["status"] == "PENDING", pending.text
    assert requests.get(f"{base}/images/{image_id}", headers=BOB).status_code == 404
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

    assert image_id in listed_ids(base, BOB, tag="smoke")
    assert image_id in listed_ids(base, BOB, user_id="alice", limit=1)
    assert image_id in listed_ids(base, BOB, title="smoke sun", created_from=today)
    assert image_id not in listed_ids(base, BOB, created_to="2000-01-01")

    # Private image: owner only, everywhere.
    private = create(base, tags=["smoke"], visibility="private")
    assert upload(private, JPEG).status_code == 204
    wait_until(lambda: requests.get(f"{base}/images/{private['image_id']}", headers=ALICE).json().get("status") == "AVAILABLE", "private upload")
    assert requests.get(f"{base}/images/{private['image_id']}", headers=BOB).status_code == 404
    assert private["image_id"] in listed_ids(base, ALICE, visibility="private")
    assert private["image_id"] in listed_ids(base, ALICE, tag="smoke", limit=1)
    assert private["image_id"] not in listed_ids(base, BOB, tag="smoke", limit=1)
    assert private["image_id"] not in listed_ids(base, BOB, user_id="alice", limit=1)
    assert requests.delete(f"{base}/images/{private['image_id']}", headers=ALICE).status_code == 204

    # Wrong file signature: the processor removes record and object.
    fake = create(base)
    assert upload(fake, b"%PDF-1.7 not an image").status_code == 204
    wait_until(lambda: requests.get(f"{base}/images/{fake['image_id']}", headers=ALICE).status_code == 404, "signature rejection")
    assert not object_exists(s3, outputs["BucketName"], f"images/alice/{fake['image_id']}")

    resp = requests.get(f"{base}/images/{image_id}/download", headers=BOB, allow_redirects=False)
    assert resp.status_code == 302, resp.text
    download = requests.get(resp.headers["Location"])
    assert download.content == JPEG
    assert "attachment" in download.headers.get("Content-Disposition", "")

    assert requests.delete(f"{base}/images/{image_id}", headers=BOB).status_code == 403
    assert requests.delete(f"{base}/images/{image_id}", headers=ALICE).status_code == 204
    assert requests.get(f"{base}/images/{image_id}", headers=ALICE).status_code == 404
    assert not object_exists(s3, outputs["BucketName"], f"images/alice/{image_id}")
    assert image_id not in listed_ids(base, BOB, tag="smoke")

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
