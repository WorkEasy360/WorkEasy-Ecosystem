"""The scrape endpoint (Phase 12, G-35): gauges from authoritative state,
behind a bearer token, reachable like a probe."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

TOKEN = "t" * 40


@pytest.fixture
def scrape(client, settings):
    settings.METRICS_TOKEN = TOKEN
    settings.SECURE_SSL_REDIRECT = True
    settings.ALLOWED_HOSTS = ["app.workeasy360.com"]

    def get(token: str | None = TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return client.get("/healthz/metrics", HTTP_HOST="10.0.3.7:8000", **headers)

    return get


def _samples(body: str) -> dict[str, float]:
    out = {}
    for line in body.strip().splitlines():
        key, value = line.rsplit(" ", 1)
        out[key] = float(value)
    return out


def test_no_token_or_a_wrong_one_is_a_404(scrape, settings):
    assert scrape(None).status_code == 404
    assert scrape("wrong").status_code == 404
    settings.METRICS_TOKEN = ""
    assert scrape("").status_code == 404
    assert scrape(TOKEN).status_code == 404


def test_the_scrape_reports_every_source_up_and_the_core_gauges(scrape):
    response = scrape()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain; version=0.0.4")
    samples = _samples(response.content.decode())
    assert samples["w360_database_up"] == 1.0
    for source in ("outbox", "due_work", "products", "partner_domains"):
        assert samples[f'w360_gauge_source_up{{source="{source}"}}'] == 1.0, source
    assert "w360_outbox_oldest_pending_seconds" in samples
    assert "w360_due_work_oldest_overdue_seconds" in samples
    assert any(k.startswith("w360_broker_up") for k in samples)


def test_a_failing_gauge_source_is_reported_not_fatal(scrape, monkeypatch):
    from workeasy360.platform.observability import health

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setitem(health._registered_gauges, "broken", broken)
    samples = _samples(scrape().content.decode())
    assert samples['w360_gauge_source_up{source="broken"}'] == 0.0
    assert samples["w360_database_up"] == 1.0


def test_labels_are_escaped():
    from workeasy360.platform.observability.health import _render

    text = _render([("m", {"l": 'a"b' + chr(92) + chr(10)}, 1.0)])
    assert text == 'm{l="a' + chr(92) + '"b' + chr(92) * 2 + chr(92) + 'n"} 1.0' + chr(10)
