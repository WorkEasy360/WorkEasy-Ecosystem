"""Simplified partner management (ADR-048): the Add Partner and staff console
fixtures of ``tests/partner_experience``, re-exported by assignment."""

from __future__ import annotations

from tests.partner_experience import conftest as _pexp

admin = _pexp.admin
manager_only = _pexp.manager_only
staff_client = _pexp.staff_client
channel = _pexp.channel
reseller = _pexp.reseller
white_label = _pexp.white_label
affiliate = _pexp.affiliate
anonymous = _pexp.anonymous
http_login = _pexp.http_login
partner_client = _pexp.partner_client
console = _pexp.console
