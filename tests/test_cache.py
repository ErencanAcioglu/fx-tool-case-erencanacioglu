"""What the cache is allowed to remember, and for how long.

A cache that answers the wrong question is worse than no cache: it turns one
bad answer into every answer.
"""

import httpx

import app as app_module
import upstream

PUBLISHED_JSON = (
    '{"amount":1.0,"base":"EUR","date":"2026-08-28","rates":{"TRY":56.1718}}'
)


class TestRepeatedQuestions:
    def test_the_same_question_is_only_asked_once(self, convert, upstream_calls):
        first = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        second = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert first.json() == second.json()
        assert len(upstream_calls) == 1

    def test_a_different_amount_reuses_the_same_rate(self, convert, upstream_calls):
        # The amount is arithmetic on our side; it is not part of the question
        # the upstream was asked.
        convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        body = convert(amount="500", **{"from": "EUR"}, to="TRY", date="2026-08-28").json()

        assert len(upstream_calls) == 1
        assert body["result"] == 28085.90

    def test_a_different_date_is_a_different_question(self, convert, upstream_calls):
        # Keying a rate cache on the currency pair alone makes the first date
        # asked about answer for every date after it. The date is in the key.
        older = convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-27").json()
        newer = convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28").json()

        assert len(upstream_calls) == 2
        assert older["rate"] != newer["rate"]
        assert older["rate_date"] == "2026-08-27"
        assert newer["rate_date"] == "2026-08-28"

    def test_a_different_pair_is_a_different_question(self, convert, upstream_calls):
        convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        convert(amount="1", **{"from": "EUR"}, to="USD", date="2026-08-28")

        assert len(upstream_calls) == 2


class TestHowLongAnAnswerLasts:
    def test_a_settled_day_is_kept_indefinitely(self, convert, upstream_calls, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(upstream, "_clock", lambda: now[0])

        convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        now[0] += 365 * 24 * 3600
        convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert len(upstream_calls) == 1

    def test_todays_rate_is_asked_again_once_it_may_have_changed(
        self, convert, upstream_calls, monkeypatch
    ):
        # The ECB publishes in the afternoon, so a "latest" answer fetched this
        # morning can be superseded while the process is still running.
        now = [1000.0]
        monkeypatch.setattr(upstream, "_clock", lambda: now[0])

        convert(amount="1", **{"from": "EUR"}, to="TRY")
        convert(amount="1", **{"from": "EUR"}, to="TRY")
        assert len(upstream_calls) == 1

        now[0] += app_module.TODAYS_RATE_TTL_SECONDS + 1
        convert(amount="1", **{"from": "EUR"}, to="TRY")
        assert len(upstream_calls) == 2


class TestWhatIsNotCached:
    def test_a_failed_answer_is_not_remembered(self, convert, upstream_that):
        calls = []

        def flaky(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500, text="upstream on fire")
            return httpx.Response(
                200, content=PUBLISHED_JSON, headers={"content-type": "application/json"}
            )

        upstream_that(flaky)

        assert convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28").status_code == 502
        assert convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28").status_code == 200
        assert len(calls) == 2

    def test_repointing_the_upstream_does_not_reuse_its_answers(
        self, convert, upstream_calls, monkeypatch
    ):
        convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")
        monkeypatch.setenv("FX_UPSTREAM_BASE", "http://another-upstream.test")
        convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")

        assert len(upstream_calls) == 2
