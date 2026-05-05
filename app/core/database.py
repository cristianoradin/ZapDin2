"""
app/core/database.py — PostgreSQL multi-tenant via asyncpg.

Cada empresa (CNPJ ativado) tem um registro em `empresas`.
Todos os dados (usuarios, config, sessoes_wa, mensagens, arquivos)
são isolados por empresa_id.
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


@asynccontextmanager
async def get_db_direct():
    async with _pool.acquire() as conn:
        yield AsyncPGAdapter(conn)


# ── Inicialização multi-tenant ────────────────────────────────────────────────

async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)

    async with _pool.acquire() as conn:

        # ── Empresas (tenants) ────────────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS empresas (
                id         BIGSERIAL PRIMARY KEY,
                cnpj       TEXT UNIQUE NOT NULL,
                nome       TEXT NOT NULL,
                token      TEXT UNIQUE NOT NULL,
                ativo      BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # ── Usuários (scoped por empresa) ─────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id            BIGSERIAL PRIMARY KEY,
                empresa_id    BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                username      TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (empresa_id, username)
            )
        """)

        # ── Config por empresa ────────────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                empresa_id BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                PRIMARY KEY (empresa_id, key)
            )
        """)

        # ── Sessões WhatsApp por empresa ──────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sessoes_wa (
                empresa_id BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                id         TEXT NOT NULL,
                nome       TEXT NOT NULL,
                status     TEXT DEFAULT 'disconnected',
                qr_data    TEXT,
                phone      TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_seen  TIMESTAMPTZ,
                PRIMARY KEY (empresa_id, id)
            )
        """)

        # ── Mensagens por empresa ─────────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id           BIGSERIAL PRIMARY KEY,
                empresa_id   BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                sessao_id    TEXT,
                destinatario TEXT NOT NULL,
                mensagem     TEXT,
                tipo         TEXT DEFAULT 'text',
                status       TEXT DEFAULT 'pending',
                erro         TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                sent_at      TIMESTAMPTZ
            )
        """)
        # ── Arquivos por empresa ──────────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS arquivos (
                id            BIGSERIAL PRIMARY KEY,
                empresa_id    BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
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

        # ── Migration: adiciona empresa_id em tabelas que já existiam ─────────
        # Necessário ao atualizar banco single-tenant → multi-tenant.
        # ADD COLUMN IF NOT EXISTS é idempotente — seguro rodar sempre.
        for _tbl in ('usuarios', 'config', 'sessoes_wa', 'mensagens', 'arquivos'):
            try:
                await conn.execute(
                    f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS empresa_id BIGINT"
                )
            except Exception:
                pass

        # Corrige PRIMARY KEY de config: (key) → (empresa_id, key)
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_name = 'config'
                     AND tc.constraint_type = 'PRIMARY KEY'
                     AND kcu.column_name = 'key'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.key_column_usage kcu2
                    JOIN information_schema.table_constraints tc2
                      ON kcu2.constraint_name = tc2.constraint_name
                     AND tc2.table_name = 'config'
                     AND tc2.constraint_type = 'PRIMARY KEY'
                     AND kcu2.column_name = 'empresa_id'
                ) THEN
                    ALTER TABLE config DROP CONSTRAINT config_pkey;
                    ALTER TABLE config ADD PRIMARY KEY (empresa_id, key);
                END IF;
            END $$;
        """)

        # Corrige PRIMARY KEY de sessoes_wa: (id) → (empresa_id, id)
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_name = 'sessoes_wa'
                     AND tc.constraint_type = 'PRIMARY KEY'
                     AND kcu.column_name = 'id'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.key_column_usage kcu2
                    JOIN information_schema.table_constraints tc2
                      ON kcu2.constraint_name = tc2.constraint_name
                     AND tc2.table_name = 'sessoes_wa'
                     AND tc2.constraint_type = 'PRIMARY KEY'
                     AND kcu2.column_name = 'empresa_id'
                ) THEN
                    ALTER TABLE sessoes_wa DROP CONSTRAINT sessoes_wa_pkey;
                    ALTER TABLE sessoes_wa ADD PRIMARY KEY (empresa_id, id);
                END IF;
            END $$;
        """)

        # Corrige UNIQUE de usuarios: (username) → (empresa_id, username)
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'usuarios'
                      AND constraint_type = 'UNIQUE'
                      AND constraint_name = 'usuarios_username_key'
                ) THEN
                    ALTER TABLE usuarios DROP CONSTRAINT usuarios_username_key;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'usuarios'
                      AND constraint_type = 'UNIQUE'
                      AND constraint_name = 'usuarios_empresa_id_username_key'
                ) THEN
                    ALTER TABLE usuarios
                        ADD CONSTRAINT usuarios_empresa_id_username_key
                        UNIQUE (empresa_id, username);
                END IF;
            END $$;
        """)

        # Índices (seguros mesmo se coluna empresa_id foi adicionada agora)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_mensagens_empresa ON mensagens(empresa_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_mensagens_status ON mensagens(empresa_id, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_arquivos_empresa ON arquivos(empresa_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_arquivos_status ON arquivos(empresa_id, status)")
