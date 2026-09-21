"""PyInstaller desktop entry point (windowed)."""
from pathlib import Path
import traceback

if __name__ == "__main__":
    try:
        from jhr_chiffrage.ui import main
        main()
    except Exception:
        diagnostic = Path.home() / ".jhr-chiffrage" / "startup-error.log"
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        diagnostic.write_text(traceback.format_exc(), encoding="utf-8")
        raise
