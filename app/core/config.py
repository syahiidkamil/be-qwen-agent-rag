from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # CORS — comma-separated list of allowed origins
    cors_origins: str = "http://localhost:5181"

    # Qwen / DashScope
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen3.5-122b-a10b"
    qwen_embedding_model: str = "text-embedding-v3"
    qwen_max_input_tokens: int = 90000

    # Supabase (Auth + Storage). The publishable key lives on the frontend only.
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "knowledgebase-docs"

    # Postgres (used directly via SQLAlchemy + pgvector)
    database_url: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url_async(self) -> str:
        """Force asyncpg driver scheme for SQLAlchemy."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
