from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Caminho absoluto: app/core/config.py → app/core/ → app/ → app/.env
# Garante leitura correta independente do cwd de onde o uvicorn é iniciado.
_ENV_FILE = str(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-secret-key-change-in-production"
    session_max_age: int = 86400
    database_url: str = "data/app.db"
    port: int = 4000

    # Estado de ativação: "locked" bloqueia todas as rotas exceto /activate
    app_state: str = "locked"

    erp_token: str = "meu-token-erp"

    monitor_url: str = "http://localhost:5000"
    monitor_client_token: str = ""

    client_name: str = "Posto Principal"
    client_cnpj: str = ""

    github_repo: str = "cristianoradin/zapdin2"

    # Velopack: URL do canal de atualizações
    velopack_channel_url: str = ""
    # Path para o Update.exe do Velopack (relativo à pasta do executável)
    velopack_update_exe: str = "Update.exe"

    dispatch_min_delay: float = 1.0   # segundos mínimos entre disparos
    dispatch_max_delay: float = 4.0   # segundos máximos entre disparos

    @property
    def is_locked(self) -> bool:
        return self.app_state.lower() == "locked"


settings = Settings()
