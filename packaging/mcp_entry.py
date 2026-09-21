"""PyInstaller MCP entry point; stdout belongs exclusively to MCP."""
import os
import sys

from jhr_chiffrage.mcp_server import main

if __name__ == "__main__":
    try:
        main()
    finally:
        # SDK 1.30.0's UTF-8 transport wrapper can close the original stdout
        # buffer at shutdown. PyInstaller then flushes stdout once more.
        # The protocol has ended; retain a valid sink for that final flush.
        # Wrapper finalizers may run only during interpreter shutdown, so a
        # conditional `closed` check here is too early. Preserve all main()
        # exceptions; only the already-finished protocol output is replaced.
        sys.stdout = sys.__stdout__ = open(os.devnull, "w", encoding="utf-8")
