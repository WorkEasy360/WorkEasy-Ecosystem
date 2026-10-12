"""A stand-in for a product built after universal sign-in shipped (ADR-044):
"Future Product", a separate application with its own host, its own cookie
and its own OpenID Connect client - nothing from this repository imported.

It does what the product integration guide asks of every product:
authorization code + PKCE (S256), state and nonce, ``private_key_jwt`` client
authentication, and offline verification of the ID token against the
provider's key set (key by ``kid``, algorithm from the key set, issuer,
audience, expiry, ``typ``, nonce). Server-side sessions keyed by an
HttpOnly cookie. Its own sign-in page offers "Access Future Product" and a
placeholder for its own accounts ("use another sign-in method").

    python future_product.py --issuer http://127.0.0.1:8021 --client-id <id> \\
        --key-id <credential id> --key <future.key> --origin http://127.0.0.3:5024

Browser-check use only; not part of the platform.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import secrets
import time
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

import jwt

FLOWS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
CONFIG: dict = {}


def _http_json(url: str, form: dict | None = None) -> tuple[int, dict]:
    data = urlencode(form).encode() if form is not None else None
    request = Request(url, data=data, headers={"Accept": "application/json"})  # noqa: S310 - configured issuer only
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except Exception as exc:
        body = getattr(exc, "read", lambda: b"{}")()
        try:
            return getattr(exc, "code", 503), json.loads(body or b"{}")
        except ValueError:
            return getattr(exc, "code", 503), {}


def discovery() -> dict:
    if "discovery" not in CONFIG:
        status, body = _http_json(f"{CONFIG['issuer']}/.well-known/openid-configuration")
        if status != 200 or body.get("issuer") != CONFIG["issuer"]:
            raise RuntimeError("discovery failed or names another issuer")
        CONFIG["discovery"] = body
    return CONFIG["discovery"]


def verify(id_token: str, nonce: str) -> dict:
    kid = jwt.get_unverified_header(id_token).get("kid")
    _status, jwks = _http_json(discovery()["jwks_uri"])
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None or key.get("alg") not in ("RS256", "ES256"):
        raise ValueError("unknown key")
    claims = jwt.decode(
        id_token,
        jwt.PyJWK(key).key,
        algorithms=[key["alg"]],
        audience=CONFIG["client_id"],
        issuer=CONFIG["issuer"],
        leeway=30,
        options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "typ"]},
    )
    if claims.get("typ") != "id_token" or not hmac.compare_digest(
        str(claims.get("nonce", "")), nonce
    ):
        raise ValueError("not our ID token")
    return claims


def assertion() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": CONFIG["client_id"],
            "sub": CONFIG["client_id"],
            "aud": discovery()["token_endpoint"],
            "iat": now,
            "exp": now + 60,
            "jti": uuid.uuid4().hex,
        },
        CONFIG["private_key"],
        algorithm="RS256",
        headers={"kid": CONFIG["key_id"]},
    )


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;color:#111}}
a.button,button{{display:inline-block;padding:.6rem 1rem;border-radius:.4rem;border:1px solid #333;background:#1d4ed8;color:#fff;text-decoration:none;font:inherit}}
.secondary{{background:#fff;color:#111}} section{{border:1px solid #ccc;border-radius:.5rem;padding:1rem;margin:1rem 0}}</style></head>
<body><main>{body}</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    disable_nagle_algorithm = True

    def log_message(self, *args):  # quiet
        return

    def _cookie(self, name: str) -> str:
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return jar[name].value if name in jar else ""

    def _send(self, status: int, body: str, headers: dict | None = None) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        session = SESSIONS.get(self._cookie("fp_session"))
        if parts.path == "/":
            if query.get("organization"):
                return self._redirect(
                    "/start?" + urlencode({"organization": query["organization"]})
                )
            if session is None:
                return self._redirect("/login")
            claims = session["claims"]
            body = (
                f"<h1>Future Product</h1><p id='who'>Signed in as <strong>{html.escape(claims.get('email', ''))}</strong></p>"
                f"<p id='org'>Organization: {html.escape(claims.get('organization_name', ''))} "
                f"(<code>{html.escape(claims.get('organization_id', ''))}</code>)</p>"
                f"<p id='product'>Product code: <code>{html.escape(claims.get('product_code', ''))}</code></p>"
                f"<p><a class='button secondary' href='/logout'>Sign out of Future Product</a></p>"
            )
            return self._send(200, PAGE.format(title="Future Product", body=body))
        if parts.path == "/login":
            error = html.escape(query.get("error", ""))
            body = (
                "<h1>Future Product</h1>"
                + (
                    f"<p role='alert'>Workeasy360 sign-in did not complete ({error}).</p>"
                    if error
                    else ""
                )
                + "<section aria-labelledby='w360'><h2 id='w360'>Already a Workeasy360 user?</h2>"
                "<p>Access Future Product using your Workeasy360 account.</p>"
                "<p><a class='button' href='/start'>Access Future Product</a></p></section>"
                "<section aria-labelledby='own'><h2 id='own'>Use another sign-in method</h2>"
                "<p>Future Product accounts are not set up in this rehearsal.</p></section>"
            )
            return self._send(
                200, PAGE.format(title="Sign in - Future Product", body=body)
            )
        if parts.path == "/start":
            verifier = secrets.token_urlsafe(48)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            state, nonce, flow_id = (
                secrets.token_urlsafe(24),
                secrets.token_urlsafe(24),
                secrets.token_urlsafe(24),
            )
            FLOWS[flow_id] = {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "at": time.time(),
            }
            params = {
                "response_type": "code",
                "client_id": CONFIG["client_id"],
                "redirect_uri": f"{CONFIG['origin']}/callback",
                "scope": "openid email profile organization",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            if query.get("organization"):
                params["organization"] = query["organization"]
            return self._redirect(
                f"{discovery()['authorization_endpoint']}?{urlencode(params)}",
                {
                    "Set-Cookie": f"fp_flow={flow_id}; HttpOnly; SameSite=Lax; Path=/; Max-Age=600"
                },
            )
        if parts.path == "/callback":
            flow = FLOWS.pop(self._cookie("fp_flow"), None)
            if query.get("error"):
                return self._redirect("/login?" + urlencode({"error": query["error"]}))
            if flow is None or not hmac.compare_digest(
                query.get("state", ""), flow["state"]
            ):
                return self._redirect("/login?error=state_mismatch")
            status, tokens = _http_json(
                discovery()["token_endpoint"],
                {
                    "grant_type": "authorization_code",
                    "code": query.get("code", ""),
                    "redirect_uri": f"{CONFIG['origin']}/callback",
                    "code_verifier": flow["verifier"],
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": assertion(),
                },
            )
            if status != 200:
                return self._redirect(
                    "/login?" + urlencode({"error": tokens.get("error", "token_failed")})
                )
            try:
                claims = verify(tokens["id_token"], flow["nonce"])
            except Exception:
                return self._redirect("/login?error=invalid_token")
            previous = self._cookie("fp_session")
            SESSIONS.pop(previous, None)  # a new person replaces the old session entirely
            session_id = secrets.token_urlsafe(32)
            SESSIONS[session_id] = {"claims": claims}
            return self._redirect(
                "/",
                {
                    "Set-Cookie": f"fp_session={session_id}; HttpOnly; SameSite=Lax; Path=/"
                },
            )
        if parts.path == "/logout":
            SESSIONS.pop(self._cookie("fp_session"), None)
            return self._redirect(
                "/login",
                {"Set-Cookie": "fp_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"},
            )
        return self._send(404, PAGE.format(title="Not found", body="<h1>Not found</h1>"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--origin", default="http://127.0.0.3:5024")
    args = parser.parse_args()
    CONFIG.update(
        issuer=args.issuer.rstrip("/"),
        client_id=args.client_id,
        key_id=args.key_id,
        private_key=open(args.key, encoding="utf-8").read(),
        origin=args.origin,
    )
    host, port = urlsplit(args.origin).hostname, urlsplit(args.origin).port
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
