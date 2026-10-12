"""Rehearse the production edge (deploy/edge/Caddyfile) for the five portal
hosts, locally, with TLS verification ON (ADR-043, brief Part 10).

    python scripts/portals_browser_check/edge_rehearsal.py --caddy <caddy.exe> --work <scratch dir>

The real Caddyfile is used with exactly three mechanical changes, all printed:
the upstreams ``api:8000`` and ``web:3000`` point at two local stub servers,
the listeners move to high ports, and the global block gains
``skip_install_trust`` so the rehearsal never touches this machine's trust
store. Certificates come from Caddy's ``internal`` issuer (what staging uses);
every https request below verifies the chain against that CA and the host
name - nothing is ever accepted unverified.

Checks, per host: plain http answers 308 to https on the same host; the
certificate verifies for that host; HSTS (2 years, includeSubDomains,
preload); the request reaches the right upstream with the **original Host
header** (which Django's surface rules and the hand-off depend on); internal
paths are 404; ``api.`` serves no web page; ``auth.`` serves the OIDC
discovery and key set from the API. A partner's custom domain gets only the
branded landing and the two public brand lookups (on-demand TLS is not
exercised: it needs the API's permission answer).

Writes nothing outside ``--work``.
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = "workeasy360.com"
PORTALS = ("admin", "partner", "app", "auth", "api")
HTTP_PORT, HTTPS_PORT = 18080, 18443
API_PORT, WEB_PORT = 18000, 18001


class Stub(http.server.BaseHTTPRequestHandler):
    """Answers with which upstream it is and the Host it was given."""

    name = "?"

    def do_GET(self) -> None:  # noqa: N802 - http.server's API
        body = json.dumps({"upstream": self.name, "host": self.headers.get("Host"), "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def stub(name: str, port: int) -> http.server.ThreadingHTTPServer:
    handler = type(f"{name}Stub", (Stub,), {"name": name})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def rehearsal_caddyfile() -> tuple[str, list[str]]:
    text = (ROOT / "deploy" / "edge" / "Caddyfile").read_text(encoding="utf-8")
    changes = []
    for old, new in (
        ("api:8000", f"127.0.0.1:{API_PORT}"),
        ("web:3000", f"127.0.0.1:{WEB_PORT}"),
    ):
        changes.append(f"{old} -> {new} ({text.count(old)} occurrences)")
        text = text.replace(old, new)
    marker = "{\n\tservers {"
    if marker not in text:
        raise SystemExit("the Caddyfile's global block changed shape")
    text = text.replace(
        marker,
        f"{{\n\thttp_port {HTTP_PORT}\n\thttps_port {HTTPS_PORT}\n\tskip_install_trust\n"
        f"\tdefault_bind 127.0.0.1\n\tadmin localhost:12019\n\tservers {{",
        1,
    )
    changes.append(
        f"global: http_port {HTTP_PORT}, https_port {HTTPS_PORT}, skip_install_trust, "
        "default_bind 127.0.0.1, admin on 12019"
    )
    # The internal permission listener and the metrics listener move too.
    text = text.replace("http://127.0.0.1:8081", "http://127.0.0.1:18081").replace(":9180 {", ":19180 {")
    changes.append("internal listeners: 8081 -> 18081, 9180 -> 19180")
    return text, changes


class Conn(http.client.HTTPSConnection):
    """An https connection to 127.0.0.1 that presents and verifies ``host``
    (SNI and the certificate's name) - what --resolve does for curl."""

    def __init__(self, host: str, port: int, context: ssl.SSLContext):
        super().__init__(host, port, context=context, timeout=10)
        self._real_host = host

    def connect(self) -> None:
        sock = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._real_host)


