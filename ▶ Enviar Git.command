#!/bin/bash
# ▶ Enviar Git.command
# Double-click no Finder para commitar e enviar todas as mudanças ao GitHub

cd "$(dirname "$0")"

echo "========================================"
echo "  ZapDin — Enviar atualizações ao GitHub"
echo "========================================"
echo ""

# Remove lock files se existirem
rm -f .git/HEAD.lock .git/index.lock .git/refs/heads/*.lock 2>/dev/null
echo "✓ Lock files removidos"

# Verifica se há algo para commitar
STATUS=$(git status --porcelain | grep -v "\.db$" | grep -v "push.*\.command$" | grep -v "ziQ9FYHd")
if [ -z "$STATUS" ]; then
  echo ""
  echo "Nada para commitar. Tudo está atualizado."
  echo ""
  read -p "Pressione ENTER para fechar..."
  exit 0
fi

echo ""
echo "Arquivos modificados:"
echo "$STATUS"
echo ""

# Pede mensagem do commit
read -p "Mensagem do commit (ENTER para mensagem automática): " MSG
if [ -z "$MSG" ]; then
  MSG="chore: atualização $(date '+%Y-%m-%d %H:%M')"
fi

echo ""

# Adiciona todos os arquivos rastreados modificados (exceto .db e arquivos temporários)
git add .github/
git add app/ --update
git add monitor/ --update
git add installer/ 2>/dev/null
git add INSTALAR.bat 2>/dev/null
git add *.iss 2>/dev/null
git add *.sh 2>/dev/null
git add *.command 2>/dev/null

echo "✓ Arquivos adicionados ao staging"

# Commit
git commit -m "$MSG"
if [ $? -ne 0 ]; then
  echo ""
  echo "❌ Erro no commit. Verifique as mensagens acima."
  read -p "Pressione ENTER para fechar..."
  exit 1
fi

echo ""
echo "✓ Commit criado"
echo ""

# Push
git push origin main
if [ $? -eq 0 ]; then
  echo ""
  echo "✅ Push realizado com sucesso!"
  echo "   O build no GitHub Actions vai iniciar em instantes."
  echo "   Acompanhe em: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/\.git$//')/actions"
else
  echo ""
  echo "❌ Erro no push. Verifique sua conexão ou credenciais."
fi

echo ""
read -p "Pressione ENTER para fechar..."
