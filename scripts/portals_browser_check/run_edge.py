"""Run the production edge (deploy/edge/Caddyfile) in front of the browser
check's scratch backend and frontend, with the five portals on plain http.

    python scripts/portals_browser_check/run_edge.py --caddy <caddy.exe> --work <dir> \
        --listen 8088 --api 127.0.0.1:8011 --web 127.0.0.1:3999

The same mechanical changes as edge_rehearsal.py and nothing else: the two
upstreams, the listener ports, ``skip_install_trust``. Every routing rule is
the production file's. TLS is not part of this run (edge_rehearsal.py checks
it with verification on); the origins are ``http://<portal>.workeasy360.com``,
which Chrome maps to ``--listen`` with ``--host-resolver-rules``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--listen", type=int, default=8088)
    parser.add_argument("--api", default="127.0.0.1:8011")
    parser.add_argument("--web", default="127.0.0.1:3999")
    args = parser.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    text = (ROOT / "deploy" / "edge" / "Caddyfile").read_text(encoding="utf-8")
    text = text.replace("api:8000", args.api).replace("web:3000", args.web)
    text = text.replace(
        "{\n\tservers {",
        f"{{\n\thttp_port {args.listen}\n\thttps_port {args.listen + 355}\n\tskip_install_trust\n"
        "\tdefault_bind 127.0.0.1\n"
        "\tadmin localhost:12020\n\tservers {",
        1,
    )
    text = text.replace("http://127.0.0.1:8081", "http://127.0.0.1:18082").replace(":9180 {", ":19181 {")
    # Plain-http portals carry no certificate, so the portal sites' issuer
    # block goes (Caddy refuses an issuer on a site without TLS next to the
    # on-demand catch-all). The https rehearsal keeps it.
    issuer = "(platform_site) {\n\ttls {\n\t\tissuer {$W360_TLS_ISSUER:acme}\n\t}\n"
    if issuer not in text:
        raise SystemExit("the Caddyfile's platform_site snippet changed shape")
    text = text.replace(issuer, "(platform_site) {\n", 1)
    # ...and the partner custom-domain block (on-demand TLS, not part of this
    # run - edge_rehearsal.py covers it), whose catch-all TLS policy Caddy
    # will not combine with certificate-less sites.
    start, end = text.find("\nhttps:// {\n"), text.find("\nhttp:// {\n")
    if start < 0 or end < start:
        raise SystemExit("the Caddyfile's partner-domain block changed shape")
    text = text[:start] + "\n" + text[end:]
    (work / "Caddyfile").write_text(text, encoding="utf-8")

    env = dict(os.environ)
    env.update({f"W360_{p.upper()}_ORIGIN": f"http://{p}.workeasy360.com" for p in ("admin", "partner", "app", "auth", "api")})
    env.update(
        W360_EDGE_UPSTREAM_HOST="api.workeasy360.com",
        W360_TLS_ISSUER="internal",
        XDG_DATA_HOME=str(work / "data"),
        XDG_CONFIG_HOME=str(work / "config"),
    )
    return subprocess.call(  # noqa: S603 - the operator's own binary
        [args.caddy, "run", "--config", str(work / "Caddyfile"), "--adapter", "caddyfile"], env=env
    )


if __name__ == "__main__":
    raise SystemExit(main())
