from datetime import date

import app as app_module
from tests.conftest import FAKE_BASE


def test_converts_at_the_rate_the_upstream_published(convert, upstream_calls):
    response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

    assert response.status_code == 200
    assert response.json() == {
        "amount": 250,
        "from": "EUR",
        "to": "TRY",
        "rate": 56.1718,
        "result": 14042.95,
        "rate_date": "2026-08-28",
        "asked_date": "2026-08-28",
        "stale": False,
        "note": None,
        "source": "ECB via frankfurter.dev",
    }


def test_upstream_base_comes_from_the_environment(convert, upstream_calls):
    convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")

    assert str(upstream_calls[0].url).startswith(FAKE_BASE + "/v1/2026-08-28")


class TestDateTheRateBelongsTo:
    """The upstream answers an unpublished date with an earlier rate. We keep
    the answer, but we never let it look like it belongs to the date asked."""

    def test_weekend_answer_carries_the_published_date_and_is_flagged(
        self, convert, upstream_calls
    ):
        # 2026-08-30 is a Sunday; the last publication before it was Friday.
        response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-30")
        body = response.json()

        assert response.status_code == 200
        assert body["asked_date"] == "2026-08-30"
        assert body["rate_date"] == "2026-08-28"
        assert body["stale"] is True

    def test_the_note_is_a_sentence_the_model_can_repeat(self, convert, upstream_calls):
        body = convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-30").json()

        assert body["note"] == (
            "The ECB published no rate for the date asked: 2026-08-30 was a Sunday. "
            "The rate below is the one published on 2026-08-28."
        )

    def test_a_weekday_gap_reads_as_a_holiday_not_as_a_weekend(
        self, convert, upstream_calls, monkeypatch
    ):
        # 2026-08-31 is a Monday the fake did publish on, so pin "today" past it
        # and ask for a Friday the fake skipped.
        body = convert(amount="1", **{"from": "EUR"}, to="USD", date="2026-08-29").json()
        assert "Saturday" in body["note"]

        # A gap on a working day must not claim a weekend.
        note = app_module.staleness_note(date(2026, 1, 1), date(2025, 12, 31))
        assert "holiday or another non-publication day" in note

    def test_latest_is_stale_when_today_has_no_publication(
        self, convert, upstream_calls, monkeypatch
    ):
        # No date in the question. The fake's last publication is 2026-08-31, so
        # on 2026-09-01 the newest rate available is already a day old.
        monkeypatch.setattr(app_module, "today", lambda: date(2026, 9, 1))

        body = convert(amount="100", **{"from": "EUR"}, to="TRY").json()

        assert body["asked_date"] == "2026-09-01"
        assert body["rate_date"] == "2026-08-31"
        assert body["stale"] is True

    def test_latest_is_not_stale_on_a_day_that_did_publish(
        self, convert, upstream_calls, monkeypatch
    ):
        monkeypatch.setattr(app_module, "today", lambda: date(2026, 8, 31))

        body = convert(amount="100", **{"from": "EUR"}, to="TRY").json()

        assert body["stale"] is False
        assert body["note"] is None
