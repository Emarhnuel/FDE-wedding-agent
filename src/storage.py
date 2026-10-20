"""Private storage of uploaded knowledge files: the versioned S3 knowledge bucket when deployed, a local
folder otherwise. Kept free of the PDF and embedding libraries so the API stays quick to load."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import boto3

KNOWLEDGE_BUCKET = os.environ.get("KNOWLEDGE_BUCKET", "")
UPLOAD_DIRECTORY = Path(__file__).resolve().parent.parent / ".data" / "uploads"


def save_original(key: str, content: bytes) -> None:
    """Keep an uploaded original privately under `key` (company/file name)."""
    if KNOWLEDGE_BUCKET:
        boto3.client("s3").put_object(Bucket=KNOWLEDGE_BUCKET, Key=key, Body=content)
    else:
        path = UPLOAD_DIRECTORY / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@contextmanager
def original_file(key: str) -> Iterator[Path]:
    """A local path to an uploaded original; one stored in S3 is downloaded to a temporary folder."""
    if not KNOWLEDGE_BUCKET:
        yield UPLOAD_DIRECTORY / key
        return
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / Path(key).name
        boto3.client("s3").download_file(KNOWLEDGE_BUCKET, key, str(path))
        yield path
