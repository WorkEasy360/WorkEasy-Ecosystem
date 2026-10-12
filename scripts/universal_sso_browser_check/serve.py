"""Start one backend of the universal-sign-in browser check, in the
foreground (run each in its own terminal or as a background job):

    USSO_WORK=<dir> python serve.py w360        # Workeasy360 Django   127.0.0.1:8021
    USSO_WORK=<dir> python serve.py crm         # Easy CRM Django      127.0.0.1:8022
    USSO_WORK=<dir> python serve.py easybook    # EasyBook Django      127.0.0.1:8023
    USSO_WORK=<dir> python serve.py future      # Future Product RP    127.0.0.3:5024 (after registration)
    USSO_WORK=<dir> python serve.py project     # Easy Project jar     127.0.0.4:8024 (SPA + API)
    USSO_WORK=<dir> python serve.py helpdesk    # Help Desk NestJS API 127.0.0.5:4025
    USSO_WORK=<dir> python serve.py helpdesk-worker  # Help Desk BullMQ worker
    USSO_WORK=<dir> python serve.py w360-fe     # Workeasy360 Next     127.0.0.1:3021
    USSO_WORK=<dir> python serve.py crm-fe      # Easy CRM Next        localhost:4022
    USSO_WORK=<dir> python serve.py easybook-fe # EasyBook Next        127.0.0.2:3023
    USSO_WORK=<dir> python serve.py helpdesk-fe # Help Desk Next       127.0.0.5:3025

The frontends are production builds (``next build``) in copies under
USSO_WORK (README.md), started with ``next start`` and the environment each
one's production preflight requires.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare import (
    CRM,
    EASYBOOK,
    HELPDESK_REPO,
    PY,
    ROOT,
    WORK,
    backend_env,
    bff_proxy_secret,
    crm_env,
    easybook_env,
    helpdesk_env,
    project_env,
)

#: The JDK the Easy Project jar runs on (JAVA_HOME, else ``java`` from PATH).
JAVA = (
    str(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe")
    if os.environ.get("JAVA_HOME")
    else "java"
)

FRONTENDS = {
    # name: (build copy, host, port, environment)
    "w360-fe": ("w360-fe", "127.0.0.1", 3021, {}),
    "crm-fe": (
        "crm-fe",
        "localhost",
        4022,
        {"API_INTERNAL_ORIGIN": "http://127.0.0.1:8022"},
    ),
    "helpdesk-fe": (
        "helpdesk-fe",
        "127.0.0.5",
        3025,
        {"API_INTERNAL_URL": "http://127.0.0.5:4025/api/v1", "NODE_ENV": "production"},
    ),
    "easybook-fe": (
        "easybook-fe",
        "127.0.0.2",
        3023,
        {
            "API_BASE_URL": "http://127.0.0.1:8023/api/v1",
            # Loopback is a secure context to the browser, so Secure cookies work
            # over plain http on 127.0.0.2 - the preflight's rule is kept.
            "SESSION_COOKIE_SECURE": "1",
            "TRUSTED_PROXY_COUNT": "0",
        },
    ),
}


def main(which: str) -> int:
    seed = json.loads((WORK / "seed.json").read_text(encoding="utf-8"))
    base = {"PYTHONUNBUFFERED": "1"}
    if which == "w360":
        env = {**backend_env(), **base}
        cmd = [
            PY,
            str(ROOT / "backend" / "manage.py"),
            "runserver",
            "127.0.0.1:8021",
            "--noreload",
        ]
        cwd = ROOT
    elif which == "crm":
        # A run-only settings module: the development settings with a short
        # re-authentication window, so "Confirm it's you" can be exercised
        # without waiting ten minutes. Nothing in Easy CRM changes.
        (WORK / "usso_crm_settings.py").write_text(
            "from config.settings.dev import *  # noqa: F401,F403\nACCOUNT_REAUTHENTICATION_TIMEOUT = 20\n",
            encoding="utf-8",
        )
        env = {
            **crm_env(seed),
            **base,
            "DJANGO_SETTINGS_MODULE": "usso_crm_settings",
            "PYTHONPATH": os.pathsep.join([str(CRM), str(WORK)]),
        }
        cmd = [
            str(CRM / ".venv" / "Scripts" / "python.exe"),
            "manage.py",
            "runserver",
            "127.0.0.1:8022",
            "--noreload",
        ]
        cwd = CRM
    elif which == "easybook":
        env = {**easybook_env(seed), **base}
        cmd = [
            str(EASYBOOK / ".venv" / "Scripts" / "python.exe"),
            "manage.py",
            "runserver",
            "127.0.0.1:8023",
            "--noreload",
        ]
        cwd = EASYBOOK
    elif which == "project":
        # The jar built from the working tree (``mvnw package``), copied to
        # USSO_WORK so a rebuild in the repository cannot swap it mid-run.
        env = {**project_env(seed), **base}
        cmd = [JAVA, "-jar", str(WORK / "project-module.jar")]
        cwd = WORK
    elif which in ("helpdesk", "helpdesk-worker"):
        env = {**helpdesk_env(seed), **base}
        part = "api" if which == "helpdesk" else "worker"
        cmd = ["node", str(HELPDESK_REPO / "backend" / part / "dist" / "main.js")]
        cwd = HELPDESK_REPO / "backend" / part
    elif which in FRONTENDS:
        folder, host, port, extra = FRONTENDS[which]
        cwd = WORK / folder
        env = {**os.environ, **base, **extra}
        if which == "easybook-fe":
            env["BFF_PROXY_SECRET"] = bff_proxy_secret()
        next_bin = cwd / "node_modules" / "next" / "dist" / "bin" / "next"
        cmd = ["node", str(next_bin), "start", "-H", host, "-p", str(port)]
    elif which == "future":
        future = json.loads((WORK / "future.json").read_text(encoding="utf-8"))
        env = {**os.environ, **base}
        cmd = [
            PY,
            str(Path(__file__).with_name("future_product.py")),
            "--issuer",
            "http://127.0.0.1:8021",
            "--client-id",
            future["client_id"],
            "--key-id",
            future["key_id"],
            "--key",
            str(WORK / "future.key"),
            "--origin",
            "http://127.0.0.3:5024",
        ]
        cwd = ROOT
    else:
        raise SystemExit(f"unknown server {which!r}")
    return subprocess.call(cmd, env=env, cwd=cwd)  # noqa: S603 - local interpreters only


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
