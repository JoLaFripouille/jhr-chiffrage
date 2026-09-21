"""Build an allowlisted, source-only Ubuntu bundle. Never include private DBs."""
from pathlib import Path
import hashlib
import tomllib
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    prefix = f"JHRChiffrage-{version}-Ubuntu"
    output = root / "dist" / f"{prefix}.zip"
    output.parent.mkdir(exist_ok=True)
    files = [(root / "pyproject.toml", "pyproject.toml"), (root / "README.md", "README.md")]
    files += [(root / "SERVEUR.md", "SERVEUR.md"), (root / "scripts/setup_server.py", "scripts/setup_server.py")]
    files += [(path, path.relative_to(root).as_posix()) for path in sorted((root / "src/jhr_chiffrage").glob("*.py"))]
    files += [(path, path.relative_to(root).as_posix()) for path in sorted((root / "src/jhr_chiffrage/assets").iterdir()) if path.suffix in {".svg", ".png", ".ico"}]
    # Local notes may contain personal paths: never package them.
    files += [(root / "packaging/linux" / name, name) for name in ("installer.sh", "install_launcher.py", "LIRE-MOI-UBUNTU.md")]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            info = zipfile.ZipInfo(f"{prefix}/{relative}")
            info.create_system = 3
            info.external_attr = (0o100755 if relative == "installer.sh" else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            data = path.read_bytes() if path.suffix in {".png", ".ico"} else path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
            archive.writestr(info, data)
    print(output)
    print(f"{len(files)} fichiers ; SHA256 {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
