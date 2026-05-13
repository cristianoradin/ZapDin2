import sys
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Quando frozen pelo PyInstaller, os módulos ficam em _internal/ e __file__ aponta
# para dentro dessa pasta. O .env real está na pasta do executável (pai de _internal).
# Em dev, sobe dois níveis a partir de app/core/ → app/ → .env como antes.
if getattr(sys, "frozen", False):
    # Frozen: sys.executable = C:\ZapDinApp\ZapDinApp.exe → parent = C:\ZapDinApp\
    _ENV_FILE = str(Path(sys.executable).parent / ".env")
else:
    # Dev: app/core/config.py → app/core/ → app/ → app/.env
    _ENV_FILE = str(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8-sig", extra="ignore")

    secret_key: str = "dev-secret-key-change-in-production"
    session_max_age: int = 86400
    database_url: str = "postgresql://postgres@localhost/zapdin_app"
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

    # Backend de WhatsApp: "playwright" (padrão) ou "evolution"
    wa_backend: str = "playwright"

    # Evolution API (usado quando wa_backend=evolution)
    evolution_url: str = "http://localhost:8080"
    evolution_api_key: str = ""

    @property
    def is_locked(self) -> bool:
        return self.app_state.lower() == "locked"

    @property
    def use_evolution(self) -> bool:
        return self.wa_backend.lower() == "evolution"


settings = Settings()
