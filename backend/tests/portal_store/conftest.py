"""Fixtures for the Store in every portal: the Store Console's catalog (CRM
and Books on the Store, an affiliate, a 10% programme rule) and a customer
organization with a GBP billing account."""

from __future__ import annotations

from tests.store_console import conftest as _console

console = _console.console
console_org = _console.console_org
fake_payments = _console.fake_payments
login = _console.login
shop = _console.shop
staff_ctx = _console.staff_ctx
