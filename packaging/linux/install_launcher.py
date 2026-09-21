"""Check the Qt runtime and create per-user launchers, without opening the DB."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import shutil


def desktop_quote(value):
    value = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def main():
    install = Path(sys.argv[1]).resolve()
    try:
        import PySide6
        from PySide6.QtWidgets import QApplication  # noqa: F401
        qt_dir = Path(PySide6.__file__).parent / "Qt"
        missing = []
        for library in (qt_dir / "plugins/platforms/libqxcb.so", qt_dir / "lib/libQt6Gui.so.6"):
            result = subprocess.run(["ldd", str(library)], capture_output=True, text=True)
            missing.extend(line.strip() for line in result.stdout.splitlines() if "not found" in line)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or f"Impossible de vérifier {library}")
        if missing:
            raise RuntimeError("Bibliothèques système absentes :\n" + "\n".join(sorted(set(missing))))
    except (ImportError, OSError, RuntimeError) as error:
        sys.exit(f"Qt ne peut pas encore fonctionner : {error}\nConsultez LIRE-MOI-UBUNTU.md ; aucun paquet système n'a été installé.")

    bin_dir = Path.home() / ".local/bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, module in (("jhr-chiffrage", "jhr_chiffrage"), ("jhr-chiffrage-mcp", "jhr_chiffrage.mcp_server")):
        launcher = bin_dir / name
        launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(str(install / 'bin/python'))} -m {module} \"$@\"\n", encoding="utf-8")
        launcher.chmod(0o755)
    apps = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    import jhr_chiffrage
    icon_dir = apps.parent / "icons/hicolor/512x512/apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(jhr_chiffrage.__file__).parent / "assets/chiffrage.png", icon_dir / "jhr-chiffrage.png")
    (apps / "jhr-chiffrage.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=JHR Chiffrage\n"
        "Comment=Chiffrage des études et dessins\n"
        f"Exec={desktop_quote(str(bin_dir / 'jhr-chiffrage'))}\n"
        "Icon=jhr-chiffrage\nTerminal=false\nCategories=Office;\n", encoding="utf-8")
    print("Installation terminée. Ouvrez JHR Chiffrage depuis le menu des applications.")
    print(f"Ou exécutez : {shlex.quote(str(bin_dir / 'jhr-chiffrage'))}")
    print("Aucune base de données n'a été ouverte ni modifiée par l'installation.")


if __name__ == "__main__":
    main()
