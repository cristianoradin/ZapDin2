"""
app/routers/pdv_router.py — Endpoints para o ZapDin PDV local.

Autenticação por TOKEN DE MÁQUINA (X-PDV-Token), não por usuário/senha.
O admin gera o token uma vez no App → coloca no .env do PDV → pronto.

Rotas:
  POST /api/pdv/tokens          — gera novo token (requer sessão de usuário)
  GET  /api/pdv/tokens          — lista tokens da empresa
  DELETE /api/pdv/tokens/{id}   — revoga token

  -- As rotas abaixo usam X-PDV-Token (sem sessão de usuário) --
  GET  /api/pdv/config          — config que o PDV precisa pra funcionar
  POST /api/pdv/sessao-local    — PDV reporta status do WhatsApp local
  GET  /api/pdv/sessoes         — lista PDVs ativos da empresa
"""
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pdv", tags=["PDV"])


# ── Auth por token de máquina ─────────────────────────────────────────────────

async def _get_empresa_by_token(
    x_pdv_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
) -> dict:
    """Valida X-PDV-Token e retorna a empresa associada."""
    if not x_pdv_token:
        raise HTTPException(401, "Header X-PDV-Token obrigatório")

    async with db.execute(
        """SELECT t.id AS token_id, t.empresa_id, t.nome AS pdv_nome, e.nome AS empresa_nome
           FROM pdv_tokens t
           JOIN empresas e ON e.id = t.empresa_id
           WHERE t.token = ? AND t.ativo = TRUE""",
        (x_pdv_token,),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(401, "Token PDV inválido ou revogado")

    # Atualiza ultimo_uso sem bloquear
    await db.execute(
        "UPDATE pdv_tokens SET ultimo_uso = NOW() WHERE id = ?",
        (row["token_id"],),
    )
    await db.commit()

    return dict(row)


# ── Modelos ────────────────────────────────────────────────────────────────────

class NovoTokenBody(BaseModel):
    nome: str = "PDV"   # ex: "Caixa 01", "Caixa 02"

class SessaoLocalPayload(BaseModel):
    sessao_id: str
    pdv_nome:  str = "PDV"
    phone:     Optional[str] = None
    status:    str = "unknown"   # connected | disconnected | connecting | unknown


# ── Gerenciamento de tokens (requer sessão de usuário logado) ─────────────────

@router.post("/tokens", summary="Gerar token para um PDV")
async def gerar_token(
    body: NovoTokenBody,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Gera um token de máquina para o PDV.
    O admin copia este token para o ZAPDIN_PDV_TOKEN no .env do PDV.
    Não precisa mais de usuário/senha.
    """
    empresa_id = user["empresa_id"]
    token = "pdv_" + secrets.token_urlsafe(32)

    cur = await db.execute(
        "INSERT INTO pdv_tokens (empresa_id, token, nome) VALUES (?, ?, ?)",
        (empresa_id, token, body.nome.strip() or "PDV"),
    )
    await db.commit()

    return {
        "ok": True,
        "id": cur.lastrowid,
        "nome": body.nome,
        "token": token,
        "instrucao": f"Coloque no .env do PDV:  ZAPDIN_PDV_TOKEN={token}",
    }


@router.get("/tokens", summary="Listar tokens da empresa")
async def listar_tokens(
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    async with db.execute(
        """SELECT id, nome, ativo, criado_em, ultimo_uso,
                  LEFT(token, 10) || '…' AS token_preview
           FROM pdv_tokens
           WHERE empresa_id = ?
           ORDER BY criado_em DESC""",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/tokens/{token_id}", summary="Revogar token")
async def revogar_token(
    token_id: int,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    await db.execute(
        "UPDATE pdv_tokens SET ativo = FALSE WHERE id = ? AND empresa_id = ?",
        (token_id, empresa_id),
    )
    await db.commit()
    return {"ok": True}


# ── Endpoints usados pelo PDV (auth por X-PDV-Token) ─────────────────────────

@router.get("/config", summary="Config que o PDV precisa")
async def config_pdv(
    empresa=Depends(_get_empresa_by_token),
    db=Depends(get_db),
):
    """
    PDV busca configurações ao iniciar:
      - evolution_api_key: chave para o Evolution API local
      - mensagem_padrao: texto padrão se nenhum template bater
      - pdv_ativo: se o PDV está habilitado para esta empresa
    """
    empresa_id = empresa["empresa_id"]

    async with db.execute(
        "SELECT key, value FROM config WHERE empresa_id = ?",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()

    cfg = {r["key"]: r["value"] for r in rows}

    return {
        "empresa":           empresa["empresa_nome"],
        "evolution_api_key": cfg.get("pdv_evolution_key", "zapdin-pdv-local"),
        "mensagem_padrao":   cfg.get("mensagem_padrao", ""),
        "pdv_ativo":         cfg.get("pdv_ativo", "true") == "true",
    }


@router.post("/sessao-local", summary="PDV reporta status do WhatsApp")
async def registrar_sessao_local(
    body: SessaoLocalPayload,
    empresa=Depends(_get_empresa_by_token),
    db=Depends(get_db),
):
    """PDV reporta que o WhatsApp local conectou/desconectou."""
    empresa_id = empresa["empresa_id"]

    await db.execute(
        """INSERT INTO pdv_sessoes (empresa_id, sessao_id, pdv_nome, phone, status, updated_at)
           VALUES (?, ?, ?, ?, ?, NOW())
           ON CONFLICT (empresa_id, sessao_id)
           DO UPDATE SET
               pdv_nome   = EXCLUDED.pdv_nome,
               phone      = EXCLUDED.phone,
               status     = EXCLUDED.status,
               updated_at = NOW()""",
        (empresa_id, body.sessao_id, body.pdv_nome, body.phone, body.status),
    )
    await db.commit()

    logger.info("PDV sessao-local: empresa=%s pdv=%s status=%s phone=%s",
                empresa_id, body.pdv_nome, body.status, body.phone)
    return {"ok": True}


@router.get("/sessoes", summary="Lista PDVs ativos da empresa")
async def listar_sessoes_pdv(
    empresa=Depends(_get_empresa_by_token),
    db=Depends(get_db),
):
    empresa_id = empresa["empresa_id"]

    async with db.execute(
        """SELECT sessao_id, pdv_nome, phone, status, updated_at,
                  EXTRACT(EPOCH FROM (NOW() - updated_at)) AS segundos_atras
           FROM pdv_sessoes
           WHERE empresa_id = ?
           ORDER BY updated_at DESC""",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        seg = int(d.pop("segundos_atras") or 0)
        d["online"] = seg < 120
        d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
        result.append(d)
    return result
