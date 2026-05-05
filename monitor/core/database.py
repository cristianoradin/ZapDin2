"""
monitor/core/database.py — Camada de acesso ao PostgreSQL via asyncpg.
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

        if head.startswith('SELECT') or head.startswith('WITH'):
            rows = await self._conn.fetch(pg, *args)
            return _Cursor(rows=rows)

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
        for stmt in script.split(';'):
            s = stmt.strip()
            if s:
                await self._conn.execute(s)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db():
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
                menus         TEXT,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS representantes (
                id         BIGSERIAL PRIMARY KEY,
                nome       TEXT NOT NULL,
                email      TEXT,
                telefone   TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS grupos (
                id         BIGSERIAL PRIMARY KEY,
                nome       TEXT UNIQUE NOT NULL,
                descricao  TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id                BIGSERIAL PRIMARY KEY,
                nome              TEXT NOT NULL,
                cnpj              TEXT,
                token             TEXT UNIQUE NOT NULL,
                representante_id  BIGINT REFERENCES representantes(id),
                grupo_id          BIGINT REFERENCES grupos(id),
                ativo             INTEGER DEFAULT 1,
                versao_instalada  TEXT DEFAULT '0.0.0',
                endereco          TEXT,
                cidade            TEXT,
                uf                TEXT,
                activation_token  TEXT,
                erp_token_hint    TEXT,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id         BIGSERIAL PRIMARY KEY,
                cliente_id BIGINT NOT NULL REFERENCES clientes(id),
                evento     TEXT NOT NULL,
                detalhe    TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeats (
                id         BIGSERIAL PRIMARY KEY,
                cliente_id BIGINT NOT NULL REFERENCES clientes(id),
                versao     TEXT,
                ip         TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usuario_clientes (
                usuario_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                cliente_id BIGINT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (usuario_id, cliente_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS versoes (
                id           BIGSERIAL PRIMARY KEY,
                app          TEXT UNIQUE NOT NULL,
                versao       TEXT NOT NULL,
                url_download TEXT,
                notas        TEXT,
                updated_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id            BIGSERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Seeds
        await conn.execute("""
            INSERT INTO usuarios (username, password_hash)
            VALUES ('cristiano', '$2b$12$Mco23X5AA8/pnXclNHGS7eMqlVEfou.ww4k1XVJQPa8HIL.Bzs30S')
            ON CONFLICT DO NOTHING
        """)
        await conn.execute("""
            INSERT INTO admins (username, password_hash)
            VALUES ('cristiano', '$2b$12$Mco23X5AA8/pnXclNHGS7eMqlVEfou.ww4k1XVJQPa8HIL.Bzs30S')
            ON CONFLICT DO NOTHING
        """)
        await conn.execute("""
            INSERT INTO versoes (app, versao)
            VALUES ('whatsapp', '1.0.0')
            ON CONFLICT DO NOTHING
        """)
