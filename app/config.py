from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    web_api_key: str = ""
    socialize_backend_url: str = "http://127.0.0.1:3002"
    api_id: int = 0
    api_hash: str = ""
    tdlib_data_root: str = "./tdlib_data"
    tdlib_mode: str = "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
