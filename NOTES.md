# Notes

## Decisions

**When the ECB published no rate for the date asked, answer — but never let the
answer look like it belongs to that date.** A Sunday request returns `200` with
Friday's rate, `rate_date` set to Friday, `stale: true`, and a `note` that is a
whole sentence the model can repeat to the customer. Refusing outright was the
other option and it is defensible, but it leaves the model with nothing to say;
this way it can answer *and* tell the customer which day the number is from,
which is what the brief asks the response to make visible.

`asked_date` is compared against "now" when no date is given, so a `latest`
answer fetched on a Sunday morning is still reported as stale. That felt more
honest than quietly treating whatever the upstream returned as current.

**`from` and `to` being equal is refused, not answered with 1.0.** A rate of
1.0 belongs to no publication, so there would be no truthful value to put in
`rate_date`. Refusing keeps every field in a `200` true.

**Money is `Decimal` from the query string to the response.** `amount` is taken
as a string and parsed here rather than letting the framework hand over a
float; the upstream's rate is read with `parse_float=Decimal`. The published
rate is never rounded — only the result is, to cents, once, `ROUND_HALF_UP`.
Rounding a rate like TRY/USD 0.02073 to two places is a 3.5% error, which is
the kind of thing this endpoint exists to avoid.

**Questions with no correct answer are refused before a request goes out.**
Future dates, dates before the series began on 1999-01-04, malformed dates, bad
amounts, same-currency pairs. Spending a round trip on them only buys latency
and a chance to misread whatever comes back.

**`503` and `502` are kept apart.** Unreachable or slow is worth retrying;
answered-but-unusable is not. A calling model can act on that difference.

**The cache is keyed on the base URL, the pair and the date.** Leaving the date
out is what turns one stale answer into every answer — the defect ranked first
in `REVIEW.md`. A settled past day is kept indefinitely; today's rate for ten
minutes, because the ECB publishes in the afternoon.

**One thing in the brief did not match reality.** The documented default,
`https://api.frankfurter.dev`, returns `404` for `/{date}`: the live API serves
its rates under `/v1`. I kept the default exactly as the brief states it and
add the prefix when building the path, unless `FX_UPSTREAM_BASE` already ends
in `/v1`. If the fake upstream used in review does not use that prefix, this is
the one line to change, and I would rather flag it than quietly pick one.

## With another day

- **Say which currency code was wrong.** A `404` from the upstream currently
  becomes `unknown_currency` for the pair. Fetching and caching `/v1/currencies`
  would let the message name the offending code. I left it out because it adds
  a request, a fake and a failure path for a better sentence, not a better number.
- **Move the cache out of the process.** It is per-worker today, so four workers
  mean four copies and four times the upstream traffic. Redis with the same key
  would fix that without changing the logic.
- **Structured logs with a request id.** `logger.exception` is enough to debug
  one failure; it is not enough to answer "how often is the upstream stale".
- **A retry with backoff on `503`.** The endpoint refuses cleanly today, which
  is correct, but one retry on a connect failure would turn most blips into a
  slightly slower success.

## AI tools

Claude Code, throughout, and it is worth saying how rather than just that.

I used it to **measure instead of assume**: before writing anything, I had it
call the live Frankfurter API for a weekend, a future date, a pre-1999 date, an
invalid currency and a same-currency pair, and put the actual responses in a
table. That is where the whole shape of this task came from — the upstream
answers a Sunday with `200` and quietly moves its own `date` field, and no
amount of reading the brief tells you that.

**The decisions were mine and I made them explicitly**: answer-with-a-note
versus refuse on a non-publication day, refusing `EUR→EUR`, accepting ten
decimal places instead of rejecting them, keeping `503` apart from `502`, and
how to handle the `/v1` discrepancy. I reviewed each commit before it was made
rather than accepting a finished repository.

For Part B I did not let it read `tool.py` and report what looked wrong. I ran
the service against the live upstream and checked every claim against what the
upstream said to the same question. Two findings changed rank once I saw the
real output, and one thing I had assumed was a defect — the missing timeout on
the httpx client — turned out to be fine, which is why it is in the
"suspicious but fine" section.

## One thing the AI got wrong

**It pinned dependencies to versions only my machine could install.** The
versions in `requirements.txt` came from `pip freeze` after developing on
Python 3.12, and the current FastAPI release needs 3.10 or newer. Everything
looked fine locally — 38 tests green, the endpoint answering correctly —
because `.venv` was already on disk and nobody was reinstalling anything.

I noticed it by not trusting that. Before calling it done I cloned the pushed
repository into an empty directory and ran `./test.sh` there, the way it will
actually be run. It died during install, before a single test executed: a bash
script's `PATH` finds the system `python3`, which here is 3.9.6, and the pinned
FastAPI simply has no distribution for it. That is the first thing a reviewer
would have seen.

The fix was to pin floors with major-version ceilings instead of exact
versions, so the same file resolves on whatever Python is present. The suite
now passes on 3.9.6 as well as 3.12, and I checked the endpoint against the
live upstream on 3.9 too. The lesson is not about pinning: it is that "the
tests pass" and "someone else can run this" are different claims, and only one
of them was true.

**And one in the logic.** In the first working version, `asked_date` was
reported as `(on or quote.rate_date)` — when no date was given, the date the
upstream returned was used as the date that had been asked for. That makes
`stale` **always** `false` for a `latest` call, because it compares the
upstream's date against itself. A Sunday-morning "what is EUR/TRY today" would
have returned Friday's rate with `stale: false` and no note: exactly the silent
wrong-date answer this service exists to prevent. The tests were green, because
the test was written from the same assumption.

I caught it while writing the weekend cases and asking what `asked_date` even
means when the caller gives no date. It should mean "as of now". The fix was
one line — `asked_day = on if on is not None else today()` — plus two tests
that pin "today" either side of a publication day, so it is now checked in both
directions rather than assumed.
