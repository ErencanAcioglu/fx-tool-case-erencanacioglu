"""The only module in this service that talks to the rate provider.

Upstream is Frankfurter, which republishes the European Central Bank's daily
euro reference rates. Its base URL comes from FX_UPSTREAM_BASE, so it can be
pointed at a fake; the real host appears only as the default below.
"""

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

# The ECB's euro reference rate series starts here. Earlier dates have no rate
# to find, so asking for one is a question we can refuse without a round trip.
SERIES_START = date(1999, 1, 4)

_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 5.0


class UpstreamProblem(Exception):
    """The upstream did not give us a rate we can trust.

    These say what went wrong, not what status code to answer with. Choosing
    that is the HTTP layer's job, which keeps this module out of it.
    """


class Unavailable(UpstreamProblem):
    """Could not be reached, or took too long to answer."""


class Failed(UpstreamProblem):
    """Answered, but with an error status."""


class NoSuchRate(UpstreamProblem):
    """Answered 404: it publishes nothing for that pair."""


class Malformed(UpstreamProblem):
    """Answered 200 with something that is not a usable rate."""


@dataclass(frozen=True)
class Quote:
    """A rate, and the date the upstream says that rate belongs to.

    The two are reported together on purpose: the upstream answers a request for
    a Sunday with Friday's rate and says so in its own `date` field, and callers
    of this module must not lose that.
    """

    rate: Decimal
    rate_date: date


def base_url() -> str:
    """The upstream base URL, read on every call so it can be repointed."""
    base = os.environ.get("FX_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE).rstrip("/")
    # Frankfurter serves rates under /v1, but the documented default is the bare
    # host, so add the prefix unless it is already there.
    return base if base.endswith("/v1") else base + "/v1"


_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
        )
    return _client


def use_client(replacement: Optional[httpx.AsyncClient]) -> None:
    """Install a different HTTP client. Tests use this to fake the upstream."""
    global _client
    _client = replacement


async def fetch_quote(source: str, target: str, on: Optional[date]) -> Quote:
    """Ask the upstream for one rate.

    `on` is the date the caller asked for, or None for the latest publication.
    The date on the returned Quote is the upstream's own, never `on`.
    """
    path = on.isoformat() if on else "latest"
    try:
        response = await client().get(
            f"{base_url()}/{path}",
            params={"base": source, "symbols": target},
        )
    except httpx.HTTPError as problem:
        # Connection refused, DNS failure, timeout: all the same to the caller.
        raise Unavailable(str(problem)) from problem

    if response.status_code == 404:
        raise NoSuchRate(f"no rates published for {source}/{target}")
    if response.status_code != 200:
        raise Failed(f"upstream answered {response.status_code}")

    try:
        # parse_float=Decimal keeps the published rate exact; going through a
        # float here would lose digits before we ever do the multiplication.
        payload = json.loads(response.text, parse_float=Decimal)
    except ValueError as problem:
        raise Malformed("answer was not JSON") from problem

    return _read_quote(payload, target)


def _read_quote(payload: object, target: str) -> Quote:
    """Build a Quote, or refuse. A half-read answer is not worth having."""
    if not isinstance(payload, dict):
        raise Malformed("answer was not a JSON object")
    try:
        rate = Decimal(payload["rates"][target])
        rate_date = date.fromisoformat(payload["date"])
    except (AttributeError, InvalidOperation, KeyError, TypeError, ValueError) as problem:
        raise Malformed(f"answer carried no readable rate and date for {target}") from problem

    # is_finite() first: comparing a NaN Decimal raises rather than returning.
    if not rate.is_finite() or rate <= 0:
        raise Malformed(f"rate for {target} was {rate}")
    return Quote(rate=rate, rate_date=rate_date)
