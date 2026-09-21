"""Exercise actual TLS and two clients using only a temporary database."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from setup_server import setup_server
from jhr_chiffrage.connection import RemoteStore
from jhr_chiffrage.core import DomainError


def main():
    with tempfile.TemporaryDirectory(prefix="jhr-https-test-") as folder:
        root = Path(folder)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        paths = setup_server("127.0.0.1", port, root / "private", root / "test.sqlite3")
        config = json.loads(Path(paths["server.json"]).read_text(encoding="utf-8"))
        connection = dict(mode="server", url=f"https://127.0.0.1:{port}",
                          token=config["token"], ca_file=config["certfile"])
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "jhr_chiffrage.server", "--config", paths["server.json"]],
                                       stdout=log, stderr=log)
            first, second = RemoteStore(connection), RemoteStore(connection)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Temporary HTTPS server exited")
                    try:
                        first.health()
                        break
                    except DomainError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("Temporary HTTPS server did not start")
                for changes, expected in ((dict(token="wrong"), "UNAUTHORIZED"),
                                          (dict(ca_file=""), "CONNECTION_ERROR")):
                    bad = RemoteStore({**connection, **changes})
                    try:
                        bad.health()
                    except DomainError as error:
                        assert error.code == expected
                    else:
                        raise AssertionError("Unauthenticated or untrusted connection accepted")
                    finally:
                        bad.close()
                original = first.create_estimate("Temporary HTTPS test", operation_id="create-once")
                assert first.create_estimate("Temporary HTTPS test", operation_id="create-once")["id"] == original["id"]
                other = second.get_estimate(original["id"])
                original["name"] = "Updated by first client"
                first.save_estimate(original, original["revision"])
                try:
                    second.save_estimate(other, other["revision"])
                except DomainError as error:
                    assert error.code == "REVISION_CONFLICT"
                else:
                    raise AssertionError("Concurrent overwrite accepted")
                assert second.get_estimate(original["id"])["name"] == "Updated by first client"
                assert "serveur" in first.backup()
                print("PASS: real HTTPS, certificate verification, authorization, two clients, revisions, idempotency, backup")
            finally:
                first.close()
                second.close()
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
