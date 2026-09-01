"""Questions we refuse without asking the upstream anything.

Every case here is one the upstream cannot answer correctly, so spending a
round trip on it only adds latency and a chance of a confusing answer.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import app as app_module


class TestDate:
    def test_a_date_that_is_not_a_date_is_refused(self, convert, upstream_calls):
        response = convert(amount="10", **{"from": "EUR"}, to="TRY", date="30-08-2026")

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_date"
        assert upstream_calls == []

    def test_a_future_date_is_refused(self, convert, upstream_calls):
        # "today" is pinned to 2026-09-01 for the suite.
        response = convert(amount="10", **{"from": "EUR"}, to="TRY", date="2026-09-02")

        assert response.status_code == 422
        assert response.json()["error"] == "date_in_future"
        assert upstream_calls == []

    def test_a_date_before_the_series_began_is_refused(self, convert, upstream_calls):
        response = convert(amount="10", **{"from": "EUR"}, to="TRY", date="1999-01-03")

        assert response.status_code == 422
        assert response.json()["error"] == "date_before_series"
        assert upstream_calls == []

    def test_today_itself_is_allowed(self, convert, upstream_calls, monkeypatch):
        # The cutoff is "after today", not "today". Reaching the upstream at all
        # is the proof, since a refusal would have stopped before it.
        monkeypatch.setattr(app_module, "today", lambda: date(2026, 8, 31))

        convert(amount="10", **{"from": "EUR"}, to="TRY", date="2026-08-31")

        assert len(upstream_calls) == 1

    def test_a_refusal_says_error_and_message_and_nothing_else(self, convert, upstream_calls):
        body = convert(amount="10", **{"from": "EUR"}, to="TRY", date="tomorrow").json()

        assert sorted(body) == ["error", "message"]
        assert body["message"].endswith(".")


class TestAmount:
    def test_a_missing_amount_is_refused(self, convert, upstream_calls):
        response = convert(**{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_amount"
        assert upstream_calls == []

    def test_zero_and_negative_amounts_are_refused(self, convert, upstream_calls):
        for amount in ["0", "0.00", "-1", "-0.01"]:
            response = convert(amount=amount, **{"from": "EUR"}, to="TRY", date="2026-08-28")

            assert response.status_code == 422, amount
            assert response.json()["error"] == "invalid_amount", amount
        assert upstream_calls == []

    def test_amounts_that_are_not_numbers_are_refused(self, convert, upstream_calls):
        # "NaN" and "Infinity" are valid Decimals, which is exactly why they are
        # checked for: they would sail through a naive parse and poison the sum.
        for amount in ["", "abc", "1,5", "NaN", "Infinity", "-Infinity"]:
            response = convert(amount=amount, **{"from": "EUR"}, to="TRY", date="2026-08-28")

            assert response.status_code == 422, amount
            assert response.json()["error"] == "invalid_amount", amount
        assert upstream_calls == []

    def test_an_absurd_amount_is_refused(self, convert, upstream_calls):
        response = convert(amount="1e30", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_amount"
        assert upstream_calls == []

    def test_ten_decimal_places_reach_the_multiplication_intact(self, convert, upstream_calls):
        body = convert(
            amount="1.2345678901", **{"from": "EUR"}, to="TRY", date="2026-08-28"
        ).json()

        expected = (Decimal("1.2345678901") * Decimal("56.1718")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert body["amount"] == 1.2345678901
        assert body["result"] == float(expected)

    def test_the_rate_is_reported_exactly_as_published(self, convert, upstream_calls):
        # Rounding the rate to two places would move this conversion by 1,800
        # lira. The published rate goes out as published; only the result is
        # rounded, once, at the end.
        body = convert(amount="1000000", **{"from": "EUR"}, to="TRY", date="2026-08-28").json()

        assert body["rate"] == 56.1718
        assert body["result"] == 56171800


class TestCurrency:
    def test_a_missing_currency_is_refused(self, convert, upstream_calls):
        assert convert(amount="10", to="TRY").json()["error"] == "invalid_currency"
        assert convert(amount="10", **{"from": "EUR"}).json()["error"] == "invalid_currency"
        assert upstream_calls == []

    def test_a_code_that_is_not_three_letters_is_refused(self, convert, upstream_calls):
        for code in ["EU", "EURO", "E1R", "123", "TÜR"]:
            response = convert(amount="10", **{"from": code}, to="TRY", date="2026-08-28")

            assert response.status_code == 422, code
            assert response.json()["error"] == "invalid_currency", code
        assert upstream_calls == []

    def test_lowercase_codes_are_accepted_and_echoed_uppercase(self, convert, upstream_calls):
        body = convert(amount="10", **{"from": "eur"}, to="try", date="2026-08-28").json()

        assert body["from"] == "EUR"
        assert body["to"] == "TRY"

    def test_converting_a_currency_into_itself_is_refused(self, convert, upstream_calls):
        response = convert(amount="10", **{"from": "EUR"}, to="eur", date="2026-08-28")

        assert response.status_code == 422
        assert response.json()["error"] == "same_currency"
        # A rate of 1.0 belongs to no publication date, so there is nothing
        # honest to put in rate_date. Refusing is the only answer that keeps
        # every field true.
        assert upstream_calls == []


class TestErrorShape:
    """A caller that gets the URL wrong should meet the same contract as one
    that gets a parameter wrong, not the framework's own error body."""

    def test_an_unknown_path_fails_in_our_shape(self, client):
        response = client.get("/tools/convrt")

        assert response.status_code == 404
        assert sorted(response.json()) == ["error", "message"]
        assert response.json()["error"] == "unknown_endpoint"

    def test_a_wrong_method_fails_in_our_shape(self, client):
        response = client.post("/tools/convert")

        assert response.status_code == 405
        assert sorted(response.json()) == ["error", "message"]
        assert response.json()["error"] == "method_not_allowed"
