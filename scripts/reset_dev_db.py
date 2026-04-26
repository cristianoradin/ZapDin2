#!/usr/bin/env python3
"""
ZapDin — Reset de Banco de Dados para Desenvolvimento / Produção
=================================================================
Execute este script com os serviços PARADOS para zerar os bancos de dados
mantendo apenas os usuários master de cada sistema.

Uso:
    python scripts/reset_dev_db.py

Usuários mantidos após o reset:
    App (app.db)     → admin / admin
    Monitor (monitor.db) → cristiano / radin123
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DB = os.path.join(BASE, "data", "app.db")
MONITOR_DB = os.path.join(BASE, "data", "monitor.db")


def reset_app_db():
    if not os.path.exists(APP_DB):
        print(f"[app.db] Não encontrado em {APP_DB} — será criado pelo init_db ao iniciar o serviço.")
        return

    conn = sqlite3.connect(APP_DB)
    try:
        # Remove usuários de teste, mantém admin
        conn.execute("DELETE FROM usuarios WHERE username != 'admin'")
        # Garante senha admin=admin (bcrypt hash de 'admin')
        conn.execute(
            "UPDATE usuarios SET password_hash=? WHERE username='admin'",
            ("$2b$12$Hwep0wwj.dmjNcQ7HEKcsO3gaxCl3Ptuegep21Q7kIxC3f50dhbnm",),
        )
        conn.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='usuarios'")

        # Limpa dados operacionais
        conn.execute("DELETE FROM arquivos")
        conn.execute("DELETE FROM mensagens")
        conn.execute("DELETE FROM sessoes_wa")

        # Reseta sequences
        for tbl in ("arquivos", "mensagens"):
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{tbl}'")

        conn.commit()
        print("[app.db] Reset concluído — admin/admin mantido, dados de teste removidos.")
    except Exception as e:
        print(f"[app.db] ERRO: {e}")
        conn.rollback()
    finally:
        conn.close()


def reset_monitor_db():
    if not os.path.exists(MONITOR_DB):
        print(f"[monitor.db] Não encontrado em {MONITOR_DB} — será criado pelo init_db ao iniciar o serviço.")
        return

    conn = sqlite3.connect(MONITOR_DB)
    try:
        # Remove usuários de teste, mantém cristiano
        conn.execute("DELETE FROM usuarios WHERE username != 'cristiano'")
        conn.execute(
            "UPDATE usuarios SET password_hash=? WHERE username='cristiano'",
            ("$2b$12$Mco23X5AA8/pnXclNHGS7eMqlVEfou.ww4k1XVJQPa8HIL.Bzs30S",),
        )
        conn.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='usuarios'")

        # Limpa dados de telemetria
        conn.execute("DELETE FROM heartbeats")
        conn.execute("DELETE FROM historico")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='heartbeats'")

        # Remove vínculos de usuários removidos
        conn.execute("DELETE FROM usuario_clientes WHERE usuario_id != 1")

        conn.commit()
        print("[monitor.db] Reset concluído — cristiano/radin123 mantido, telemetria removida.")
    except Exception as e:
        print(f"[monitor.db] ERRO: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("  ZapDin — Reset de Banco de Dados")
    print("  ATENÇÃO: Pare os serviços antes de executar!")
    print("=" * 60)

    resp = input("\nConfirmar reset? (s/N): ").strip().lower()
    if resp != "s":
        print("Cancelado.")
        sys.exit(0)

    print()
    reset_app_db()
    reset_monitor_db()
    print("\nPronto. Reinicie os serviços normalmente.")
