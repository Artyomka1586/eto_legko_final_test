from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    keitaro_api_url: str = "https://keitaro-for-tests.etolegko.com/admin_api/v1"
    # Токен обязателен: без него приложение откажется стартовать с понятной ошибкой
    # (fail fast), а не упадёт загадочным 401 при первом же запросе к Keitaro.
    keitaro_api_token: str
    database_url: str = "sqlite:///./wrapper.db"
    campaign_name_prefix: str = "test_adulybin_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
