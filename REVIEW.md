# Review of tool.py

I ran it against the live upstream and checked each finding by comparing what
it answered with what the upstream said to the same question. Every command
below is one I actually ran; the outputs are real.

Findings are ranked by what they cost a customer, not by how they read.

---

## 1. The cache answers a question it was never asked

```python
key = f"{base}-{target}"
if key in _cache:
    return _cache[key], str(on or date.today())
```

The date is not part of the key, and nothing expires. The first rate fetched
for a pair is returned for every later question about that pair, wearing
whatever date the new caller asked about.

**What it does to a customer.** One person asks what something was worth in
March 2020. Everyone after them is quoted that 2020 rate as if it were today's.
For EUR/TRY that gap is roughly eightfold: a 250 EUR invoice comes back as
about 1,750 TRY instead of about 14,000. The response looks completely normal,
the number is plausible enough to act on, and it stays wrong until the process
restarts.

**How I verified it.**

```sh
curl '.../tools/convert?amount=1&from_=EUR&to=USD&on=2020-03-16'
# {"rate":1.12,"rate_date":"2020-03-16"}

curl '.../tools/convert?amount=1&from_=EUR&to=USD'
# {"rate":1.12,"rate_date":"2026-09-02"}     <- 2020's rate, today's date

curl 'https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD'
# {"date":"2026-09-01","rates":{"USD":1.159}}
```

---

## 2. The date on the answer is the date that was asked for, not the date the rate is from

Both return paths report `str(on or date.today())`. The upstream's own `date`
field — the one thing that says which publication a rate came from — is never
read. The fallback makes this worse rather than better: when the upstream has
nothing for a date it answers `404`, and the code then fetches `/latest` and
labels today's rate with the date in the question.

**What it does to a customer.** The model tells them a number belongs to a day
it does not belong to, which is exactly the thing an invoice or a tax filing
gets checked against later. Asked for a Sunday, it presents Friday's rate as
Sunday's. Asked for a date the ECB will never publish, it invents one:

```sh
curl '.../tools/convert?amount=1&from_=EUR&to=JPY&on=2030-01-01'
# {"rate":185.63,"rate_date":"2030-01-01"}
curl 'https://api.frankfurter.dev/v1/2030-01-01?base=EUR&symbols=JPY'
# {"message":"not found"}                    <- upstream has no such day
```

**How I verified it.** The pair above, and the same comparison for a weekend:

```sh
curl '.../tools/convert?amount=1&from_=EUR&to=GBP&on=2026-08-30'
# {"rate":0.86,"rate_date":"2026-08-30"}
curl 'https://api.frankfurter.dev/v1/2026-08-30?base=EUR&symbols=GBP'
# {"date":"2026-08-28","rates":{"GBP":0.8572}}
```

The upstream says 2026-08-28. The service says 2026-08-30. Nothing in the
response lets the model tell the customer which day the number is really from.

---

## 3. Two of the four documented parameters are silently ignored

The brief's call is `?amount=250&from=EUR&to=TRY&date=2026-08-28`. The handler
declares `from_` and `on`, so `from` and `date` are not read at all — they fall
back to the defaults `EUR` and "latest".

**What it does to a customer.** They ask for 250 US dollars at last Friday's
rate. They are quoted 250 **euros** at **today's** rate, with a 200 and no
warning. Two currencies and one date wrong in a single answer, and nothing in
the response hints that anything was dropped.

**How I verified it.** Called it exactly as the brief documents:

```sh
curl '.../tools/convert?amount=250&from=USD&to=TRY&date=2026-08-28'
# {"amount":250.0,"from":"EUR","to":"TRY","rate":55.95,
#  "result":13987.5,"rate_date":"2026-09-02"}
```

`from` came back as EUR and the rate is today's, not 2026-08-28's.

---

## Three smaller ones

**A failure is reported as a rate of zero, with a 200.** The `except Exception`
block returns `rate: 0.0, result: 0.0` and a normal-looking body. The model has
no way to tell that apart from a real answer, so the customer is told their 250
EUR is worth 0.00. Verified with `to=XXX`, which returns
`{"rate":0.0,"result":0.0}` and HTTP 200. This one is close to being finding 4;
what keeps it below the three above is that a zero is at least visibly absurd,
while a stale-but-plausible rate is not.

**`round(rate, 2)` throws away most of a small rate.** The published rate is
rounded before it is used. For TRY/USD the rate is 0.02073 and becomes 0.02, a
3.5% error in the customer's favour or against them depending on direction:
1,000,000 TRY is quoted as 20,000 USD instead of 20,730. Verified against
`https://api.frankfurter.dev/v1/2026-08-28?base=TRY&symbols=USD`. Rounding the
*result* to cents is right; rounding the rate is not.

**`amount` is not validated.** `amount=nan` returns `{"amount":null,
"result":null}` with a 200, and `amount=-500` returns `-3735.0`. A tool that
answers `null` teaches the model nothing about what went wrong.

---

## The one I would fix before shipping tonight

**Finding 1, the cache key.** It is a one-line change — put the date and a TTL
in the key — and it stops the largest and most frequent wrong number. It is
also the only one on this list that gets *worse* the longer the process stays
up, and the hardest to notice: findings 2 and 3 produce a wrong date or a wrong
currency that a careful reader can spot in the response, while a poisoned cache
produces a well-formed answer that is simply untrue.

If I were allowed a second line, I would delete the `except Exception` block
that returns zeros. Failing loudly is not a feature I would want to wait a
sprint for.

---

## Things that look suspicious but are fine

**`client = httpx.AsyncClient()` with no timeout argument.** It looks like a
request could hang forever. It cannot: httpx defaults to a 5 second timeout on
connect, read, write and pool. Confirmed with
`python -c "import httpx; print(httpx.AsyncClient().timeout)"` →
`Timeout(timeout=5.0)`. A separate, shorter connect timeout would be nicer, but
nothing hangs.

**The cache is unbounded.** In principle a module-level dict that never evicts
is a leak. Here the key is only `base-target`, so the whole key space is the
currency pairs the ECB publishes — a few hundred entries at most. The cache's
problem is its key, not its size.

**The global client is never closed.** There is no `aclose()` and no lifespan
handler. For a single long-lived process holding one client this is not a leak;
the connections are reused, which is the point of a shared client.

**Rounding the result to two decimals.** That part is correct — money is
quoted in cents. Only the rounding of the *rate* is wrong.

**The `/health` endpoint.** Not required by the brief, but harmless and useful
to anything running this behind a load balancer.
