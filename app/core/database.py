import os
from contextlib import asynccontextmanager
import aiosqlite
from .config import settings


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.database_url)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def get_db_direct():
    db = await aiosqlite.connect(settings.database_url)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    os.makedirs(os.path.dirname(settings.database_url) if os.path.dirname(settings.database_url) else ".", exist_ok=True)
    async with aiosqlite.connect(settings.database_url) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessoes_wa (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                status TEXT DEFAULT 'disconnected',
                qr_data TEXT,
                phone TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id TEXT,
                destinatario TEXT NOT NULL,
                mensagem TEXT,
                tipo TEXT DEFAULT 'text',
                status TEXT DEFAULT 'pending',
                erro TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS arquivos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_original TEXT NOT NULL,
                nome_arquivo TEXT NOT NULL,
                tamanho INTEGER,
                destinatario TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                sent_at TEXT,
                delivered_at TEXT,
                read_at TEXT
            );

            INSERT OR IGNORE INTO usuarios (username, password_hash)
            VALUES ('admin', '$2b$12$Hwep0wwj.dmjNcQ7HEKcsO3gaxCl3Ptuegep21Q7kIxC3f50dhbnm');
        """)
        # Migrações para bancos existentes
        for col, typ in [
            ("sent_at",      "TEXT"),
            ("delivered_at", "TEXT"),
            ("read_at",      "TEXT"),
            ("caption",      "TEXT"),
            ("sessao_id",    "TEXT"),
            ("erro",         "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE arquivos ADD COLUMN {col} {typ}")
            except Exception:
                pass
        await db.executescript("""

            INSERT OR IGNORE INTO config (key, value) VALUES
                ('mensagem_padrao', 'Olá {nome}, obrigado pela sua compra de {valor} em {data}!'),
                ('erp_token', 'meu-token-erp');
        """)
        await db.commit()
