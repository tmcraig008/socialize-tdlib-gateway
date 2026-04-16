from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    web_api_key: str = ""
    # Same URL the Node API is reachable at (webhooks + downloading /uploads/... for TDLib sends).
    socialize_backend_url: str = Field(
        default="http://127.0.0.1:3002",
        validation_alias=AliasChoices("SOCIALIZE_BACKEND_URL", "BACKEND_PUBLIC_URL"),
    )
    gateway_public_url: str = ""
    api_id: int = 0
    api_hash: str = ""
    tdlib_data_root: str = "./tdlib_data"
    tdlib_mode: str = "mock"
    database_encryption_key: str = ""
    td_verbosity: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
