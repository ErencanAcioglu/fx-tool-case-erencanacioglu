"""HTTP surface for the currency conversion tool.

One endpoint, GET /tools/convert, meant to be called by a language model that is
talking to a paying customer. A wrong number is worse than no number, so the
endpoint reports the date its rate actually belongs to rather than the date it
was asked about.
"""

import logging
import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Union
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import upstream

app = FastAPI(title="fx-tool")
logger = logging.getLogger("fx-tool")

CENTS = Decimal("0.01")
SOURCE_LABEL = "ECB via frankfurter.dev"

# Past a trillion nobody is converting money any more, and an unbounded amount
# lets a caller hand us an exponent large enough to be its own problem.
MAX_AMOUNT = Decimal("1000000000000")

# A rate published on a past day is settled and can be kept indefinitely. One
# for today is not: the ECB publishes in the afternoon, so an answer fetched
# this morning can be superseded within the same day.
TODAYS_RATE_TTL_SECONDS = 600.0

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


@app.exception_handler(Exception)
async def _unexpected(_: Request, error: Exception) -> JSONResponse:
    """Last resort. A tool caller must never be handed an HTML error page."""
    logger.exception("unhandled error while converting", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "Something went wrong on our side, so no rate was returned.",
        },
    )


def today() -> date:
    """Today on the ECB's clock. A seam, so tests can pin the date."""
    return datetime.now(ECB_TIMEZONE).date()


def cache_lifetime(on: Optional[date]) -> float:
    """How long an answer about `on` stays true."""
    return math.inf if on is not None and on < today() else TODAYS_RATE_TTL_SECONDS


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


def parse_amount(raw: Optional[str]) -> Decimal:
    """Read the amount as a Decimal, straight from the text the caller sent.

    Taking it as a string and parsing here, rather than letting the framework
    hand us a float, is what keeps ten decimal places intact all the way to the
    multiplication.
    """
    if raw is None or not raw.strip():
        raise ToolError(
            422, "invalid_amount",
            "amount is required: give the number to convert, for example amount=250.",
        )
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise ToolError(422, "invalid_amount", f"amount must be a number; got {raw!r}.")
    # NaN and Infinity parse fine as Decimals, and comparing them raises, so
    # this check has to come before the range checks below.
    if not value.is_finite():
        raise ToolError(422, "invalid_amount", f"amount must be a finite number; got {raw!r}.")
    if value <= 0:
        raise ToolError(
            422, "invalid_amount",
            f"amount must be greater than zero; got {raw!r}.",
        )
    if value > MAX_AMOUNT:
        raise ToolError(
            422, "invalid_amount",
            f"amount must not be larger than {MAX_AMOUNT:,f}; got {raw!r}.",
        )
    return value


def parse_currency(raw: Optional[str], field: str) -> str:
    """Check the shape of a currency code. Whether it exists is the ECB's call."""
    if raw is None or not raw.strip():
        raise ToolError(
            422, "invalid_currency",
            f"{field} is required: give a three-letter currency code, for example {field}=EUR.",
        )
    code = raw.strip().upper()
    if len(code) != 3 or not (code.isalpha() and code.isascii()):
        raise ToolError(
            422, "invalid_currency",
            f"{field} must be a three-letter currency code such as EUR; got {raw!r}.",
        )
    return code


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
    amount: Optional[str] = Query(default=None, description="How much to convert, e.g. 250"),
    from_: Optional[str] = Query(
        default=None, alias="from", description="Currency to convert from, e.g. EUR"
    ),
    to: Optional[str] = Query(
        default=None, alias="to", description="Currency to convert to, e.g. TRY"
    ),
    on_date: Optional[str] = Query(
        default=None, alias="date", description="Rate date, YYYY-MM-DD. Defaults to the latest."
    ),
):
    """Convert an amount between two currencies at an ECB published rate."""
    value = parse_amount(amount)
    source = parse_currency(from_, "from")
    target = parse_currency(to, "to")
    if source == target:
        raise ToolError(
            422, "same_currency",
            f"from and to are both {source}, so there is no exchange rate to look up.",
        )
    on = parse_date(on_date)

    try:
        quote = await upstream.fetch_quote(source, target, on, cache_lifetime(on))
    except upstream.Unavailable as problem:
        raise ToolError(
            503, "upstream_unavailable",
            "The rate provider could not be reached, so no rate was available. "
            "Nothing was converted; try again shortly.",
        ) from problem
    except upstream.NoSuchRate as problem:
        raise ToolError(
            422, "unknown_currency",
            f"The ECB publishes no rate between {source} and {target}. "
            "Check both currency codes.",
        ) from problem
    except upstream.Failed as problem:
        raise ToolError(
            502, "upstream_error",
            "The rate provider returned an error, so no rate was available. "
            "Nothing was converted.",
        ) from problem
    except upstream.Malformed as problem:
        raise ToolError(
            502, "upstream_invalid_response",
            "The rate provider's answer could not be read as a rate, so it was "
            "discarded rather than guessed at.",
        ) from problem

    # No date in the question means "as of now", so that is what we compare the
    # published date against. On a Sunday morning, "latest" is still stale.
    asked_day = on if on is not None else today()
    stale = quote.rate_date != asked_day

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
