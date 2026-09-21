"""Prepare private HTTPS connection files without opening the application database."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def setup_server(host: str, port: int, directory: Path, database: Path) -> dict[str, str]:
    """Create a self-signed trust certificate and configuration, never replacing files."""
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Indiquez l’adresse IP réelle du serveur, pas une adresse générique.")
    if not 1 <= port <= 65535:
        raise ValueError("Le port doit être compris entre 1 et 65535.")
    if not database.is_absolute():
        raise ValueError("Le chemin de la base doit être absolu.")
    directory = directory.expanduser().resolve()
    paths = {name: directory / name for name in (
        "server.pem", "server.key", "server.json", "connexion-client.txt"
    )}
    if any(path.exists() or path.is_symlink() for path in paths.values()):
        raise FileExistsError("Configuration déjà présente : aucun fichier n’a été remplacé.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JHR Chiffrage local server")])
    addresses = {address, ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    names = [x509.IPAddress(ip) for ip in sorted(addresses, key=str)]
    names += [x509.DNSName(name) for name in sorted({"localhost", socket.gethostname()})]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=730))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(private_key.public_key()), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    token = secrets.token_urlsafe(32)
    url_host = f"[{address}]" if address.version == 6 else str(address)
    url = f"https://{url_host}:{port}"
    config = {
        "host": str(address), "port": port, "database": str(database), "token": token,
        "certfile": str(paths["server.pem"]), "keyfile": str(paths["server.key"]),
    }
    client_instructions = (
        "Connexion privée JHR Chiffrage\n\n"
        f"Adresse du serveur : {url}\nClé d’accès : {token}\n"
        "Certificat à sélectionner dans l’application : server.pem\n\n"
        "Copiez uniquement server.pem et connexion-client.txt sur votre autre PC, "
        "par un moyen privé. Ce document contient la clé d’accès à vos affaires.\n"
        "Ne partagez jamais server.key ni server.json : ils restent sur le serveur.\n"
        "Ne publiez aucun de ces fichiers sur GitHub.\n"
        "Le serveur doit rester allumé et accessible. Si son adresse IP change, "
        "il faudra renouveler le certificat et la configuration.\n"
    )
    contents = {
        "server.key": private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "server.pem": certificate.public_bytes(serialization.Encoding.PEM),
        "server.json": (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "connexion-client.txt": client_instructions.encode("utf-8"),
    }
    created = []
    try:
        for name, content in contents.items():
            # Exclusive creation also protects against a race after the preflight check.
            descriptor = os.open(paths[name], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(paths[name])
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex(":").upper()
    return {**{name: str(path) for name, path in paths.items()}, "fingerprint": fingerprint}


def main() -> None:
    parser = argparse.ArgumentParser(description="Préparer la connexion HTTPS privée de JHR Chiffrage.")
    parser.add_argument("--host", required=True, help="Adresse IP locale réelle du serveur")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, required=True, help="Dossier privé hors du dépôt Git")
    parser.add_argument("--database", type=Path, required=True, help="Chemin absolu de la base existante")
    args = parser.parse_args()
    try:
        result = setup_server(args.host, args.port, args.directory, args.database)
    except (ValueError, FileExistsError) as error:
        parser.exit(2, f"{error}\n")
    for name, value in result.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
