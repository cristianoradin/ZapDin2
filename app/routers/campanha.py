"""
Rotas de Disparo em Massa — Contatos e Campanhas.
"""
import asyncio
import io
import os
import uuid
import logging
from typing import List, Optional

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

# ─────────────────────── Grupos de Contatos ─────────────────────────────────

class GrupoIn(BaseModel):
    nome: str


@router.get("/grupos")
async def list_grupos(db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    async with db.execute(
        """SELECT g.id, g.nome, g.created_at,
                  COUNT(gc.contato_id) AS total
           FROM grupos_contatos g
           LEFT JOIN grupo_contatos gc ON gc.grupo_id = g.id
           WHERE g.empresa_id=?
           GROUP BY g.id, g.nome, g.created_at
           ORDER BY g.nome""",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return result


@router.post("/grupos")
async def create_grupo(body: GrupoIn, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(400, "Nome obrigatório")
    try:
        cur = await db.execute(
            "INSERT INTO grupos_contatos (empresa_id, nome) VALUES (?,?)",
            (empresa_id, nome),
        )
        await db.commit()
        return {"ok": True, "id": cur.lastrowid, "nome": nome}
    except Exception as exc:
        raise HTTPException(400, "Grupo já existe ou erro: " + str(exc))


@router.put("/grupos/{grupo_id}")
async def update_grupo(grupo_id: int, body: GrupoIn, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(400, "Nome obrigatório")
    await db.execute(
        "UPDATE grupos_contatos SET nome=? WHERE id=? AND empresa_id=?",
        (nome, grupo_id, empresa_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/grupos/{grupo_id}")
async def delete_grupo(grupo_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    await db.execute(
        "DELETE FROM grupos_contatos WHERE id=? AND empresa_id=?", (grupo_id, empresa_id)
    )
    await db.commit()
    return {"ok": True}


@router.get("/grupos/{grupo_id}/contatos")
async def list_grupo_contatos(grupo_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    async with db.execute(
        """SELECT c.id, c.phone, c.nome, c.ativo
           FROM contatos c
           JOIN grupo_contatos gc ON gc.contato_id = c.id
           WHERE gc.grupo_id=? AND c.empresa_id=?
           ORDER BY c.nome""",
        (grupo_id, empresa_id),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


class GrupoContatosIn(BaseModel):
    contato_ids: List[int]


@router.post("/grupos/{grupo_id}/contatos")
async def add_grupo_contatos(grupo_id: int, body: GrupoContatosIn, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    # Verifica que o grupo pertence à empresa
    async with db.execute(
        "SELECT id FROM grupos_contatos WHERE id=? AND empresa_id=?", (grupo_id, empresa_id)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Grupo não encontrado")
    added = 0
    for cid in body.contato_ids:
        try:
            await db.execute(
                "INSERT INTO grupo_contatos (grupo_id, contato_id) VALUES (?,?) ON CONFLICT DO NOTHING",
                (grupo_id, cid),
            )
            added += 1
        except Exception:
            pass
    await db.commit()
    return {"ok": True, "adicionados": added}


@router.delete("/grupos/{grupo_id}/contatos/{contato_id}")
async def remove_grupo_contato(grupo_id: int, contato_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    empresa_id = _eid(user)
    await db.execute(
        """DELETE FROM grupo_contatos
           WHERE grupo_id=? AND contato_id=?
             AND grupo_id IN (SELECT id FROM grupos_contatos WHERE empresa_id=?)""",
        (grupo_id, contato_id, empresa_id),
    )
    await db.commit()
    return {"ok": True}


# ─────────────────────── Campanhas ──────────────────────────────────────────

class IniciarPayload(BaseModel):
    contato_ids: Optional[List[int]] = None  # None ou [] = todos os ativos
    grupo_id: Optional[int] = None           # se informado, usa contatos do grupo


@router.post("/{campanha_id}/iniciar")
async def iniciar_campanha(
    campanha_id: int,
    body: IniciarPayload = IniciarPayload(),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
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

    if camp["status"] in ("draft", "done"):
        # Remove envios antigos e recria
        await db.execute("DELETE FROM campanha_envios WHERE campanha_id=?", (campanha_id,))

        # Filtra por grupo, IDs selecionados ou pega todos os ativos
        if body.grupo_id:
            async with db.execute(
                """SELECT c.phone, c.nome FROM contatos c
                   JOIN grupo_contatos gc ON gc.contato_id = c.id
                   WHERE gc.grupo_id=? AND c.empresa_id=? AND c.ativo=TRUE
                   ORDER BY c.nome""",
                (body.grupo_id, empresa_id),
            ) as cur:
                contatos = await cur.fetchall()
        elif body.contato_ids:
            placeholders = ",".join("?" * len(body.contato_ids))
            async with db.execute(
                f"SELECT phone, nome FROM contatos WHERE empresa_id=? AND ativo=TRUE AND id IN ({placeholders})",
                (empresa_id, *body.contato_ids),
            ) as cur:
                contatos = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT phone, nome FROM contatos WHERE empresa_id=? AND ativo=TRUE ORDER BY nome",
                (empresa_id,),
            ) as cur:
                contatos = await cur.fetchall()

        if not contatos:
            raise HTTPException(400, "Nenhum contato selecionado para disparar")

        await db.executemany(
            "INSERT INTO campanha_envios (campanha_id, empresa_id, phone, nome, status) VALUES (?,?,?,?,?)",
            [(campanha_id, empresa_id, c["phone"], c["nome"] or "", "queued") for c in contatos],
        )
        await db.execute(
            "UPDATE campanhas SET status='running', total=?, enviados=0, erros=0, started_at=NOW() WHERE id=?",
            (len(contatos), campanha_id),
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


# ── Status da fila / worker ───────────────────────────────────────────────────

@router.get("/queue/status")
async def queue_status(db=Depends(get_db), user=Depends(get_current_user)):
    """Retorna estado do worker e contadores de fila para esta empresa."""
    from ..services import queue_worker
    empresa_id = _eid(user)

    # Contadores por tabela
    async with db.execute(
        "SELECT status, COUNT(*) as cnt FROM mensagens WHERE empresa_id=? GROUP BY status",
        (empresa_id,),
    ) as cur:
        msg_rows = await cur.fetchall()

    async with db.execute(
        "SELECT status, COUNT(*) as cnt FROM arquivos WHERE empresa_id=? GROUP BY status",
        (empresa_id,),
    ) as cur:
        arq_rows = await cur.fetchall()

    async with db.execute(
        "SELECT ce.status, COUNT(*) as cnt FROM campanha_envios ce "
        "WHERE ce.empresa_id=? GROUP BY ce.status",
        (empresa_id,),
    ) as cur:
        env_rows = await cur.fetchall()

    def _to_map(rows):
        return {r["status"]: r["cnt"] for r in rows}

    return {
        "worker": queue_worker.worker_status(),
        "mensagens": _to_map(msg_rows),
        "arquivos": _to_map(arq_rows),
        "campanha_envios": _to_map(env_rows),
    }


@router.post("/queue/restart")
async def queue_restart(user=Depends(get_current_user)):
    """Para e reinicia o queue worker (sem reiniciar o app)."""
    from ..services import queue_worker
    queue_worker.stop()
    await asyncio.sleep(0.5)
    queue_worker.start()
    return {"ok": True, "status": queue_worker.worker_status()}
