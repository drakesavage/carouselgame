"""Passcode gate for other devices reaching Crawler Console through Barcode Battles (:8787/dcc)."""
from fastapi.testclient import TestClient

from app.access import MAX_FAILURES
from app.main import create_app

PHONE = {"X-Forwarded-For": "192.168.1.50", "X-Forwarded-Prefix": "/dcc", "X-Forwarded-Proto": "http"}


def make(settings, conn, demo_id, local_hosts=frozenset({"testclient"})):
    app = create_app(settings, bootstrap=False, local_hosts=set(local_hosts))
    return TestClient(app, follow_redirects=False), app.state.gate


def test_this_pc_needs_no_passcode(settings, conn, demo_id):
    c, _ = make(settings, conn, demo_id)
    assert c.get("/api/campaigns").status_code == 200
    # Barcode forwarding a request that came from this PC itself (loopback) is local too
    assert c.get("/api/campaigns", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 200
    assert c.get("/api/campaigns", headers={"X-Forwarded-For": "::1"}).status_code == 200


def test_other_devices_must_sign_in(settings, conn, demo_id):
    c, gate = make(settings, conn, demo_id)
    assert c.get("/api/campaigns", headers=PHONE).status_code == 401
    assert c.get(f"/api/campaigns/{demo_id}/messages", headers=PHONE).status_code == 401
    assert c.get("/media/portraits/x.png", headers=PHONE).status_code == 401
    r = c.get("/", headers=PHONE)
    assert r.status_code == 303 and r.headers["location"] == "/dcc/login"
    page = c.get("/login", headers=PHONE)
    assert page.status_code == 200 and 'action="/dcc/login"' in page.text and gate.passcode() not in page.text

    bad = c.post("/login", data={"passcode": "nope-nope"}, headers=PHONE)
    assert bad.status_code == 401 and "passcode didn" in bad.text
    good = c.post("/login", data={"passcode": gate.passcode().upper()}, headers=PHONE)
    assert good.status_code == 303 and good.headers["location"] == "/dcc/"
    cookie = good.cookies.get("crawler_console_access")
    assert cookie and "HttpOnly" in good.headers["set-cookie"] and "Path=/dcc/" in good.headers["set-cookie"]
    c.cookies.set("crawler_console_access", cookie)
    assert c.get("/api/campaigns", headers=PHONE).status_code == 200


def test_passcode_is_only_shown_on_this_pc_and_regenerating_signs_devices_out(settings, conn, demo_id):
    c, gate = make(settings, conn, demo_id)
    info = c.get("/api/access").json()
    assert info["passcode"] == gate.passcode() and info["path"] == "/dcc/"
    old_cookie = gate.cookie_value()
    c.cookies.set("crawler_console_access", old_cookie)
    assert c.get("/api/access", headers=PHONE).status_code == 403          # even signed in, a phone can't read it
    assert c.post("/api/access/regenerate", headers=PHONE).status_code == 403
    new = c.post("/api/access/regenerate").json()["passcode"]
    assert new != info["passcode"]
    assert c.get("/api/campaigns", headers=PHONE).status_code == 401      # old cookie no longer valid


def test_lockout_after_repeated_wrong_guesses(settings, conn, demo_id):
    c, gate = make(settings, conn, demo_id)
    for _ in range(MAX_FAILURES):
        c.post("/login", data={"passcode": "wrong"}, headers=PHONE)
    r = c.post("/login", data={"passcode": gate.passcode()}, headers=PHONE)
    assert r.status_code == 401 and "Too many attempts" in r.text
    # a different device is not locked out
    other = {**PHONE, "X-Forwarded-For": "192.168.1.77"}
    assert c.post("/login", data={"passcode": gate.passcode()}, headers=other).status_code == 303


def test_direct_non_loopback_client_cannot_spoof_forwarded_for(settings, conn, demo_id):
    # If Crawler Console were ever bound to the network, a remote peer claiming X-Forwarded-For: 127.0.0.1 is not trusted.
    c, _ = make(settings, conn, demo_id, local_hosts=frozenset())
    assert c.get("/api/campaigns", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401


def test_prefix_header_is_sanitised(settings, conn, demo_id):
    c, _ = make(settings, conn, demo_id)
    r = c.get("/", headers={**PHONE, "X-Forwarded-Prefix": "https://evil.example"})
    assert r.headers["location"] == "/login"
