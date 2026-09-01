"""What the caller is told when the upstream is down, slow, or talking nonsense.

The rule these all check is the same one: no number goes out unless it came
from the upstream and we could read the date it belongs to. A refusal is an
acceptable answer here; a plausible-looking wrong one is not.
"""

import socket

import httpx
from fastapi.testclient import TestClient

import app as app_module
import upstream


def _refusal(response):
    """A refusal says only what went wrong, and carries no numbers at all."""
    body = response.json()
    assert sorted(body) == ["error", "message"]
    assert "rate" not in body and "result" not in body
    return body


class TestUnreachable:
    def test_connection_refused_is_reported_as_unavailable(self, convert, upstream_that):
        def refuse(request):
            raise httpx.ConnectError("connection refused", request=request)

        upstream_that(refuse)

        response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 503
        assert _refusal(response)["error"] == "upstream_unavailable"

    def test_a_slow_upstream_is_reported_not_waited_on_forever(self, convert, upstream_that):
        def stall(request):
            raise httpx.ReadTimeout("timed out", request=request)

        upstream_that(stall)

        response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 503
        assert _refusal(response)["error"] == "upstream_unavailable"

    def test_a_closed_port_is_refused_rather_than_guessed(self, convert, monkeypatch):
        # This is exactly how the service is reviewed: FX_UPSTREAM_BASE pointed
        # at a closed port, with a real socket and no fake in the way.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
        probe.close()

        monkeypatch.setenv("FX_UPSTREAM_BASE", f"http://127.0.0.1:{closed_port}")
        upstream.use_client(None)
        try:
            response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        finally:
            upstream.use_client(None)

        assert response.status_code == 503
        assert _refusal(response)["error"] == "upstream_unavailable"


class TestBadAnswers:
    def test_a_500_is_passed_on_as_an_upstream_error(self, convert, upstream_that):
        upstream_that(lambda request: httpx.Response(500, text="upstream on fire"))

        response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 502
        assert _refusal(response)["error"] == "upstream_error"

    def test_an_html_error_page_is_not_mistaken_for_a_rate(self, convert, upstream_that):
        upstream_that(
            lambda request: httpx.Response(
                200, text="<html><body>502 Bad Gateway</body></html>",
                headers={"content-type": "text/html"},
            )
        )

        response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 502
        assert _refusal(response)["error"] == "upstream_invalid_response"

    def test_json_without_a_usable_rate_is_discarded(self, convert, upstream_that):
        unusable = [
            {"date": "2026-08-28", "rates": {}},               # pair missing
            {"date": "2026-08-28"},                            # no rates at all
            {"rates": {"TRY": 56.1718}},                       # no date to stand on
            {"date": "2026-08-28", "rates": {"TRY": "abc"}},   # rate is not a number
            {"date": "not-a-date", "rates": {"TRY": 56.1718}},
            {"date": "2026-08-28", "rates": {"TRY": 0}},       # a rate of zero is not a rate
            {"date": "2026-08-28", "rates": {"TRY": -1}},
            ["not", "an", "object"],
        ]
        for payload in unusable:
            upstream_that(lambda request, body=payload: httpx.Response(200, json=body))

            response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

            assert response.status_code == 502, payload
            assert _refusal(response)["error"] == "upstream_invalid_response", payload

    def test_a_404_means_the_pair_is_not_published(self, convert, upstream_that):
        upstream_that(lambda request: httpx.Response(404, json={"message": "not found"}))

        response = convert(amount="250", **{"from": "EUR"}, to="XXX", date="2026-08-28")

        assert response.status_code == 422
        assert _refusal(response)["error"] == "unknown_currency"


def test_an_unexpected_failure_still_answers_in_the_error_shape(monkeypatch, upstream_calls):
    def boom(_):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "parse_amount", boom)
    client = TestClient(app_module.app, raise_server_exceptions=False)

    response = client.get(
        "/tools/convert", params={"amount": "1", "from": "EUR", "to": "TRY"}
    )

    assert response.status_code == 500
    assert _refusal(response)["error"] == "internal_error"
