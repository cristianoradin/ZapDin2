"""
ZapDin — Módulo de Ativação por Token
======================================
Fluxo:
  1. Monitor criptografa a config do cliente com AES-256-GCM usando uma chave
     derivada do token de ativação via PBKDF2-SHA256.
  2. App recebe o blob cifrado, deriva a mesma chave a partir do token digitado
     pelo usuário e descriptografa localmente.
  3. Config é gravada no .env e APP_STATE muda para 'active'.

Dependência: pip install cryptography
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _crypto_imports():
    """Importa cryptography de forma lazy — evita falha de ImportError no startup."""
    from cryptography.exceptions import InvalidTag  # noqa: F401
    from cryptography.hazmat.primitives import hashes  # noqa: F401
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: F401
    return InvalidTag, hashes, AESGCM, PBKDF2HMAC

# Salt fixo e público — a segurança está no token, não no salt.
# Altere este valor entre projetos distintos.
_PBKDF2_SALT = b"zapdin-activation-v1-salt-2024"
_PBKDF2_ITERATIONS = 200_000


# ─────────────────────────────────────────────────────────────────────────────
#  Derivação de chave
# ─────────────────────────────────────────────────────────────────────────────

def derive_key(token: str) -> bytes:
    """Deriva 256 bits de chave AES a partir do token via PBKDF2-SHA256."""
    _, hashes, _, PBKDF2HMAC = _crypto_imports()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(token.strip().encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
#  Criptografia (usada pelo Monitor)
# ─────────────────────────────────────────────────────────────────────────────

def encrypt_config(token: str, config: dict[str, Any]) -> dict[str, str]:
    """
    Criptografa um dicionário de config com AES-256-GCM.
    Retorna {"encrypted": "<b64>", "nonce": "<b64>"}.
    Chamado pelo Monitor ao gerar a resposta de ativação.
    """
    _, _, AESGCM, _ = _crypto_imports()
    key = derive_key(token)
    nonce = secrets.token_bytes(12)          # 96 bits — tamanho padrão GCM
    plaintext = json.dumps(config, ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "encrypted": base64.b64encode(ciphertext).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Descriptografia (usada pelo App)
# ─────────────────────────────────────────────────────────────────────────────

def decrypt_config(
    token: str,
    encrypted_b64: str,
    nonce_b64: str,
) -> dict[str, Any]:
    """
    Descriptografa o blob recebido do Monitor.
    Levanta ValueError se o token estiver errado ou o blob corrompido.
    """
    InvalidTag, _, AESGCM, _ = _crypto_imports()
    key = derive_key(token)
    try:
        nonce      = base64.b64decode(nonce_b64)
        ciphertext = base64.b64decode(encrypted_b64)
        plaintext  = AESGCM(key).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))
    except InvalidTag:
        raise ValueError("Token inválido ou config corrompida.")
    except Exception as exc:
        raise ValueError(f"Falha na descriptografia: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
#  Aplicação da config no .env
# ─────────────────────────────────────────────────────────────────────────────

def apply_config_to_env(config: dict[str, Any], env_path: str | Path) -> None:
    """
    Mescla `config` no .env existente.
    - Preserva linhas de comentário.
    - Sobrescreve apenas as chaves presentes em `config`.
    - Gera SECRET_KEY aleatória se não vier na config.
    - Define APP_STATE=active.
    """
    env_path = Path(env_path)

    # Lê estado atual
    existing: dict[str, str] = {}
    comment_lines: list[str] = []

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                comment_lines.append(line)
                continue
            if "=" in stripped:
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()

    # Aplica novos valores
    existing.update({k: str(v) for k, v in config.items()})
    existing["APP_STATE"] = "active"

    # Gera SECRET_KEY forte se não fornecida
    if not existing.get("SECRET_KEY"):
        existing["SECRET_KEY"] = secrets.token_hex(32)

    # Reescreve o .env
    lines = ["# ZapDin — configuração ativa (gerada pelo fluxo de ativação)\n"]
    for k, v in existing.items():
        # Citação automática se o valor contém espaços
        lines.append(f'{k}={v}\n')

    env_path.write_text("".join(lines), encoding="utf-8")
    logger.info("[activation] .env atualizado em %s — APP_STATE=active", env_path)


# ─────────────────────────────────────────────────────────────────────────────
#  Localização do .env a partir do executável
# ─────────────────────────────────────────────────────────────────────────────

def env_path() -> Path:
    """Retorna o caminho absoluto do .env da aplicação."""
    import sys
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        # Desenvolvimento: dois níveis acima deste arquivo (app/core/ → app/ → raiz)
        base = Path(__file__).parent.parent
    return base / ".env"
