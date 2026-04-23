from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_jwt_secret: str

    vast_api_key: str
    agent_shared_secret: str

    # Optional Hugging Face token. Unauthenticated HF pulls are rate-limited;
    # a read-scoped token makes vLLM's model weight download 3-5× faster.
    hf_token: str | None = None

    public_api_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
