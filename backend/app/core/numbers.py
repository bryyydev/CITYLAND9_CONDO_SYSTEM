"""Strict parsing of numbers typed by users (amounts, rates, readings, areas).

Rules (all money in the system):
  * Decimal arithmetic only; money is rounded to centavos with ROUND_HALF_UP ("0.005 -> 0.01").
  * Rejected, never silently turned into 0: empty (unless allowed), malformed text, NaN,
    Infinity, exponents, more decimals than allowed, values above the limit, negatives
    (unless allowed).
  * Thousands separators "1,234.50" and a leading "₱"/"PHP" are accepted.

parse_* raise InputError(field, message); the application turns that into a message on the
form and changes nothing (see legacy_app's InputError handler).
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")
MAX_AMOUNT = Decimal("99999999.99")      # fits DECIMAL(12,2) with headroom for sums
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


class InputError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def round_money(value):
    """Round any Decimal-convertible value to centavos (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def parse_decimal(raw, *, label, places, minimum=None, maximum=None, allow_empty=False, default=None):
    text = "" if raw is None else str(raw).strip()
    text = re.sub(r"^(₱|PHP|Php|php)\s*", "", text).replace(",", "").replace(" ", "")
    if text == "":
        if allow_empty:
            return default
        raise InputError(label, f"{label} is required.")
    if not _NUMBER.match(text):
        raise InputError(label, f"{label} must be a number, for example 1234.50.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise InputError(label, f"{label} must be a number, for example 1234.50.") from None
    if not value.is_finite():
        raise InputError(label, f"{label} must be a number, for example 1234.50.")
    if -value.as_tuple().exponent > places:
        raise InputError(label, f"{label} can have at most {places} decimal place{'s' if places != 1 else ''}.")
    if minimum is not None and value < minimum:
        raise InputError(label, f"{label} can't be less than {minimum:,}.")
    if maximum is not None and value > maximum:
        raise InputError(label, f"{label} can't be more than {maximum:,}.")
    return value.quantize(Decimal(1).scaleb(-places))


def parse_amount(raw, *, label="Amount", allow_zero=False, allow_negative=False, allow_empty=False, maximum=MAX_AMOUNT):
    """A peso amount: at most 2 decimals, positive unless allowed, at most `maximum`."""
    minimum = -maximum if allow_negative else Decimal("0")
    value = parse_decimal(raw, label=label, places=2, minimum=minimum, maximum=maximum,
                          allow_empty=allow_empty, default=Decimal("0.00") if allow_empty else None)
    if value == 0 and not allow_zero and not (allow_empty and (raw is None or str(raw).strip() == "")):
        raise InputError(label, f"{label} must be greater than zero.")
    return value
