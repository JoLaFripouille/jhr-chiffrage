"""Initialize diagnostics before loading the native GUI."""
from .diagnostics import install, install_qt, report_exception
import sys


def main():
    install()
    try:
        install_qt()
        from .ui import main as run
        return run()
    except Exception:
        report_exception(*sys.exc_info())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
