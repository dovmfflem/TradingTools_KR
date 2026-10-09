"""Exchange-independent order evidence. Quantities remain in native instrument units."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class OrderState(str, Enum):
    UNKNOWN = "UNKNOWN"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class Acceptance(str, Enum):
    UNKNOWN = "UNKNOWN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class OrderAck:
    exchange: str
    acceptance: Acceptance
    order_id: str | None
    client_order_id: str | None


@dataclass(frozen=True)
class OrderSnapshot:
    exchange: str
    order_id: str | None
    client_order_id: str | None
    state: OrderState
    raw_state: str
    filled_quantity: Decimal | None
    remaining_quantity: Decimal | None
    average_fill_price: Decimal | None
    issues: tuple[str, ...] = ()
    market: str | None = None
    side: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED}

    @property
    def acknowledgement(self) -> OrderAck:
        acceptance = Acceptance.UNKNOWN
        if self.state == OrderState.REJECTED:
            acceptance = Acceptance.REJECTED
        elif self.order_id:
            acceptance = Acceptance.ACCEPTED
        return OrderAck(self.exchange, acceptance, self.order_id, self.client_order_id)


def identifier(value: Any) -> str | None:
    # Do not round large order IDs through float or treat booleans as identifiers.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip() or None


def decimal_field(value: Any, name: str, issues: list[str]) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or len(number.as_tuple().digits) > 40 or abs(number.as_tuple().exponent) > 30:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        issues.append(f"INVALID_{name.upper()}")
        return None
