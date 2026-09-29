"""Local per-session diagnostics, including native failures in windowed builds."""
from __future__ import annotations

import atexit
from datetime import datetime
import faulthandler
import logging
import os
from pathlib import Path
import sys
import threading

from . import __version__

_stream = None
_session = None
_qt_handler = None


def logs_directory():
    return Path.home() / ".jhr-chiffrage" / "logs"


def install(directory=None):
    global _stream, _session
    if _stream is not None:
        return _session
    folder = Path(directory) if directory else logs_directory()
    folder.mkdir(parents=True, exist_ok=True)
    # Per-process files keep faulthandler's descriptor valid throughout a run.
    for old in sorted(folder.glob("desktop-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[19:]:
        try:
            old.unlink()
        except OSError:
            pass
    _session = folder / f"desktop-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
    _stream = _session.open("a", encoding="utf-8", buffering=1)
    handler = logging.StreamHandler(_stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("jhr_chiffrage").setLevel(logging.INFO)
    faulthandler.enable(file=_stream, all_threads=True)
    if sys.stderr is None:
        sys.stderr = _stream
    if sys.stdout is None:
        sys.stdout = _stream
    sys.excepthook = report_exception
    threading.excepthook = lambda args: report_exception(args.exc_type, args.exc_value, args.exc_traceback)
    logging.getLogger(__name__).info("Session start version=%s platform=%s pid=%s", __version__, sys.platform, os.getpid())
    atexit.register(lambda: logging.getLogger(__name__).info("Process exit"))
    return _session


def report_exception(kind, value, tb):
    logging.getLogger(__name__).critical("Unhandled Python exception", exc_info=(kind, value, tb))


def install_qt():
    global _qt_handler
    from PySide6.QtCore import qInstallMessageHandler, qVersion
    def handle(kind, context, message):
        logging.getLogger(__name__).warning("Qt %s: %s", kind.name, message)
    _qt_handler = handle
    qInstallMessageHandler(_qt_handler)
    logging.getLogger(__name__).info("Qt version=%s", qVersion())
