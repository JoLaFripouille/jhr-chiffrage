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
from jhr_chiffrage.offline import OfflineStore


def stop_server(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def wait_server(process, client):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Temporary HTTPS server exited")
        try:
            client.health()
            return
        except DomainError:
            time.sleep(.1)
    raise RuntimeError("Temporary HTTPS server did not start")


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
            offline = None
            try:
                wait_server(process, first)
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
                # Every offline path is explicitly inside the disposable test root.
                cache = root / "offline-cache"
                offline = OfflineStore(connection, cache_dir=cache)
                assert offline.get_estimate(original["id"])["name"] == "Updated by first client"
                offline.close()
                offline = None
                stop_server(process)
                offline = OfflineStore(connection, cache_dir=cache)
                assert not offline.synchronize()
                trip = offline.create_estimate("Created without network")
                trip["name"] = "Edited without network"
                trip = offline.save_estimate(trip, trip["revision"])
                assert offline.pending_count == 1
                # Persisted edits must survive closing and reopening while offline.
                offline.close()
                offline = OfflineStore(connection, cache_dir=cache)
                assert offline.get_estimate(trip["id"]) == trip
                process = subprocess.Popen([sys.executable, "-m", "jhr_chiffrage.server", "--config", paths["server.json"]],
                                           stdout=log, stderr=log)
                wait_server(process, first)
                assert offline.synchronize()
                assert offline.pending_count == 0
                assert second.get_estimate(trip["id"]) == trip
                # Concurrent changes stay intact, then explicit resolution publishes
                # a separate draft containing the offline work.
                local = offline.get_estimate(trip["id"])
                local["name"] = "Train conflict version"
                offline.save_estimate(local, local["revision"])
                remote = second.get_estimate(trip["id"])
                remote["name"] = "Office conflict version"
                second.save_estimate(remote, remote["revision"])
                try:
                    offline.synchronize()
                except DomainError as error:
                    assert error.code == "SYNC_CONFLICT"
                else:
                    raise AssertionError("Offline concurrent overwrite accepted")
                assert offline.has_conflict and offline.pending_count == 1
                assert offline.get_estimate(trip["id"])["name"] == "Train conflict version"
                assert second.get_estimate(trip["id"])["name"] == "Office conflict version"
                offline.resolve_conflict_keep_both()
                assert offline.synchronize()
                names = {x["name"] for x in second.list_estimates()}
                assert {"Office conflict version", "Train conflict version (copie hors ligne)"} <= names
                assert offline.pending_count == 0
                assert list(cache.glob("backups/*.sqlite3"))
                print("PASS: real HTTPS, certificate verification, authorization, two clients, revisions, idempotency, backup, offline restart/edit/sync, conflict copies")
            finally:
                if offline is not None:
                    offline.close()
                first.close()
                second.close()
                stop_server(process)


if __name__ == "__main__":
    main()
