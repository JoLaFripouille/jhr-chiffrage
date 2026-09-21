#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# In a release ZIP this script is beside pyproject.toml; in Git it is under packaging/linux.
if [[ -f "$script_dir/pyproject.toml" ]]; then
  bundle_dir="$script_dir"
else
  bundle_dir="$(cd -- "$script_dir/../.." && pwd)"
fi
if [[ ! -f "$bundle_dir/pyproject.toml" ]] || [[ ! -f "$script_dir/install_launcher.py" ]]; then
  echo "Installation impossible : le dossier de l'application est incomplet." >&2
  exit 1
fi
python_bin="${JHR_PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3.11 ou plus récent est requis. Consultez LIRE-MOI-UBUNTU.md." >&2
  exit 1
fi
"$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 minimum requis ; Ubuntu 22.04 fournit normalement Python 3.10.")'
version="$("$python_bin" -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$bundle_dir/pyproject.toml")"
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/jhr-chiffrage/versions"
mkdir -p -- "$install_root"
install_dir="$(mktemp -d "$install_root/$version-XXXXXXXX")"
echo "Installation privée dans : $install_dir"
if ! "$python_bin" -m venv "$install_dir"; then
  echo "La création de l'environnement Python a échoué (module venv/ensurepip manquant ?)." >&2
  echo "Consultez LIRE-MOI-UBUNTU.md. Aucun paquet système n'a été installé." >&2
  exit 1
fi
if ! "$install_dir/bin/python" -m pip install "$bundle_dir"; then
  echo "Installation incomplète : vérifiez Internet et les messages ci-dessus." >&2
  echo "Votre base de données n'a pas été ouverte ni modifiée." >&2
  exit 1
fi
"$install_dir/bin/python" "$script_dir/install_launcher.py" "$install_dir"
