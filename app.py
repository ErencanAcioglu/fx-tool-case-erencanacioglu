"""HTTP surface for the currency conversion tool.

One endpoint, GET /tools/convert, meant to be called by a language model that is
talking to a paying customer. A wrong number is worse than no number, so the
endpoint reports the date its rate actually belongs to rather than the date it
was asked about.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Union

from fastapi import FastAPI, Query

import upstream

app = FastAPI(title="fx-tool")

CENTS = Decimal("0.01")
SOURCE_LABEL = "ECB via frankfurter.dev"


def _number(value: Decimal) -> Union[int, float]:
    """Render a Decimal as a JSON number, without a pointless trailing .0."""
    return int(value) if value == value.to_integral_value() else float(value)


@app.get("/tools/convert")
async def convert(
    amount: str = Query(description="How much to convert, e.g. 250"),
    source: str = Query(alias="from", description="Currency to convert from, e.g. EUR"),
    target: str = Query(alias="to", description="Currency to convert to, e.g. TRY"),
    asked: Optional[str] = Query(
        default=None, alias="date", description="Rate date, YYYY-MM-DD. Defaults to the latest."
    ),
):
    """Convert an amount between two currencies at an ECB published rate."""
    source = source.upper()
    target = target.upper()
    on = date.fromisoformat(asked) if asked else None

    quote = await upstream.fetch_quote(source, target, on)

    value = Decimal(amount)
    result = (value * quote.rate).quantize(CENTS, rounding=ROUND_HALF_UP)

    return {
        "amount": _number(value),
        "from": source,
        "to": target,
        "rate": _number(quote.rate),
        "result": _number(result),
        "rate_date": quote.rate_date.isoformat(),
        "asked_date": (on or quote.rate_date).isoformat(),
        "source": SOURCE_LABEL,
    }
