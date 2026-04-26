import os
import aiosqlite
from .config import settings


async def get_db() -> aiosqlite.Connection:
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
                menus TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS representantes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT,
                telefone TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS grupos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE NOT NULL,
                descricao TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                cnpj TEXT,
                token TEXT UNIQUE NOT NULL,
                representante_id INTEGER REFERENCES representantes(id),
                ativo INTEGER DEFAULT 1,
                versao_instalada TEXT DEFAULT '0.0.0',
                endereco TEXT,
                cidade TEXT,
                uf TEXT,
                -- Token de ativação one-time (gerado pelo painel, usado no first-run)
                activation_token TEXT,
                -- Token ERP sugerido (entregue cifrado ao App na ativação)
                erp_token_hint TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL REFERENCES clientes(id),
                evento TEXT NOT NULL,
                detalhe TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL REFERENCES clientes(id),
                versao TEXT,
                ip TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Relação N:N entre usuários e clientes (controle de acesso)
            CREATE TABLE IF NOT EXISTS usuario_clientes (
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (usuario_id, cliente_id)
            );

            CREATE TABLE IF NOT EXISTS versoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app TEXT UNIQUE NOT NULL,
                versao TEXT NOT NULL,
                url_download TEXT,
                notas TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            -- Admin default: cristiano / radin123
            INSERT OR IGNORE INTO usuarios (username, password_hash)
            VALUES ('cristiano', '$2b$12$Mco23X5AA8/pnXclNHGS7eMqlVEfou.ww4k1XVJQPa8HIL.Bzs30S');

            INSERT OR IGNORE INTO versoes (app, versao)
            VALUES ('whatsapp', '1.0.0');
        """)
        # Migrações para bancos existentes — tabela clientes
        for col, typ in [
            ("activation_token", "TEXT"),
            ("erp_token_hint",   "TEXT"),
            ("grupo_id",         "INTEGER REFERENCES grupos(id)"),
        ]:
            try:
                await db.execute(f"ALTER TABLE clientes ADD COLUMN {col} {typ}")
            except Exception:
                pass  # coluna já existe

        # Migrações para bancos existentes — tabela usuarios
        for col, typ in [
            ("menus", "TEXT"),  # JSON array de menus permitidos no app; NULL = todos
        ]:
            try:
                await db.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {typ}")
            except Exception:
                pass  # coluna já existe

        await db.commit()
