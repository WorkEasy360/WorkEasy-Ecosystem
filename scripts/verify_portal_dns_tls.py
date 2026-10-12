"""Check the five portal hosts as the internet sees them (ADR-043, brief Part 10).

    python scripts/verify_portal_dns_tls.py [--json report.json] [--domain workeasy360.com]

For each of admin., partner., app., auth. and api.<domain>:

- DNS: the name resolves (A or AAAA), and to what;
- TLS: an https connection with **full certificate verification** (the
  system trust store, hostname checked - never disabled), the certificate's
  issuer and days to expiry (renewal: under 21 days left is a failure, since
  ACME renews at 30);
- HTTP -> HTTPS: plain http answers 301/308 to the same host over https;
- HSTS: the https answer carries Strict-Transport-Security with a max-age of at
  least a year;
- the host answers as its portal: a page or a redirect within the platform,
  not a parking page or another site (the final https URL stays on a
  platform host).

No credential is sent and nothing is written. Exit status 0 only when every
check of every host passes; the report says which failed and why.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import socket
import ssl
import sys
from urllib.parse import urlsplit

PORTALS = ("admin", "partner", "app", "auth", "api")
#: What each host is expected to answer at its root (after redirects within
#: the platform): a path prefix the portal serves.
EXPECT = {
    "admin": ("/admin", "/login", "/auth/"),
    "partner": ("/partner", "/login", "/auth/"),
    "app": ("/",),
    "auth": ("/login",),
    "api": ("/",),
}
TIMEOUT = 10


def resolve(host: str) -> tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return False, f"does not resolve ({exc.strerror or exc})"
    addresses = sorted({info[4][0] for info in infos})
    return True, ", ".join(addresses)


def tls(host: str) -> tuple[bool, str]:
    context = ssl.create_default_context()  # verification and hostname check on
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as conn:
                cert = conn.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return False, f"certificate not valid for {host}: {exc.verify_message}"
    except (OSError, ssl.SSLError) as exc:
        return False, f"no TLS connection: {exc}"
    expires = dt.datetime.fromtimestamp(
        ssl.cert_time_to_seconds(cert["notAfter"]), tz=dt.timezone.utc
    )
    days = (expires - dt.datetime.now(tz=dt.timezone.utc)).days
    issuer = dict(item[0] for item in cert.get("issuer", ())).get("organizationName", "?")
    if days < 21:
        return (
            False,
            f"expires in {days} days (issuer {issuer}): renewal is not happening",
        )
    return True, f"valid, issuer {issuer}, {days} days left"


def request(scheme: str, host: str, path: str = "/") -> tuple[int, dict[str, str]]:
    connection_class = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    kwargs = {"context": ssl.create_default_context()} if scheme == "https" else {}
    conn = connection_class(host, timeout=TIMEOUT, **kwargs)
    try:
        conn.request("GET", path, headers={"User-Agent": "workeasy360-portal-check/1"})
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}
    finally:
        conn.close()


def http_redirect(host: str) -> tuple[bool, str]:
    try:
        status, headers = request("http", host)
    except OSError as exc:
        return False, f"plain http unreachable: {exc}"
    location = headers.get("location", "")
    target = urlsplit(location)
    if status in (301, 308) and target.scheme == "https" and target.hostname == host:
        return True, f"{status} -> {location}"
    return False, f"plain http answered {status} {location or '(no Location)'}"


def https_answer(host: str, domain: str, portal: str) -> tuple[bool, str, bool, str]:
    """(answer ok, detail, hsts ok, hsts detail) - following redirects that
    stay on the platform's own hosts, at most five."""
    platform_hosts = {f"{p}.{domain}" for p in PORTALS}
    current_host, path = host, "/"
    hsts = ""
    for _ in range(6):
        try:
            status, headers = request("https", current_host, path)
        except (OSError, ssl.SSLError) as exc:
            return False, f"https unreachable: {exc}", False, "not checked"
        if current_host == host:
            hsts = headers.get("strict-transport-security", "")
        if status in (301, 302, 303, 307, 308):
            location = urlsplit(headers.get("location", ""))
            next_host = location.hostname or current_host
            if next_host not in platform_hosts:
                return (
                    False,
                    f"redirects off the platform to {next_host}",
                    bool(hsts),
                    hsts,
                )
            current_host, path = (
                next_host,
                (location.path or "/") + (f"?{location.query}" if location.query else ""),
            )
            continue
        final = f"https://{current_host}{path}"
        expected = EXPECT[portal]
        on_portal = current_host in platform_hosts and any(
            path.startswith(p) for p in expected
        )
        answer_ok = status < 500 and on_portal
        break
    else:
        return False, "redirect loop", bool(hsts), hsts
    max_age = 0
    for part in hsts.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "max-age" and value.isdigit():
            max_age = int(value)
    hsts_ok = max_age >= 31_536_000
    return answer_ok, f"{status} at {final}", hsts_ok, hsts or "(none)"


def check(domain: str) -> list[dict[str, object]]:
    rows = []
    for portal in PORTALS:
        host = f"{portal}.{domain}"
        row: dict[str, object] = {"portal": portal, "host": host}
        ok, detail = resolve(host)
        row["dns"] = {"ok": ok, "detail": detail}
        if not ok:
            for name in ("tls", "http_redirect", "answer", "hsts"):
                row[name] = {"ok": False, "detail": "not checked: no DNS"}
            rows.append(row)
            continue
        ok, detail = tls(host)
        row["tls"] = {"ok": ok, "detail": detail}
        ok, detail = http_redirect(host)
        row["http_redirect"] = {"ok": ok, "detail": detail}
        if row["tls"]["ok"]:  # type: ignore[index]
            answer_ok, answer, hsts_ok, hsts = https_answer(host, domain, portal)
        else:
            answer_ok, answer, hsts_ok, hsts = (
                False,
                "not checked: no valid TLS",
                False,
                "not checked",
            )
        row["answer"] = {"ok": answer_ok, "detail": answer}
        row["hsts"] = {"ok": hsts_ok, "detail": hsts}
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--domain", default="workeasy360.com")
    parser.add_argument("--json", help="also write the report here")
    args = parser.parse_args()

    rows = check(args.domain)
    checks = ("dns", "tls", "http_redirect", "answer", "hsts")
    failed = 0
    for row in rows:
        print(f"\n{row['host']}")
        for name in checks:
            result = row[name]
            mark = "PASS" if result["ok"] else "FAIL"  # type: ignore[index]
            failed += 0 if result["ok"] else 1  # type: ignore[index]
            print(f"  {mark}  {name:<14} {result['detail']}")  # type: ignore[index]
    print(f"\n{len(rows) * len(checks) - failed}/{len(rows) * len(checks)} checks passed")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "checked_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                    "domain": args.domain,
                    "hosts": rows,
                },
                handle,
                indent=2,
            )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
