"""
Rotas de Disparo em Massa — Contatos e Campanhas.
"""
import io
import os
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/campanha", tags=["campanha"])

UPLOAD_DIR = "data/arquivos"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _eid(user: dict) -> int:
    return user["empresa_id"]


# ─────────────────────── Contatos ───────────────────────────────────────────

class ContatoIn(BaseModel):
    phone: str
    nome: Optional[str] = ""


@router.get("/contatos")
async def list_contatos(q: str = "", db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    if q:
        async with db.execute(
            "SELECT id, phone, nome, ativo FROM contatos "
            "WHERE empresa_id=? AND (phone ILIKE ? OR nome ILIKE ?) ORDER BY nome",
            (empresa_id, f"%{q}%", f"%{q}%"),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT id, phone, nome, ativo FROM contatos WHERE empresa_id=? ORDER BY nome",
            (empresa_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/contatos")
async def create_contato(body: ContatoIn, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    phone = body.phone.strip()
    if not phone:
        raise HTTPException(400, "Telefone obrigatório")
    try:
        cur = await db.execute(
            "INSERT INTO contatos (empresa_id, phone, nome) VALUES (?,?,?) "
            "ON CONFLICT (empresa_id, phone) DO UPDATE SET nome=EXCLUDED.nome",
            (empresa_id, phone, body.nome or ""),
        )
        await db.commit()
        return {"ok": True, "id": cur.lastrowid}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.post("/contatos/importar")
async def importar_contatos(
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    empresa_id = _eid(user)
    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    imported = 0
    errors = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        phone = parts[0] if parts else ""
        nome = parts[1] if len(parts) > 1 else ""
        if not phone:
            continue
        try:
            await db.execute(
                "INSERT INTO contatos (empresa_id, phone, nome) VALUES (?,?,?) "
                "ON CONFLICT (empresa_id, phone) DO UPDATE SET nome=EXCLUDED.nome",
                (empresa_id, phone, nome),
            )
            imported += 1
        except Exception:
            errors += 1
    await db.commit()
    return {"ok": True, "importados": imported, "erros": errors}


@router.delete("/contatos/{contato_id}")
async def delete_contato(contato_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    await db.execute(
        "DELETE FROM contatos WHERE id=? AND empresa_id=?", (contato_id, empresa_id)
    )
    await db.commit()
    return {"ok": True}


# ─────────────────────── Campanhas ──────────────────────────────────────────

class CampanhaIn(BaseModel):
    nome: str
    tipo: str = "text"  # text | file
    mensagem: Optional[str] = ""


@router.get("")
async def list_campanhas(db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    async with db.execute(
        "SELECT id, nome, tipo, mensagem, status, total, enviados, erros, created_at, started_at, done_at "
        "FROM campanhas WHERE empresa_id=? ORDER BY id DESC",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "started_at", "done_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        result.append(d)
    return result


@router.post("")
async def create_campanha(body: CampanhaIn, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    cur = await db.execute(
        "INSERT INTO campanhas (empresa_id, nome, tipo, mensagem, status) VALUES (?,?,?,?,?)",
        (empresa_id, body.nome.strip(), body.tipo, body.mensagem or "", "draft"),
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.delete("/{campanha_id}")
async def delete_campanha(campanha_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    # Remove envios, arquivos e campanha
    await db.execute("DELETE FROM campanha_envios WHERE campanha_id=?", (campanha_id,))
    # Remove arquivos do disco
    async with db.execute(
        "SELECT nome_arquivo FROM campanha_arquivos WHERE campanha_id=?", (campanha_id,)
    ) as cur:
        arqs = await cur.fetchall()
    for a in arqs:
        path = os.path.join(UPLOAD_DIR, a["nome_arquivo"])
        try:
            os.remove(path)
        except Exception:
            pass
    await db.execute("DELETE FROM campanha_arquivos WHERE campanha_id=?", (campanha_id,))
    await db.execute("DELETE FROM campanhas WHERE id=? AND empresa_id=?", (campanha_id, empresa_id))
    await db.commit()
    return {"ok": True}


# ── Arquivos de campanha ─────────────────────────────────────────────────────

@router.post("/{campanha_id}/arquivo")
async def upload_campanha_arquivo(
    campanha_id: int,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    empresa_id = _eid(user)
    # Valida que campanha pertence a empresa
    async with db.execute(
        "SELECT id FROM campanhas WHERE id=? AND empresa_id=?", (campanha_id, empresa_id)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Campanha não encontrada")

    ext = os.path.splitext(file.filename or "")[-1]
    nome_arquivo = f"camp_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(UPLOAD_DIR, nome_arquivo)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    await db.execute(
        "INSERT INTO campanha_arquivos (campanha_id, nome_original, nome_arquivo) VALUES (?,?,?)",
        (campanha_id, file.filename, nome_arquivo),
    )
    await db.commit()
    return {"ok": True, "nome_original": file.filename, "nome_arquivo": nome_arquivo}


@router.get("/{campanha_id}/arquivos")
async def list_campanha_arquivos(
    campanha_id: int, db=Depends(get_db), user=Depends(get_current_user)
):
    empresa_id = _eid(user)
    async with db.execute(
        "SELECT ca.id, ca.nome_original, ca.nome_arquivo "
        "FROM campanha_arquivos ca "
        "JOIN campanhas c ON c.id=ca.campanha_id "
        "WHERE ca.campanha_id=? AND c.empresa_id=?",
        (campanha_id, empresa_id),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/{campanha_id}/arquivo/{arq_id}")
async def delete_campanha_arquivo(
    campanha_id: int, arq_id: int, db=Depends(get_db), user=Depends(get_current_user)
):
    empresa_id = _eid(user)
    async with db.execute(
        "SELECT ca.nome_arquivo FROM campanha_arquivos ca "
        "JOIN campanhas c ON c.id=ca.campanha_id "
        "WHERE ca.id=? AND ca.campanha_id=? AND c.empresa_id=?",
        (arq_id, campanha_id, empresa_id),
    ) as cur:
        row = await cur.fetchone()
    if row:
        path = os.path.join(UPLOAD_DIR, row["nome_arquivo"])
        try:
            os.remove(path)
        except Exception:
            pass
        await db.execute("DELETE FROM campanha_arquivos WHERE id=?", (arq_id,))
        await db.commit()
    return {"ok": True}


# ── Iniciar / Pausar campanha ────────────────────────────────────────────────

@router.post("/{campanha_id}/iniciar")
async def iniciar_campanha(campanha_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    async with db.execute(
        "SELECT id, tipo, mensagem, status FROM campanhas WHERE id=? AND empresa_id=?",
        (campanha_id, empresa_id),
    ) as cur:
        camp = await cur.fetchone()
    if not camp:
        raise HTTPException(404, "Campanha não encontrada")
    if camp["status"] == "running":
        raise HTTPException(400, "Campanha já em execução")

    # Se está retomando (paused), não recria os envios
    if camp["status"] in ("draft", "done"):
        # Remove envios antigos
        await db.execute("DELETE FROM campanha_envios WHERE campanha_id=?", (campanha_id,))
        # Cria envios para todos os contatos ativos
        async with db.execute(
            "SELECT phone, nome FROM contatos WHERE empresa_id=? AND ativo=TRUE",
            (empresa_id,),
        ) as cur:
            contatos = await cur.fetchall()
        if not contatos:
            raise HTTPException(400, "Nenhum contato ativo para disparar")
        rows = [(campanha_id, empresa_id, c["phone"], c["nome"] or "") for c in contatos]
        await db.executemany(
            "INSERT INTO campanha_envios (campanha_id, empresa_id, phone, nome, status) VALUES (?,?,?,?,?)",
            [(campanha_id, empresa_id, c["phone"], c["nome"] or "", "queued") for c in contatos],
        )
        total = len(contatos)
        await db.execute(
            "UPDATE campanhas SET status='running', total=?, enviados=0, erros=0, started_at=NOW() WHERE id=?",
            (total, campanha_id),
        )
    else:
        # retoma pausada — apenas muda status
        await db.execute(
            "UPDATE campanha_envios SET status='queued' WHERE campanha_id=? AND status='paused'",
            (campanha_id,),
        )
        await db.execute(
            "UPDATE campanhas SET status='running' WHERE id=?", (campanha_id,)
        )

    await db.commit()
    return {"ok": True}


@router.post("/{campanha_id}/pausar")
async def pausar_campanha(campanha_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    # Muda envios queued → paused
    await db.execute(
        "UPDATE campanha_envios SET status='paused' WHERE campanha_id=? AND status='queued'",
        (campanha_id,),
    )
    await db.execute(
        "UPDATE campanhas SET status='paused' WHERE id=? AND empresa_id=?",
        (campanha_id, empresa_id),
    )
    await db.commit()
    return {"ok": True}


# ── Progresso ────────────────────────────────────────────────────────────────

@router.get("/{campanha_id}/progresso")
async def campanha_progresso(campanha_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    async with db.execute(
        "SELECT status, total, enviados, erros FROM campanhas WHERE id=? AND empresa_id=?",
        (campanha_id, empresa_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Campanha não encontrada")
    return dict(row)
