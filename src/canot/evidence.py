"""Tamper-evident log: every entry holds the hash of the previous one; the last hash is signed
with an Ed25519 key created in the customer's environment."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class EvidenceLog:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.path = out / "evidence.jsonl"
        self.entries: list[dict] = []
        self.prev = "0" * 64

    def add(self, event: str, data: dict) -> None:
        data = json.loads(json.dumps(data, sort_keys=True, default=str))  # normalize (int keys -> str) before hashing
        e = {"seq": len(self.entries), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "event": event, "data": data, "prev": self.prev}
        e["hash"] = _h(json.dumps({k: e[k] for k in ("seq", "ts", "event", "data", "prev")}, sort_keys=True, default=str).encode())
        self.prev = e["hash"]
        self.entries.append(e)

    def seal(self, key_path: Path, announce=None) -> dict:
        key = load_or_create_key(key_path, announce)
        sig = key.sign(self.prev.encode())
        pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.path.write_text("\n".join(json.dumps(e, sort_keys=True, default=str) for e in self.entries) + "\n")
        (self.out / "evidence.sig").write_text(sig.hex())
        (self.out / "evidence.pub").write_bytes(pub)
        return {"last_hash": self.prev, "signature": sig.hex(), "entries": len(self.entries)}


def load_or_create_key(path: Path, announce=None) -> Ed25519PrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]
    if announce:
        announce(f"Signing key created: {path}. Keep it: without it, two evidence folders are not signed by the same key (see CANOT_EVIDENCE_KEY_PATH).")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(path, 0o600)
    return key


def verify_dir(d: Path, expect_pub: Path | None = None) -> tuple[bool, str]:
    try:
        if expect_pub is not None and (d / "evidence.pub").read_bytes().strip() != expect_pub.read_bytes().strip():
            return False, "the folder's public key is not the expected one"
        lines = [json.loads(l) for l in (d / "evidence.jsonl").read_text().splitlines() if l.strip()]
        prev = "0" * 64
        for e in lines:
            body = {k: e[k] for k in ("seq", "ts", "event", "data", "prev")}
            if e["prev"] != prev or _h(json.dumps(body, sort_keys=True, default=str).encode()) != e["hash"]:
                return False, f"chain broken at entry {e['seq']}"
            prev = e["hash"]
        pub = serialization.load_pem_public_key((d / "evidence.pub").read_bytes())
        assert isinstance(pub, Ed25519PublicKey)
        pub.verify(bytes.fromhex((d / "evidence.sig").read_text().strip()), prev.encode())
        return True, f"{len(lines)} entries, chain intact, signature valid"
    except InvalidSignature:
        return False, "invalid signature"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
