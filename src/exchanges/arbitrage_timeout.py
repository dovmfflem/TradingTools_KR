"""Validate finite requests timeouts without dropping urllib3 total budgets."""
import math
from urllib3.util import Timeout


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("FINITE_POSITIVE_TIMEOUT_REQUIRED")
    return value


def bounded_timeout(value):
    if isinstance(value, Timeout):
        clone = value.clone()
        if clone.total is not None:
            _positive(clone.total)
            _positive(clone.connect_timeout)
            # A total-only Timeout is finite even without explicit read cap.
            return clone
        _positive(clone.connect_timeout)
        _positive(clone.read_timeout)
        return clone
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("TIMEOUT_REQUIRES_CONNECT_READ_PAIR")
        return tuple(_positive(v) for v in value)
    return _positive(value)


def after_signing(timeout, elapsed):
    """Charge DigitalX server-time synchronization to a supplied total budget."""
    if not isinstance(timeout, Timeout) or timeout.total is None:
        return timeout
    remaining = timeout.total - max(0, elapsed)
    if remaining <= 0:
        raise TimeoutError("REQUEST_BUDGET_EXHAUSTED_BEFORE_SEND")
    # Reading a clone after start_connect resolves total-only read timeouts.
    probe = timeout.clone()
    probe.start_connect()
    return Timeout(total=remaining, connect=min(timeout.connect_timeout, remaining),
                   read=min(probe.read_timeout, remaining))
