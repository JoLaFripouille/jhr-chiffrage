#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$project_dir"
if ! command -v git >/dev/null 2>&1; then
  echo "Git est requis pour mettre à jour depuis GitHub." >&2
  exit 1
fi
if [[ ! -d .git ]] || [[ "$(git rev-parse --show-toplevel)" != "$project_dir" ]]; then
  echo "Cette commande doit être utilisée dans le dépôt cloné depuis GitHub." >&2
  exit 1
fi
origin="$(git remote get-url origin)"
case "$origin" in
  https://github.com/JoLaFripouille/jhr-chiffrage|https://github.com/JoLaFripouille/jhr-chiffrage.git|git@github.com:JoLaFripouille/jhr-chiffrage|git@github.com:JoLaFripouille/jhr-chiffrage.git|ssh://git@github.com/JoLaFripouille/jhr-chiffrage|ssh://git@github.com/JoLaFripouille/jhr-chiffrage.git) ;;
  *) echo "Mise à jour arrêtée : l'origine n'est pas le dépôt JHR Chiffrage attendu." >&2; exit 1 ;;
esac
if [[ "$(git branch --show-current)" != main ]]; then
  echo "Mise à jour arrêtée : le dépôt doit être sur la branche main." >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Mise à jour arrêtée : des fichiers suivis ont été modifiés localement." >&2
  echo "Conservez ou annulez volontairement vos modifications avant de réessayer." >&2
  exit 1
fi
echo "Récupération de la dernière version depuis GitHub…"
git pull --ff-only origin main
exec bash "$project_dir/installer.sh"
