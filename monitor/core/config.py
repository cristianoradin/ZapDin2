from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-monitor-secret-key-change-in-prod"
    session_max_age: int = 86400
    database_url: str = "data/monitor.db"
    port: int = 5000

    current_app_version: str = "1.0.0"


settings = Settings()
