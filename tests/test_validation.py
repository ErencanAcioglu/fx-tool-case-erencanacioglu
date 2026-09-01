"""Questions we refuse without asking the upstream anything.

Every case here is one the upstream cannot answer correctly, so spending a
round trip on it only adds latency and a chance of a confusing answer.
"""

from datetime import date

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
