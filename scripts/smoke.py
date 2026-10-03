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
