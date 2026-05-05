"""
app/core/database.py — Camada de acesso ao PostgreSQL via asyncpg.

O AsyncPGAdapter emula a interface do aiosqlite para que os routers
não precisem de grandes alterações:
  - execute(sql, params)  → _Cursor  (await ou async with)
  - cursor.fetchone()     → Record | None
  - cursor.fetchall()     → list[Record]
  - cursor.lastrowid      → int | None  (via RETURNING id)
  - commit()              → no-op (autocommit fora de transação)
"""
from __future__ import annotations

import asyncpg
from contextlib import asynccontextmanager
from .config import settings


# ── Pool global ───────────────────────────────────────────────────────────────
_pool: asyncpg.Pool | None = None


def _to_pg(sql: str) -> str:
    """Converte placeholders SQLite '?' → '$1', '$2', ... do PostgreSQL."""
    n, out = 0, []
    for ch in sql:
        if ch == '?':
            n += 1
            out.append(f'${n}')
        else:
            out.append(ch)
    return ''.join(out)


# ── Cursor proxy ──────────────────────────────────────────────────────────────

class _Cursor:
    __slots__ = ('lastrowid', '_rows')

    def __init__(self, rows=None, lastrowid: int | None = None):
        self.lastrowid = lastrowid
        self._rows: list = rows if rows is not None else []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _ExecProxy:
    """Retornado por execute() — suporta tanto 'await expr' quanto 'async with expr'."""
    __slots__ = ('_coro',)

    def __init__(self, coro):
        self._coro = coro

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self):
        return await self._coro

    async def __aexit__(self, *_):
        pass


# ── Adapter principal ─────────────────────────────────────────────────────────

class AsyncPGAdapter:
    """Envolve asyncpg.Connection e expõe interface compatível com aiosqlite."""

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> _ExecProxy:
        return _ExecProxy(self._run(sql, params))

    async def _run(self, sql: str, params: tuple) -> _Cursor:
        pg = _to_pg(sql)
        args = list(params)
        head = pg.lstrip().upper()

        # SELECT / WITH → retorna linhas
        if head.startswith('SELECT') or head.startswith('WITH'):
            rows = await self._conn.fetch(pg, *args)
            return _Cursor(rows=rows)

        # INSERT → tenta capturar id via RETURNING
        if head.startswith('INSERT') and 'RETURNING' not in head:
            pg_ret = pg.rstrip().rstrip(';') + ' RETURNING id'
            try:
                row = await self._conn.fetchrow(pg_ret, *args)
                return _Cursor(lastrowid=row['id'] if row else None)
            except (asyncpg.UndefinedColumnError, asyncpg.PostgresSyntaxError,
                    asyncpg.UndefinedFunctionError):
                await self._conn.execute(pg, *args)
                return _Cursor()

        await self._conn.execute(pg, *args)
        return _Cursor()

    async def commit(self):
        """No-op: asyncpg faz autocommit fora de transações explícitas."""

    async def executemany(self, sql: str, params_list):
        pg = _to_pg(sql)
        await self._conn.executemany(pg, [list(p) for p in params_list])

    async def executescript(self, script: str):
        """Executa múltiplos statements separados por ';'."""
        for stmt in script.split(';'):
            s = stmt.strip()
            if s:
                await self._conn.execute(s)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db():
    async with _pool.acquire() as conn:
        yield AsyncPGAdapter(conn)


@asynccontextmanager
async def get_db_direct():
    async with _pool.acquire() as conn:
        yield AsyncPGAdapter(conn)


# ── Inicialização do banco ────────────────────────────────────────────────────

async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id            BIGSERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sessoes_wa (
                id         TEXT PRIMARY KEY,
                nome       TEXT NOT NULL,
                status     TEXT DEFAULT 'disconnected',
                qr_data    TEXT,
                phone      TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_seen  TIMESTAMPTZ
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id          BIGSERIAL PRIMARY KEY,
                sessao_id   TEXT,
                destinatario TEXT NOT NULL,
                mensagem    TEXT,
                tipo        TEXT DEFAULT 'text',
                status      TEXT DEFAULT 'pending',
                erro        TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                sent_at     TIMESTAMPTZ
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS arquivos (
                id            BIGSERIAL PRIMARY KEY,
                nome_original TEXT NOT NULL,
                nome_arquivo  TEXT NOT NULL,
                tamanho       INTEGER,
                destinatario  TEXT,
                sessao_id     TEXT,
                caption       TEXT,
                status        TEXT DEFAULT 'pending',
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                sent_at       TIMESTAMPTZ,
                delivered_at  TIMESTAMPTZ,
                read_at       TIMESTAMPTZ,
                erro          TEXT
            )
        """)
        # Seed: admin padrão (senha: admin123)
        await conn.execute("""
            INSERT INTO usuarios (username, password_hash)
            VALUES ('admin', '$2b$12$Hwep0wwj.dmjNcQ7HEKcsO3gaxCl3Ptuegep21Q7kIxC3f50dhbnm')
            ON CONFLICT DO NOTHING
        """)
        # Config padrão
        await conn.execute("""
            INSERT INTO config (key, value)
            VALUES
                ('mensagem_padrao', 'Olá {nome}, obrigado pela sua compra de {valor} em {data}!'),
                ('wa_delay_min',    '5'),
                ('wa_delay_max',    '15'),
                ('wa_daily_limit',  '100'),
                ('wa_hora_inicio',  '08:00'),
                ('wa_hora_fim',     '18:00'),
                ('wa_spintax',      '1')
            ON CONFLICT DO NOTHING
        """)
