from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-secret-key-change-in-production"
    session_max_age: int = 86400
    database_url: str = "data/app.db"
    port: int = 4000

    erp_token: str = "meu-token-erp"

    monitor_url: str = "http://localhost:5000"
    monitor_client_token: str = "token-deste-posto"

    client_name: str = "Posto Principal"
    client_cnpj: str = ""

    github_repo: str = "cristianoradin/zapdin2"

    dispatch_min_delay: float = 1.0   # segundos mínimos entre disparos
    dispatch_max_delay: float = 4.0   # segundos máximos entre disparos


settings = Settings()
