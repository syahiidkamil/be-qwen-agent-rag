"""Thin wrapper over Supabase Storage for knowledgebase files."""
from __future__ import annotations

import io
import uuid
from typing import IO

from app.core.config import get_settings
from app.core.supabase import get_supabase


def _bucket():
    return get_supabase().storage.from_(get_settings().supabase_storage_bucket)


def make_storage_path(document_id: uuid.UUID, filename: str) -> str:
    return f"documents/{document_id}/{filename}"


def upload_bytes(path: str, content: bytes, content_type: str | None = None) -> None:
    options = {"upsert": "true"}
    if content_type:
        options["content-type"] = content_type
    _bucket().upload(path, content, options)


def download_bytes(path: str) -> bytes:
    return _bucket().download(path)


def remove(path: str) -> None:
    _bucket().remove([path])


def public_url(path: str) -> str:
    """Return the public URL for a storage object.

    Only valid when the bucket is set to public in the Supabase dashboard.
    For private buckets, generate a signed URL instead.
    """
    settings = get_settings()
    base = settings.supabase_url.rstrip('/')
    bucket = settings.supabase_storage_bucket
    return f"{base}/storage/v1/object/public/{bucket}/{path}"
