"""A bounded MLS watch band above the user's asking-price ceiling."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

BUFFER_PERCENT = 10


def watch_ceiling(max_price):
    try:
        value = Decimal(str(max_price))
        if not value.is_finite() or value <= 0:
            raise ValueError('Invalid asking-price ceiling')
        return int((value * Decimal('1.10')).to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Invalid asking-price ceiling') from exc


def classify_price(price, max_price):
    try:
        value = Decimal(str(price))
        if not value.is_finite() or value <= 0:
            return 'price_unknown'
        maximum = Decimal(str(max_price))
        if value <= maximum:
            return 'within_budget'
        return 'background_watch' if value <= watch_ceiling(max_price) else 'historical_outside_watch'
    except (InvalidOperation, TypeError, ValueError):
        return 'price_unknown'
