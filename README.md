# fx-tool

One HTTP endpoint an AI agent can call to convert money at a European Central
Bank published rate. It reports the date the rate actually belongs to, and
refuses rather than guess: a wrong number is worse than no number.

Case brief: [MangoLab-AI/case-fx-tool](https://github.com/MangoLab-AI/case-fx-tool).
`tool.py` in this repository is the version under review in [REVIEW.md](REVIEW.md).

## Run

```sh
./run.sh                                           # http://127.0.0.1:8080
PORT=9000 ./run.sh                                 # a different port
FX_UPSTREAM_BASE=http://127.0.0.1:9999 ./run.sh    # a fake upstream
```

The first run creates `.venv` and installs `requirements.txt`; later runs skip
straight to serving.

| Variable | Default | |
|---|---|---|
| `PORT` | `8080` | port to listen on |
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | upstream base URL |

## Test

```sh
./test.sh
```

38 tests, no network needed. The upstream is faked in-process with
`httpx.MockTransport`; one test points `FX_UPSTREAM_BASE` at a genuinely closed
port and checks the refusal that comes back.

## The endpoint

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

`date` is optional. Without it the latest publication is used.

```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 56.1718,
  "result": 14042.95,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "stale": false,
  "note": null,
  "source": "ECB via frankfurter.dev"
}
```

`rate_date` is the day the rate belongs to, taken from the upstream's own
answer. `asked_date` is the day that was asked about. When they differ, `stale`
is `true` and `note` is a sentence the model can repeat to the customer without
composing one of its own:

```json
{
  "rate": 56.1718,
  "result": 14042.95,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-30",
  "stale": true,
  "note": "The ECB published no rate for the date asked: 2026-08-30 was a Sunday. The rate below is the one published on 2026-08-28."
}
```

## What it does in each case

| Case | Answer |
|---|---|
| The ECB published nothing that day (weekend, holiday) | `200` with the previous publication, `rate_date` set to that day, `stale: true`, and a `note` naming the day the number is from |
| The date is in the future | `422 date_in_future`, without asking the upstream |
| The date is before the series began (1999-01-04) | `422 date_before_series`, without asking the upstream |
| The currency code does not exist | `422 unknown_currency`, after the upstream answers `404` |
| `from` and `to` are the same | `422 same_currency`, without asking the upstream. A rate of 1.0 belongs to no publication, so there would be no honest `rate_date` |
| The upstream is slow or unreachable | `503 upstream_unavailable`. Connect timeout 2s, read timeout 5s |
| The upstream returns 500 | `502 upstream_error` |
| The upstream returns something that is not JSON, or JSON with no usable rate and date | `502 upstream_invalid_response`. A rate of zero or a missing date counts as unusable |
| `amount` is missing, zero, or negative | `422 invalid_amount` |
| `amount` has ten decimal places | Accepted. It is parsed as a `Decimal` from the raw text, multiplied exactly, and only the result is rounded, to cents, once |

## Error codes

Every failure is a non-2xx status with `{"error": ..., "message": ...}` and no
numbers in it.

| Status | `error` | When |
|---|---|---|
| 422 | `invalid_amount` | missing, not a number, zero, negative, NaN/Infinity, or above 1,000,000,000,000 |
| 422 | `invalid_currency` | missing, or not three ASCII letters |
| 422 | `same_currency` | `from` and `to` are the same currency |
| 422 | `invalid_date` | not a calendar date in `YYYY-MM-DD` form |
| 422 | `date_in_future` | later than today on the ECB's clock |
| 422 | `date_before_series` | earlier than 1999-01-04 |
| 422 | `unknown_currency` | the upstream publishes no rate for the pair |
| 422 | `invalid_request` | the query string could not be read at all |
| 502 | `upstream_error` | the upstream answered with an error status |
| 502 | `upstream_invalid_response` | the upstream answered `200` with something unusable |
| 503 | `upstream_unavailable` | the upstream could not be reached, or was too slow |
| 500 | `internal_error` | a bug on our side |

`503` is worth retrying. `502` is not: the upstream answered, it just answered
with something that cannot be trusted.

## Three things worth knowing

**The upstream moves the date silently.** Ask Frankfurter for a Sunday and it
answers `200` with Friday's rate, saying so only in its own `date` field. Every
answer here carries that date, never the date in the question.

**`/v1`.** The brief's default is `https://api.frankfurter.dev`, but the live
API serves rates under `/v1` and returns `404` without it. The default is left
exactly as the brief states it and the prefix is added when building the path,
unless `FX_UPSTREAM_BASE` already ends in `/v1`. See [NOTES.md](NOTES.md).

**A repeated question costs nothing.** Answers are cached on the base URL, the
pair and the date. A settled past day is kept indefinitely; today's rate for ten
minutes, because the ECB publishes in the afternoon. Failures are never cached.

## Layout

```
app.py        the endpoint: validation, the error contract, the response
upstream.py   the only module that makes HTTP calls, plus the cache
tests/        conftest.py holds a fake upstream that behaves like the real one
tool.py       given by the brief, reviewed in REVIEW.md, not used by the service
```
