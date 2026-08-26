#!/usr/bin/env bash
# Publica o ProfessorVGC como repositório PRIVADO no seu GitHub a partir do bundle.
# Uso: bash publish_github.sh [nome-do-repo]
# Requer: git + GitHub CLI (gh) autenticado (gh auth login).
set -euo pipefail

REPO_NAME="${1:-oracle-vgc}"
BUNDLE="oracle-vgc.bundle"

command -v git >/dev/null || { echo "git não encontrado."; exit 1; }
command -v gh  >/dev/null || { echo "GitHub CLI (gh) não encontrado. Use a Opção B do PUBLISH_TO_GITHUB.md."; exit 1; }
[ -f "$BUNDLE" ] || { echo "Arquivo $BUNDLE não encontrado nesta pasta."; exit 1; }

echo ">> Clonando o bundle..."
rm -rf "$REPO_NAME"
git clone "$BUNDLE" "$REPO_NAME"
cd "$REPO_NAME"
git branch -M main

echo ">> Criando repositório privado '$REPO_NAME' e fazendo push..."
gh repo create "$REPO_NAME" --private --source=. --remote=origin --push

echo ">> Pronto! Repositório privado criado e enviado."
gh repo view --web >/dev/null 2>&1 || true
