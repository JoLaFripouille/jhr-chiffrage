import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID


spec = importlib.util.spec_from_file_location(
    "setup_server", Path(__file__).parents[1] / "scripts" / "setup_server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_setup_generates_trusted_certificate_and_private_configuration(tmp_path):
    database = tmp_path / "untouched.sqlite3"
    result = module.setup_server("192.168.1.20", 8765, tmp_path / "private", database)
    assert not database.exists()
    config = json.loads(Path(result["server.json"]).read_text(encoding="utf-8"))
    assert config["database"] == str(database)
    assert config["host"] == "192.168.1.20"
    assert config["port"] == 8765
    assert len(config["token"]) >= 43
    assert Path(config["certfile"]).is_absolute()
    assert Path(config["keyfile"]).is_absolute()
    certificate = x509.load_pem_x509_certificate(Path(config["certfile"]).read_bytes())
    sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.168.1.20") in sans.get_values_for_type(x509.IPAddress)
    assert ipaddress.ip_address("127.0.0.1") in sans.get_values_for_type(x509.IPAddress)
    assert {"localhost", socket.gethostname()} <= set(sans.get_values_for_type(x509.DNSName))
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert ExtendedKeyUsageOID.SERVER_AUTH in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert (certificate.not_valid_after_utc - certificate.not_valid_before_utc).days == 730
    private_key = serialization.load_pem_private_key(Path(config["keyfile"]).read_bytes(), password=None)
    assert private_key.public_key().public_numbers() == certificate.public_key().public_numbers()
    certificate.verify_directly_issued_by(certificate)
    context = ssl.create_default_context(cafile=config["certfile"])
    assert context.cert_store_stats()["x509_ca"] == 1
    instructions = Path(result["connexion-client.txt"]).read_text(encoding="utf-8")
    assert "https://192.168.1.20:8765" in instructions
    assert config["token"] in instructions
    assert "Ne partagez jamais server.key ni server.json" in instructions
    if os.name != "nt":
        assert Path(config["keyfile"]).stat().st_mode & 0o777 == 0o600


def test_setup_refuses_existing_files_and_generates_unique_tokens(tmp_path):
    database = tmp_path / "db.sqlite3"
    result = module.setup_server("127.0.0.1", 8765, tmp_path / "first", database)
    before = {name: Path(path).read_bytes() for name, path in result.items() if name != "fingerprint"}
    with pytest.raises(FileExistsError):
        module.setup_server("127.0.0.1", 8765, tmp_path / "first", database)
    assert before == {name: Path(result[name]).read_bytes() for name in before}
    second = module.setup_server("127.0.0.1", 8765, tmp_path / "second", database)
    assert json.loads(before["server.json"])["token"] != json.loads(Path(second["server.json"]).read_bytes())["token"]


def test_setup_rejects_relative_database_and_unspecified_address(tmp_path):
    with pytest.raises(ValueError, match="absolu"):
        module.setup_server("127.0.0.1", 8765, tmp_path / "private", Path("db.sqlite3"))
    with pytest.raises(ValueError, match="réelle"):
        module.setup_server("0.0.0.0", 8765, tmp_path / "private", tmp_path / "db.sqlite3")
    assert not (tmp_path / "private").exists()


def test_setup_refuses_orphan_existing_key(tmp_path):
    key = tmp_path / "server.key"
    key.write_bytes(b"existing private key")
    with pytest.raises(FileExistsError):
        module.setup_server("127.0.0.1", 8765, tmp_path, tmp_path / "db.sqlite3")
    assert key.read_bytes() == b"existing private key"
    assert not (tmp_path / "server.json").exists()
