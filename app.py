"""HTTP surface for the currency conversion tool.

One endpoint, GET /tools/convert, meant to be called by a language model that is
talking to a paying customer. A wrong number is worse than no number, so the
endpoint reports the date its rate actually belongs to rather than the date it
was asked about.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Union
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import upstream

app = FastAPI(title="fx-tool")

CENTS = Decimal("0.01")
SOURCE_LABEL = "ECB via frankfurter.dev"

# The ECB publishes once a working day, on its own clock. Using that clock means
# "latest" means the same thing here as it does upstream.
ECB_TIMEZONE = ZoneInfo("Europe/Berlin")

# Spelled out rather than via strftime, which would follow the server's locale.
WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


class ToolError(Exception):
    """A refusal: a short code for the model, a sentence for the customer.

    Raised instead of guessing. Every non-2xx answer this service gives goes
    through here, so the shape of a failure never varies.
    """

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@app.exception_handler(ToolError)
async def _refusal(_: Request, error: ToolError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"error": error.code, "message": error.message},
    )


@app.exception_handler(RequestValidationError)
async def _unreadable_request(_: Request, error: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422 body has a different shape; restate it as ours."""
    fields = ", ".join(sorted({str(item["loc"][-1]) for item in error.errors()}))
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_request",
            "message": f"The request could not be read. Check these query parameters: {fields}.",
        },
    )


def today() -> date:
    """Today on the ECB's clock. A seam, so tests can pin the date."""
    return datetime.now(ECB_TIMEZONE).date()


def staleness_note(asked: date, published: date) -> str:
    """One sentence the model can repeat to the customer, as is."""
    if asked.weekday() >= 5:
        reason = f"{asked.isoformat()} was a {WEEKDAY_NAMES[asked.weekday()]}"
    else:
        reason = f"{asked.isoformat()} was a holiday or another non-publication day"
    return (
        f"The ECB published no rate for the date asked: {reason}. "
        f"The rate below is the one published on {published.isoformat()}."
    )


def parse_date(raw: Optional[str]) -> Optional[date]:
    """Turn the caller's date into one worth asking the upstream about.

    Refusing here keeps two whole classes of wrong answer off the table: the
    upstream cannot invent a future rate, and it has nothing before 1999.
    """
    if raw is None:
        return None
    try:
        asked = date.fromisoformat(raw)
    except ValueError:
        raise ToolError(
            422, "invalid_date",
            f"date must be a calendar date written as YYYY-MM-DD; got {raw!r}.",
        )
    if asked > today():
        raise ToolError(
            422, "date_in_future",
            f"{asked.isoformat()} is in the future, and no rate has been published for it.",
        )
    if asked < upstream.SERIES_START:
        raise ToolError(
            422, "date_before_series",
            f"The ECB's euro reference rates begin on "
            f"{upstream.SERIES_START.isoformat()}, so there is none for {asked.isoformat()}.",
        )
    return asked


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
    on = parse_date(asked)

    quote = await upstream.fetch_quote(source, target, on)

    # No date in the question means "as of now", so that is what we compare the
    # published date against. On a Sunday morning, "latest" is still stale.
    asked_day = on if on else today()
    stale = quote.rate_date != asked_day

    value = Decimal(amount)
    result = (value * quote.rate).quantize(CENTS, rounding=ROUND_HALF_UP)

    return {
        "amount": _number(value),
        "from": source,
        "to": target,
        "rate": _number(quote.rate),
        "result": _number(result),
        "rate_date": quote.rate_date.isoformat(),
        "asked_date": asked_day.isoformat(),
        "stale": stale,
        "note": staleness_note(asked_day, quote.rate_date) if stale else None,
        "source": SOURCE_LABEL,
    }
