"""The scratch backend for the Phase 10 browser check.

It is the extension check's backend (development settings, the production
frontend's origin trusted, tasks run in-process) with one difference: domain
verification asks a **file-backed DNS resolver** instead of the network. The
check publishes a record by writing it into ``DNS_FILE`` - exactly what the
white-label journey tells a partner to do in their DNS - and the platform's own
verification code reads it back. Nothing else about verification is faked.

    DNS_FILE=dns.json python run_backend.py          # serves 127.0.0.1:8011
"""

import json
import os

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.partners import domains


class FileDns:
    """``txt_values(name)`` from a JSON file of ``{"name": ["value", ...]}``."""

    def __init__(self, path: str) -> None:
        self.path = path

    def txt_values(self, name: str) -> list[str]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                return list(json.load(handle).get(name, []))
        except (FileNotFoundError, ValueError):
            return []


domains.set_txt_resolver(FileDns(os.environ["DNS_FILE"]))
call_command("runserver", os.environ.get("BACKEND_ADDR", "127.0.0.1:8011"), "--noreload")
