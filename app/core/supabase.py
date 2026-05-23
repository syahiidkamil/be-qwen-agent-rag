"""Singleton Supabase client used for Storage and Auth-admin operations.

Built with the service_role (sb_secret_...) key so it bypasses RLS. Never
expose this client to the frontend.
"""
from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set to use the Supabase client."
        )
    return create_client(settings.supabase_url, settings.supabase_secret_key)
