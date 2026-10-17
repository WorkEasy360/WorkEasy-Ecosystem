"""Partner portal final menu (ADR-046): fixtures.

Reuses the business-panel fixtures unchanged (a seller partner, real signed-in
members of every role, the second-factor session) - the final menu adds screens,
three small tables and a key-authenticated read API over that same partner, not
a second kind of partner.
"""

from __future__ import annotations

from tests.partner_panel import conftest as _panel

PASSWORD = _panel.PASSWORD
api = _panel.api
send = _panel.send
http_login = _panel.http_login
anonymous = _panel.anonymous
seller = _panel.seller
owner_client = _panel.owner_client
member_client = _panel.member_client
partner_audit = _panel.partner_audit
console_org = _panel.console_org
login = _panel.login
