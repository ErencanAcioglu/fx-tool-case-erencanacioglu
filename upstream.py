"""The only module in this service that talks to the rate provider.

Upstream is Frankfurter, which republishes the European Central Bank's daily
euro reference rates. Its base URL comes from FX_UPSTREAM_BASE, so it can be
pointed at a fake; the real host appears only as the default below.
"""

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import httpx

DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 5.0


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
    response = await client().get(
        f"{base_url()}/{path}",
        params={"base": source, "symbols": target},
    )
    # parse_float=Decimal keeps the published rate exact; going through float
    # here would lose digits before we ever do the multiplication.
    payload = json.loads(response.text, parse_float=Decimal)
    return Quote(
        rate=Decimal(payload["rates"][target]),
        rate_date=date.fromisoformat(payload["date"]),
    )
