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
        "source": "ECB via frankfurter.dev",
    }


def test_rate_date_is_the_upstreams_date_not_the_date_we_asked_for(convert, upstream_calls):
    # 2026-08-30 is a Sunday. The upstream answers with Friday's rate and says
    # so; we must repeat its date, not the one in the question.
    response = convert(amount="250", **{"from": "EUR"}, to="TRY", date="2026-08-30")

    assert response.status_code == 200
    assert response.json()["rate_date"] == "2026-08-28"
    assert response.json()["asked_date"] == "2026-08-30"


def test_upstream_base_comes_from_the_environment(convert, upstream_calls):
    convert(amount="1", **{"from": "EUR"}, to="TRY", date="2026-08-28")

    assert str(upstream_calls[0].url).startswith(FAKE_BASE + "/v1/2026-08-28")
