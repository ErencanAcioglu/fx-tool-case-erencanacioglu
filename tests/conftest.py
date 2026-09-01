"""An in-process fake of the upstream, so the tests never touch the network.

The fake copies the behaviour of the real Frankfurter API that matters here:
it answers a date it did not publish on with the previous publication, and it
says which date it used in its own `date` field.
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import upstream

FAKE_BASE = "http://fake-upstream.test"

# EUR-based rates the fake has "published". The gap is real: 2026-08-29 and
# 2026-08-30 are a Saturday and a Sunday, and the ECB publishes on neither.
PUBLISHED = {
    date(2026, 8, 27): {"TRY": "56.0912", "USD": "1.1655"},
    date(2026, 8, 28): {"TRY": "56.1718", "USD": "1.1682"},
    date(2026, 8, 31): {"TRY": "55.9871", "USD": "1.1701"},
}
FIRST_PUBLISHED = min(PUBLISHED)
LAST_PUBLISHED = max(PUBLISHED)
KNOWN_CURRENCIES = {"EUR"} | {code for rates in PUBLISHED.values() for code in rates}


def _publication_for(day):
    """The publication the upstream would use for `day`, or None."""
    published = [d for d in sorted(PUBLISHED) if d <= day]
    return published[-1] if published else None


def _rate(rates, source, target):
    per_eur = {"EUR": Decimal("1")}
    per_eur.update({code: Decimal(value) for code, value in rates.items()})
    if source == "EUR":
        return per_eur[target]  # exactly as published, no rounding
    return (per_eur[target] / per_eur[source]).quantize(Decimal("0.00001"))


def _handler(calls):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)

        source = request.url.params.get("base", "EUR")
        target = request.url.params.get("symbols", "")
        if source not in KNOWN_CURRENCIES or target not in KNOWN_CURRENCIES:
            return httpx.Response(404, json={"message": "not found"})
        if source == target:
            return httpx.Response(422, json={"message": "bad currency pair"})

        asked = request.url.path.rsplit("/", 1)[-1]
        if asked == "latest":
            day = LAST_PUBLISHED
        else:
            asked_day = date.fromisoformat(asked)
            if asked_day > LAST_PUBLISHED or asked_day < FIRST_PUBLISHED:
                return httpx.Response(404, json={"message": "not found"})
            day = _publication_for(asked_day)

        rate = _rate(PUBLISHED[day], source, target)
        # Hand back raw JSON rather than a dict, so the rate stays a JSON number
        # with its full precision, exactly as the real upstream sends it.
        body = (
            '{"amount":1.0,"base":"%s","date":"%s","rates":{"%s":%s}}'
            % (source, day.isoformat(), target, rate)
        )
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return handle


@pytest.fixture
def upstream_calls(monkeypatch):
    """Point the service at the fake upstream and record every request to it."""
    calls = []
    monkeypatch.setenv("FX_UPSTREAM_BASE", FAKE_BASE)
    upstream.use_client(httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))))
    yield calls
    upstream.use_client(None)


@pytest.fixture
def client():
    return TestClient(app_module.app)


@pytest.fixture
def convert(client):
    def get(**params):
        return client.get("/tools/convert", params=params)

    return get
