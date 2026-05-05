from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Caminho absoluto: monitor/core/config.py → monitor/core/ → monitor/ → monitor/.env
# Garante leitura correta independente do cwd de onde o uvicorn é iniciado.
_ENV_FILE = str(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-monitor-secret-key-change-in-prod"
    session_max_age: int = 86400
    database_url: str = "postgresql://postgres@localhost/zapdin_monitor"
    port: int = 5000

    current_app_version: str = "1.0.0"

    # URL do app de envio (para sincronização de usuários)
    # Em produção: IP/hostname da máquina do cliente, ex: http://192.168.1.50:4000
    app_url: str = "http://localhost:4000"

    # Token de autenticação do app (deve bater com MONITOR_CLIENT_TOKEN do app/.env)
    # Usado no header x-monitor-token nas chamadas de sync de usuários
    app_sync_token: str = ""

    # URL pública deste monitor — enviada ao app durante a ativação
    # O app grava esse valor no seu .env e usa para todas as chamadas futuras
    # Em produção: http://SEU_IP_OU_DOMINIO:5000
    monitor_public_url: str = "http://localhost:5000"


settings = Settings()
