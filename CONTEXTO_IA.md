# ZapDin2 — Contexto para IA

Arquivo de referência rápida para retomar sessões de trabalho com IA.
Atualizado em: 2026-04-28

---

## Estado atual do projeto

- **Versão:** 1.0.1 (app/versao.json)
- **GitHub:** https://github.com/cristianoradin/ZapDin2
- **Monitor produção:** http://zapdin.gruposgapetro.com.br:5000
- **CI:** GitHub Actions — build-installer.yml gera ZapDin-Setup-X.X.X.exe

## Pendências abertas

- [ ] CI não está gerando o setup exe — adicionado diagnóstico (`fail_on_unmatched_files: true` + step de verificação). Aguardando próximo build para identificar o passo que falha.
- [ ] Instalação Windows do cliente precisa ser validada com o setup gerado pelo novo CI
- [ ] NSSM service no cliente testado apontava para start_app.bat (instalação antiga) — corrigir no novo instalador

## Arquitetura resumida

```
app/          → Cliente Windows (porta 4000) — FastAPI + Playwright + SQLite
monitor/      → Servidor Linux (porta 5000) — Painel admin + licenças
```

## Regras críticas (não esquecer)

1. **NUNCA rodar git do sandbox** — cria lock files. Todo push via terminal do Mac ou `▶ Enviar Git.command`
2. **pywebview fora do requirements.txt** — só instalar no CI antes do PyInstaller
3. **ICO no workflow** — usar `run: |` + python one-liner, nunca here-string PowerShell
4. **Inno Setup paths** — .iss em `installer/` → usar `..\payload\` para referenciar raiz
5. **Para parar serviços Windows** — parar ZapDinWorker antes de ZapDinApp
6. **bcrypt** — pode faltar no venv do cliente: `pip install bcrypt`
7. **sc no PowerShell** — usar `sc.exe` (não `sc` que é alias de Set-Content)

## Como fazer push

```bash
cd ~/Zapdin2
rm -f .git/HEAD.lock .git/index.lock
git add <arquivos>
git commit -m "mensagem"
git push origin main
```

Ou double-click em `▶ Enviar Git.command` na pasta do projeto.

## Como iniciar serviços locais (Mac)

Double-click em:
- `▶ Iniciar App.command` → app na porta 4000
- `▶ Iniciar Monitor.command` → monitor na porta 5000

## Separação de usuários no monitor (implementado abril/2026)

- **Admins do Monitor** → tabela `admins` — acesso apenas ao painel monitor
- **Usuários do App** → tabela `usuarios` — acesso ao app de envio (com menus e clientes)
- Login verifica `admins` primeiro, depois `usuarios`

## CI — tempo esperado de build

- 1ª vez (sem cache): ~45 min
- Com cache (Nuitka + Playwright + pip): ~10 min
- Trigger automático: push em app/**, monitor/**, installer/**, .github/workflows/**
- Trigger manual: GitHub Actions → "Run workflow"
