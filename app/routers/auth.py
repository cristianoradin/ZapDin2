"""
app/routers/auth.py — Autenticação multi-tenant com CNPJ.

Fluxo de login em 2 etapas:
  1. POST /api/auth/check-cnpj  → verifica se o CNPJ está ativo
  2. POST /api/auth/login       → valida usuário vinculado àquele CNPJ

Ativação de empresa (onboarding):
  POST /api/auth/registrar-empresa → valida token no Monitor, cria empresa no DB
"""
from __future__ import annotations

import logging
import secrets

import httpx
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from ..core.config import settings
from ..core.database import get_db
from ..core.security import (
    verify_password, hash_password, create_session_token,
    SESSION_COOKIE, get_current_user, normalize_cnpj,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Modelos ───────────────────────────────────────────────────────────────────

class CNPJCheck(BaseModel):
    cnpj: str


class LoginRequest(BaseModel):
    cnpj: str | None = None  # opcional: se omitido, busca usuário em qualquer empresa ativa
    username: str
    password: str


class RegistrarEmpresaRequest(BaseModel):
    token: str          # token do cliente (do Monitor)
    admin_username: str
    admin_password: str


# ── Info pública da empresa instalada (para pré-preencher CNPJ no login) ─────

@router.get("/empresa-info")
async def empresa_info(db=Depends(get_db)):
    """Retorna CNPJ e nome da empresa ativa nesta instalação (sem autenticação)."""
    async with db.execute(
        "SELECT cnpj, nome FROM empresas WHERE ativo = TRUE ORDER BY id LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"cnpj": None, "nome": None}
    return {"cnpj": row["cnpj"], "nome": row["nome"]}


# ── Passo 1: Verifica CNPJ ────────────────────────────────────────────────────

@router.post("/check-cnpj")
async def check_cnpj(body: CNPJCheck, db=Depends(get_db)):
    """
    Verifica se o CNPJ está cadastrado e ativo.
    Retorna o nome da empresa para exibição antes de pedir credenciais.
    """
    cnpj = normalize_cnpj(body.cnpj)
    if len(cnpj) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido. Informe os 14 dígitos.")

    async with db.execute(
        "SELECT id, nome FROM empresas WHERE cnpj = ? AND ativo = TRUE", (cnpj,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="CNPJ não cadastrado ou sem token ativo. "
                   "Entre em contato com o suporte para ativar seu acesso.",
        )
    return {"ok": True, "nome": row["nome"], "cnpj": cnpj}


# ── Passo 2: Login com usuário/senha ──────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    username = body.username.strip().lower()

    if body.cnpj:
        # Login com CNPJ explícito (multi-tenant)
        cnpj = normalize_cnpj(body.cnpj)
        async with db.execute(
            "SELECT id, nome FROM empresas WHERE cnpj = ? AND ativo = TRUE", (cnpj,)
        ) as cur:
            empresa = await cur.fetchone()
        if not empresa:
            raise HTTPException(status_code=401, detail="CNPJ não autorizado.")

        empresa_id = empresa["id"]
        async with db.execute(
            "SELECT id, username, password_hash FROM usuarios WHERE username = ? AND empresa_id = ?",
            (username, empresa_id),
        ) as cur:
            row = await cur.fetchone()
    else:
        # Login sem CNPJ — busca usuário em qualquer empresa ativa (single-empresa por instalação)
        async with db.execute(
            """SELECT u.id, u.username, u.password_hash, u.empresa_id, e.nome
               FROM usuarios u
               JOIN empresas e ON e.id = u.empresa_id
               WHERE u.username = ? AND e.ativo = TRUE
               LIMIT 1""",
            (username,),
        ) as cur:
            row = await cur.fetchone()
        empresa = {"id": row["empresa_id"], "nome": row["nome"]} if row else None
        empresa_id = empresa["id"] if empresa else None

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")

    token = create_session_token(row["id"], row["username"], empresa_id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return {"ok": True, "username": row["username"], "empresa": empresa["nome"]}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user), db=Depends(get_db)):
    empresa_id = user.get("empresa_id")
    empresa_nome = None
    empresa_cnpj = None

    if empresa_id:
        async with db.execute(
            "SELECT nome, cnpj FROM empresas WHERE id = ?", (empresa_id,)
        ) as cur:
            emp = await cur.fetchone()
        if emp:
            empresa_nome = emp["nome"]
            empresa_cnpj = emp["cnpj"]

    return {
        "username": user["usr"],
        "uid": user["uid"],
        "empresa_id": empresa_id,
        "empresa": empresa_nome,
        "cnpj": empresa_cnpj,
    }


# ── Registrar nova empresa (onboarding) ───────────────────────────────────────

@router.post("/registrar-empresa", status_code=status.HTTP_201_CREATED)
async def registrar_empresa(body: RegistrarEmpresaRequest, db=Depends(get_db)):
    """
    Registra um novo tenant (empresa) validando o token com o Monitor.

    Fluxo:
      1. Valida token com Monitor → obtém nome, CNPJ, token permanente
      2. Cria registro em `empresas` (ou atualiza se já existe)
      3. Cria usuário admin para a empresa
      4. Retorna credenciais de acesso
    """
    token = body.token.strip().replace("-", "").upper()
    admin_username = body.admin_username.strip().lower()

    if not token:
        raise HTTPException(status_code=400, detail="Token não pode ser vazio.")
    if not admin_username or len(body.admin_password) < 6:
        raise HTTPException(status_code=400, detail="Usuário inválido ou senha muito curta (mín. 6 chars).")

    # ── Valida token no Monitor ───────────────────────────────────────────────
    monitor_url = settings.monitor_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{monitor_url}/api/auth/cliente/{token}")
    except Exception as exc:
        logger.error("Erro ao chamar Monitor: %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível conectar ao servidor de ativação.")

    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Token não encontrado. Verifique o token informado.")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Monitor retornou erro {r.status_code}.")

    data = r.json()
    cnpj = normalize_cnpj(data.get("cnpj", ""))
    nome = data.get("nome", "Empresa")
    client_token = data.get("token", token)

    if not cnpj:
        raise HTTPException(status_code=422, detail="Monitor não retornou CNPJ válido.")

    # ── Cria ou atualiza empresa ──────────────────────────────────────────────
    try:
        cur = await db.execute(
            """INSERT INTO empresas (cnpj, nome, token, ativo)
               VALUES (?, ?, ?, TRUE)
               ON CONFLICT (cnpj) DO UPDATE
               SET nome = EXCLUDED.nome, token = EXCLUDED.token, ativo = TRUE
               RETURNING id""",
            (cnpj, nome, client_token),
        )
    except Exception as exc:
        logger.error("Erro ao criar empresa: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao registrar empresa.")

    # Se ON CONFLICT atualizou, precisamos buscar o id
    async with db.execute("SELECT id FROM empresas WHERE cnpj = ?", (cnpj,)) as c:
        emp_row = await c.fetchone()
    empresa_id = emp_row["id"]

    # ── Cria usuário admin para a empresa ─────────────────────────────────────
    try:
        await db.execute(
            """INSERT INTO usuarios (empresa_id, username, password_hash)
               VALUES (?, ?, ?)
               ON CONFLICT (empresa_id, username) DO UPDATE
               SET password_hash = EXCLUDED.password_hash""",
            (empresa_id, admin_username, hash_password(body.admin_password)),
        )
        await db.commit()
    except Exception as exc:
        logger.error("Erro ao criar usuário admin: %s", exc)
        raise HTTPException(status_code=500, detail="Empresa registrada mas falha ao criar usuário.")

    # ── Config padrão para a empresa ──────────────────────────────────────────
    defaults = [
        (empresa_id, 'mensagem_padrao', 'Olá {nome}, obrigado pela sua compra de {valor} em {data}!'),
        (empresa_id, 'wa_delay_min',    '5'),
        (empresa_id, 'wa_delay_max',    '15'),
        (empresa_id, 'wa_daily_limit',  '100'),
        (empresa_id, 'wa_hora_inicio',  '08:00'),
        (empresa_id, 'wa_hora_fim',     '18:00'),
        (empresa_id, 'wa_spintax',      '1'),
    ]
    for row in defaults:
        await db.execute(
            "INSERT INTO config (empresa_id, key, value) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            row,
        )
        await db.commit()

    logger.info("Empresa registrada: %s (%s) — admin: %s", nome, cnpj, admin_username)

    return {
        "ok": True,
        "empresa": nome,
        "cnpj": cnpj,
        "username": admin_username,
        "message": "Empresa ativada com sucesso! Faça login com seu CNPJ e as credenciais informadas.",
    }


# ── Criar usuário adicional na empresa ────────────────────────────────────────

class NovoUsuarioRequest(BaseModel):
    username: str
    password: str


@router.post("/usuarios", status_code=status.HTTP_201_CREATED)
async def criar_usuario(
    body: NovoUsuarioRequest,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    username = body.username.strip().lower()
    if not username or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Username inválido ou senha muito curta.")

    try:
        cur = await db.execute(
            "INSERT INTO usuarios (empresa_id, username, password_hash) VALUES (?, ?, ?)",
            (empresa_id, username, hash_password(body.password)),
        )
        await db.commit()
        return {"id": cur.lastrowid, "username": username}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Username já existe nesta empresa.")


@router.get("/usuarios")
async def listar_usuarios(db=Depends(get_db), user: dict = Depends(get_current_user)):
    empresa_id = user["empresa_id"]
    async with db.execute(
        "SELECT id, username, created_at FROM usuarios WHERE empresa_id = ? ORDER BY username",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/usuarios/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_usuario(uid: int, db=Depends(get_db), user: dict = Depends(get_current_user)):
    empresa_id = user["empresa_id"]
    if uid == user["uid"]:
        raise HTTPException(status_code=400, detail="Você não pode remover seu próprio usuário.")
    await db.execute(
        "DELETE FROM usuarios WHERE id = ? AND empresa_id = ?", (uid, empresa_id)
    )
    await db.commit()
