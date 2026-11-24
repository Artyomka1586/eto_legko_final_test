from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    keitaro_api_url: str = "https://keitaro-for-tests.etolegko.com/admin_api/v1"
    keitaro_api_token: str = ""
    database_url: str = "sqlite:///./wrapper.db"
    campaign_name_prefix: str = "test_ulybin_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
