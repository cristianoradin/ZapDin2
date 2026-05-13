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
    Verifica CNPJ diretamente no Monitor.
    O Monitor confirma se o token desta instalação corresponde ao CNPJ digitado.
    """
    cnpj = normalize_cnpj(body.cnpj)
    if len(cnpj) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido. Informe os 14 dígitos.")

    if not settings.monitor_client_token:
        raise HTTPException(status_code=503, detail="Sistema não ativado. Informe o token de ativação.")

    monitor_url = settings.monitor_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{monitor_url}/api/auth/check-cnpj",
                json={"cnpj": cnpj, "client_token": settings.monitor_client_token},
            )
    except Exception as exc:
        logger.error("Erro ao contatar Monitor (check-cnpj): %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível conectar ao servidor de autenticação.")

    if r.status_code in (404, 403):
        detail = r.json().get("detail", "CNPJ não autorizado.")
        raise HTTPException(status_code=r.status_code, detail=detail)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Erro no servidor de autenticação ({r.status_code}).")

    data = r.json()
    return {"ok": True, "nome": data["nome"], "cnpj": cnpj}


# ── Passo 2: Login com usuário/senha ──────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    username = body.username.strip().lower()

    if not settings.monitor_client_token:
        raise HTTPException(status_code=503, detail="Sistema não ativado. Informe o token de ativação.")

    # Valida credenciais no Monitor
    monitor_url = settings.monitor_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{monitor_url}/api/auth/verificar",
                json={
                    "username": username,
                    "password": body.password,
                    "client_token": settings.monitor_client_token,
                },
            )
    except Exception as exc:
        logger.error("Erro ao contatar Monitor (login): %s", exc)
        raise HTTPException(status_code=503, detail="Não foi possível conectar ao servidor de autenticação.")

    if r.status_code == 401:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")
    if r.status_code == 403:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Acesso não autorizado para este posto.")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Erro no servidor de autenticação ({r.status_code}).")

    # Monitor validou — determina empresa pelo CNPJ ou pela única empresa ativa
    empresa_id = None
    empresa_nome = None
    if body.cnpj:
        cnpj = normalize_cnpj(body.cnpj)
        async with db.execute(
            "SELECT id, nome FROM empresas WHERE cnpj = ? AND ativo = TRUE", (cnpj,)
        ) as cur:
            emp = await cur.fetchone()
        if emp:
            empresa_id = emp["id"]
            empresa_nome = emp["nome"]

    if not empresa_id:
        async with db.execute(
            "SELECT id, nome FROM empresas WHERE ativo = TRUE ORDER BY id LIMIT 1"
        ) as cur:
            emp = await cur.fetchone()
        if emp:
            empresa_id = emp["id"]
            empresa_nome = emp["nome"]

    if not empresa_id:
        raise HTTPException(status_code=503, detail="Nenhuma empresa ativa. Ative o sistema primeiro.")

    # ── Puxa menus do monitor (fonte da verdade) e atualiza banco local ──────
    # O monitor pode ter menus restritos para este usuário. Como o push
    # (monitor → app) não funciona através de NAT, puxamos a cada login.
    import json as _json
    menus_from_monitor = None  # None = todos os menus permitidos
    try:
        async with db.execute(
            "SELECT token FROM empresas WHERE id = ? AND ativo = TRUE", (empresa_id,)
        ) as c:
            emp_tok = await c.fetchone()
        if emp_tok and emp_tok["token"]:
            async with httpx.AsyncClient(timeout=5) as hc:
                mr = await hc.get(
                    f"{monitor_url}/api/auth/usuario-menus/{username}",
                    params={"client_token": emp_tok["token"]},
                )
            if mr.status_code == 200:
                mdata = mr.json()
                menus_from_monitor = mdata.get("menus")  # None ou lista
    except Exception as exc:
        logger.debug("[login] Não foi possível buscar menus do monitor: %s", exc)

    # Busca uid local do usuário (cria se não existir ainda)
    async with db.execute(
        "SELECT id, password_hash FROM usuarios WHERE username = ? AND empresa_id = ?",
        (username, empresa_id),
    ) as cur:
        row = await cur.fetchone()

    menus_json = _json.dumps(menus_from_monitor) if menus_from_monitor is not None else None

    if row:
        local_uid = row["id"]
        # Atualiza menus no banco local (sincroniza com o que o monitor retornou)
        await db.execute(
            "UPDATE usuarios SET menus = ? WHERE id = ? AND empresa_id = ?",
            (menus_json, local_uid, empresa_id),
        )
        await db.commit()
    else:
        # Usuário ainda não existe localmente — cria sem senha (login via monitor)
        from ..core.security import hash_password as _hp
        cur2 = await db.execute(
            "INSERT INTO usuarios (empresa_id, username, password_hash, menus) VALUES (?, ?, ?, ?)",
            (empresa_id, username, _hp(""), menus_json),
        )
        await db.commit()
        local_uid = cur2.lastrowid or 0

    token = create_session_token(local_uid, username, empresa_id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return {"ok": True, "username": username, "empresa": empresa_nome}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user), db=Depends(get_db)):
    empresa_id = user.get("empresa_id")
    empresa_nome = None
    empresa_cnpj = None
    menus = None

    if empresa_id:
        async with db.execute(
            "SELECT nome, cnpj FROM empresas WHERE id = ?", (empresa_id,)
        ) as cur:
            emp = await cur.fetchone()
        if emp:
            empresa_nome = emp["nome"]
            empresa_cnpj = emp["cnpj"]

        # Busca menus permitidos do usuário
        async with db.execute(
            "SELECT menus FROM usuarios WHERE id = ? AND empresa_id = ?",
            (user["uid"], empresa_id),
        ) as cur:
            u = await cur.fetchone()
        if u and u["menus"]:
            import json as _json
            try:
                menus = _json.loads(u["menus"])
            except Exception:
                menus = None

    return {
        "username": user["usr"],
        "uid": user["uid"],
        "empresa_id": empresa_id,
        "empresa": empresa_nome,
        "cnpj": empresa_cnpj,
        "menus": menus,  # null = todos permitidos; array = só esses menus
    }


# ── Registrar nova empresa (onboarding) ───────────────────────────────────────

@router.post("/registrar-empresa", status_code=status.HTTP_201_CREATED)
async def registrar_empresa(body: RegistrarEmpresaRequest, db=Depends(get_db)):
    """
    Ativa o app com o token do cliente gerado no Monitor.

    Fluxo:
      1. Valida token com Monitor → obtém nome, CNPJ, token e usuários vinculados
      2. Cria/atualiza empresa no banco do app
      3. Importa todos os usuários vinculados ao cliente no monitor
      4. Retorna CNPJ para prosseguir ao login
    """
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token não pode ser vazio.")

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
    usuarios_monitor = data.get("usuarios", [])

    if not cnpj:
        raise HTTPException(status_code=422, detail="Monitor não retornou CNPJ válido.")

    # ── Cria ou atualiza empresa ──────────────────────────────────────────────
    await db.execute(
        """INSERT INTO empresas (cnpj, nome, token, ativo)
           VALUES (?, ?, ?, TRUE)
           ON CONFLICT (cnpj) DO UPDATE
           SET nome = EXCLUDED.nome, token = EXCLUDED.token, ativo = TRUE""",
        (cnpj, nome, client_token),
    )
    await db.commit()

    async with db.execute("SELECT id FROM empresas WHERE cnpj = ?", (cnpj,)) as c:
        emp_row = await c.fetchone()
    empresa_id = emp_row["id"]

    # ── Importa usuários vinculados ao cliente no Monitor ────────────────────
    usuarios_importados = 0
    for u in usuarios_monitor:
        username = u.get("username", "").strip().lower()
        password_hash = u.get("password_hash", "")
        if not username or not password_hash:
            continue
        await db.execute(
            """INSERT INTO usuarios (empresa_id, username, password_hash)
               VALUES (?, ?, ?)
               ON CONFLICT (empresa_id, username) DO UPDATE
               SET password_hash = EXCLUDED.password_hash""",
            (empresa_id, username, password_hash),
        )
        usuarios_importados += 1
    await db.commit()

    logger.info("Empresa ativada: %s (%s) — %d usuário(s) importado(s)", nome, cnpj, usuarios_importados)

    return {
        "ok": True,
        "empresa": nome,
        "cnpj": cnpj,
        "usuarios_importados": usuarios_importados,
        "message": f"Empresa ativada! {usuarios_importados} usuário(s) importado(s). Faça login com seu CNPJ.",
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
