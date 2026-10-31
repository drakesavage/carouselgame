"""Passcode gate for access from other devices (e.g. phones through Barcode Battles on :8787/dcc).

Crawler Console itself listens only on 127.0.0.1, so every request comes from this PC: either a browser here,
or Barcode Battles forwarding a request it received from another device. The forwarder sets X-Forwarded-For
to the real client address (overwriting anything the client sent), and that header is trusted only because
nothing outside this PC can reach the listener.

* This PC (loopback client): no passcode, as before.
* Any other device: needs the passcode once; a signed cookie remembers it. Changing the passcode invalidates
  every cookie. Wrong guesses lock that device out for a while.
* The passcode is readable only from this PC (Saves & Settings).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import os
import secrets
import socket
import time
from pathlib import Path

COOKIE = "crawler_console_access"
COOKIE_DAYS = 180
MAX_FAILURES = 8
LOCKOUT_SECONDS = 600
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o/1/l/i: easy to type on a phone


def _read_or_create(path: Path, make) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(make(), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def new_passcode() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class AccessGate:
    def __init__(self, data_dir: Path, local_hosts: set[str] | None = None):
        self.data_dir = data_dir
        self.local_hosts = local_hosts or set()
        self.failures: dict[str, tuple[int, float]] = {}

    # -- secrets
    def passcode(self) -> str:
        if os.environ.get("CAROUSEL_PASSCODE"):
            return os.environ["CAROUSEL_PASSCODE"].strip()
        return _read_or_create(self.data_dir / "access_passcode.txt", new_passcode)

    def regenerate(self) -> str:
        if os.environ.get("CAROUSEL_PASSCODE"):
            raise ValueError("the passcode is fixed by CAROUSEL_PASSCODE in .env; change it there")
        (self.data_dir / "access_passcode.txt").write_text(new_passcode(), encoding="utf-8")
        return self.passcode()

    def _secret(self) -> bytes:
        return _read_or_create(self.data_dir / "session_secret.txt", lambda: secrets.token_hex(32)).encode()

    def cookie_value(self) -> str:
        return hmac.new(self._secret(), self.passcode().encode(), hashlib.sha256).hexdigest()

    # -- request classification
    def client_address(self, peer: str | None, forwarded_for: str | None) -> str:
        peer = peer or ""
        if self._is_loopback(peer) and forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return peer

    def _is_loopback(self, host: str) -> bool:
        if host in self.local_hosts:
            return True
        try:
            return ipaddress.ip_address(host.split("%")[0]).is_loopback
        except ValueError:
            return host == "localhost"

    def is_local(self, peer: str | None, forwarded_for: str | None) -> bool:
        return self._is_loopback(self.client_address(peer, forwarded_for))

    def has_valid_cookie(self, cookie: str | None) -> bool:
        return bool(cookie) and hmac.compare_digest(cookie, self.cookie_value())

    # -- login with lockout
    def locked_for(self, client: str) -> int:
        count, until = self.failures.get(client, (0, 0.0))
        return max(0, int(until - time.time())) if count >= MAX_FAILURES else 0

    def check(self, client: str, attempt: str) -> bool:
        if self.locked_for(client):
            return False
        if hmac.compare_digest(attempt.strip().lower(), self.passcode().lower()):
            self.failures.pop(client, None)
            return True
        count, _ = self.failures.get(client, (0, 0.0))
        count += 1
        self.failures[client] = (count, time.time() + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0.0)
        return False


def lan_addresses() -> list[str]:
    """Best-effort IPv4 addresses other devices can use to reach this PC."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # no packet is sent; this only picks the outbound interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def login_page(prefix: str, error: str | None = None) -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Crawler Console — passcode</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#06080c;color:#dbe5ec;font:16px/1.5 "Segoe UI",system-ui,sans-serif}}
form{{width:min(360px,calc(100vw - 32px));border:1px solid #2b3c4d;border-radius:8px;padding:24px;background:#0d1219;box-shadow:0 0 40px rgba(62,240,200,.06)}}
h1{{margin:0 0 4px;font-size:18px;letter-spacing:.14em;text-transform:uppercase;color:#3ef0c8}}
p{{margin:0 0 16px;color:#aab8c4;font-size:14px}}.err{{color:#ff5257}}
input{{width:100%;box-sizing:border-box;font:20px/1.4 Consolas,monospace;letter-spacing:.12em;padding:10px 12px;border-radius:6px;border:1px solid #2b3c4d;background:#0a0e14;color:#fff}}
button{{margin-top:14px;width:100%;padding:11px;border:0;border-radius:6px;background:#2fd9b3;color:#04110d;font-weight:700;letter-spacing:.1em;text-transform:uppercase;font-size:14px}}
</style></head><body><form method="post" action="{html.escape(prefix)}/login">
<h1>Crawler Console</h1><p>Enter the passcode shown in Saves &amp; Settings on the game PC.</p>{err}
<input name="passcode" autocomplete="current-password" autocapitalize="none" autofocus placeholder="xxxx-xxxx">
<button type="submit">Enter the dungeon</button></form></body></html>"""
