"""
Script de diagnóstico — roda na raiz do projeto com o venv:
  app/.venv/bin/python diagnostico.py
"""
import sys, os, socket
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=== Diagnóstico ZapDin App ===\n")

all_ok = True

# ── 1. Verifica .env ──────────────────────────────────────────────────────────
env_path = os.path.join("app", ".env")
print(f"[1] .env: {env_path}")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key = line.split("=")[0]
                print(f"    {key}=...")
else:
    print("    ❌ ARQUIVO NÃO ENCONTRADO!")
    all_ok = False

print()

# ── 2. Verifica porta ─────────────────────────────────────────────────────────
print("[2] Porta 4000:")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    result = s.connect_ex(('127.0.0.1', 4000))
    s.close()
    if result == 0:
        print("    ⚠️  PORTA 4000 JÁ OCUPADA — outro processo está rodando")
        import subprocess
        pid = subprocess.run(['lsof', '-ti', 'tcp:4000'], capture_output=True, text=True).stdout.strip()
        if pid:
            print(f"    PID ocupando: {pid}")
    else:
        print("    OK — porta livre")
except Exception as e:
    print(f"    Erro ao verificar porta: {e}")

print()

# ── 3. Testa dependências ─────────────────────────────────────────────────────
imports = [
    ("fastapi",           "fastapi"),
    ("uvicorn",           "uvicorn"),
    ("socketio",          "python-socketio"),
    ("aiosqlite",         "aiosqlite"),
    ("pydantic_settings", "pydantic-settings"),
    ("httpx",             "httpx"),
    ("cryptography",      "cryptography"),
    ("playwright",        "playwright"),
    ("itsdangerous",      "itsdangerous"),
    ("starlette",         "starlette"),
]

print("[3] Dependências:")
for mod, pkg in imports:
    try:
        m = __import__(mod)
        ver = getattr(m, '__version__', '?')
        print(f"    ✅ {pkg} ({ver})")
    except ImportError as e:
        print(f"    ❌ FALTANDO: {pkg} — {e}")
        all_ok = False

print()

# ── 4. Testa import da app ────────────────────────────────────────────────────
print("[4] Import da aplicação (app.main):")
try:
    from app.core.config import settings
    print(f"    ✅ config carregada — APP_STATE={settings.app_state}, PORT={settings.port}")
    print(f"    DATABASE_URL={settings.database_url}")
except Exception as e:
    import traceback
    print("    ❌ ERRO ao carregar config:")
    traceback.print_exc()
    all_ok = False

print()

# ── 5. Verifica banco de dados ────────────────────────────────────────────────
print("[5] Banco de dados:")
try:
    from app.core.config import settings as _s
    db_path = _s.database_url
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.getcwd(), db_path)
    print(f"    Caminho: {db_path}")
    if os.path.exists(db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        print(f"    ✅ DB acessível — tabelas: {', '.join(tables)}")
        # Conta sessões
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM sessoes_wa").fetchone()[0]
        conn.close()
        print(f"    Sessões WhatsApp cadastradas: {count}")
    else:
        print(f"    ⚠️  DB não existe ainda (será criado no primeiro start)")
except Exception as e:
    print(f"    ❌ ERRO: {e}")

print()

# ── 6. Verifica Playwright browsers ──────────────────────────────────────────
print("[6] Playwright Chromium:")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        # Só verifica se o executável existe, não lança browser
        exe = p.chromium.executable_path
        if os.path.exists(exe):
            print(f"    ✅ Chromium encontrado: {exe}")
        else:
            print(f"    ❌ Chromium NÃO instalado: {exe}")
            print("    Execute: app/.venv/bin/playwright install chromium")
            all_ok = False
except Exception as e:
    print(f"    ❌ ERRO: {e}")
    print("    Execute: app/.venv/bin/playwright install chromium")
    all_ok = False

print()

# ── Resultado final ───────────────────────────────────────────────────────────
if all_ok:
    print("✅ Tudo OK. Se ainda não abre, verifique o log: app_startup.log")
else:
    print("❌ Problemas encontrados. Corrija os itens marcados com ❌ acima.")

print()
input("Pressione Enter para fechar...")
