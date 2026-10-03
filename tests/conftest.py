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