def get(host: str, path: str, *, context: ssl.SSLContext | None, port: int) -> tuple[int, dict[str, str], bytes]:
    if context is None:
        conn: http.client.HTTPConnection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path, headers={"Host": host})
    else:
        # The Host a browser sends on 443: the bare name, no port - the
        # rehearsal's high port is an artefact of running locally.
        conn = Conn(host, port, context)
        conn.putrequest("GET", path, skip_host=True)
        conn.putheader("Host", host)
        conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    headers = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, headers, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--json")
    args = parser.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    text, changes = rehearsal_caddyfile()
    (work / "Caddyfile").write_text(text, encoding="utf-8")
    print("Rehearsal Caddyfile = deploy/edge/Caddyfile with:")
    for change in changes:
        print("  -", change)

    servers = [stub("api", API_PORT), stub("web", WEB_PORT)]
    env = dict(os.environ)
    env.update({f"W360_{p.upper()}_ORIGIN": f"https://{p}.{DOMAIN}" for p in PORTALS})
    env.update(
        W360_EDGE_UPSTREAM_HOST=f"api.{DOMAIN}",
        W360_TLS_ISSUER="internal",
        XDG_DATA_HOME=str(work / "data"),
        XDG_CONFIG_HOME=str(work / "config"),
    )
    caddy = subprocess.Popen(  # noqa: S603 - the operator's own binary
        [args.caddy, "run", "--config", str(work / "Caddyfile"), "--adapter", "caddyfile"],
        env=env,
        stdout=open(work / "caddy.log", "w"),  # noqa: SIM115
        stderr=subprocess.STDOUT,
    )
    results: list[dict[str, object]] = []
    try:
        root = None
        for _ in range(60):
            found = list((work / "data").rglob("pki/authorities/local/root.crt"))
            if found and caddy.poll() is None:
                root = found[0]
                break
            time.sleep(0.5)
        if root is None:
            print((work / "caddy.log").read_text(encoding="utf-8")[-3000:])
            return 2
        time.sleep(2)
        context = ssl.create_default_context(cafile=str(root))  # verify chain + host name

        def record(name: str, ok: bool, detail: str) -> None:
            results.append({"check": name, "ok": ok, "detail": detail})
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<52} {detail}")

        for portal in PORTALS:
            host = f"{portal}.{DOMAIN}"
            print(f"\n{host}")
            status, headers, _ = get(host, "/some/page?x=1", context=None, port=HTTP_PORT)
            record(
                f"{portal}: http -> https",
                status == 308 and headers.get("location") == f"https://{host}/some/page?x=1",
                f"{status} {headers.get('location')}",
            )
            try:
                status, headers, body = get(host, "/api/v1/auth/session", context=context, port=HTTPS_PORT)
                ok_tls = True
            except ssl.SSLError as exc:
                record(f"{portal}: certificate verifies for {host}", False, str(exc))
                continue
            record(f"{portal}: certificate verifies for {host}", ok_tls, "chain + host name verified")
            hsts = headers.get("strict-transport-security", "")
            record(
                f"{portal}: HSTS",
                "max-age=63072000" in hsts and "includeSubDomains" in hsts and "preload" in hsts,
                hsts,
            )
            seen = json.loads(body or b"{}")
            record(
                f"{portal}: /api reaches Django with Host {host}",
                seen.get("upstream") == "api" and seen.get("host") == host,
                str(seen),
            )
            status, _, body = get(host, "/login", context=context, port=HTTPS_PORT)
            web = json.loads(body or b"{}") if status == 200 else {}
            if portal == "api":
                record("api: no web UI", status == 404, f"{status}")
            else:
                record(
                    f"{portal}: pages reach the frontend with Host {host}",
                    web.get("upstream") == "web" and web.get("host") == host,
                    str(web),
                )
            for internal in ("/api/v1/public/edge/tls-allowed?domain=x", "/healthz/ready"):
                status, _, _ = get(host, internal, context=context, port=HTTPS_PORT)
                record(f"{portal}: {internal.split('?')[0]} hidden", status == 404, str(status))
            status, _, body = get(host, "/.well-known/openid-configuration", context=context, port=HTTPS_PORT)
            seen = json.loads(body or b"{}") if status == 200 else {}
            if portal == "auth":
                record("auth: OIDC discovery served by the API", seen.get("upstream") == "api", str(seen or status))
            elif portal == "api":
                record("api: no discovery document (the issuer is auth.)", status == 404, str(status))
            else:
                record(f"{portal}: discovery is not the API here", seen.get("upstream") != "api", str(seen or status))

        print(f"\napp.partner-brand.com (a partner's own domain, over plain http)")
        for path, expected in (("/login", "web"), ("/api/v1/public/branding", "api"), ("/api/v1/auth/login", None)):
            status, headers, body = get("app.partner-brand.com", path, context=None, port=HTTP_PORT)
            record(
                f"partner domain: http {path} redirects to https",
                status == 308,
                f"{status} {headers.get('location')}",
            )
    finally:
        caddy.terminate()
        try:
            caddy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            caddy.kill()
        for server in servers:
            server.shutdown()

    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} edge checks passed")
    if args.json:
        Path(args.json).write_text(json.dumps({"changes": changes, "results": results}, indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
