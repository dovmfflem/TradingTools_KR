# KIS domestic dollar futures: grid integration contract

Implemented 2026-10-01. This is a TradingTools library extension, not activation of a SuperGrid adapter.

Application integration was subsequently added in `pyqt_launcher/super_gridbot_kis.py`
and `grid_adapter.py`; see the parent repository's `docs/super-gridbot-kis.md` for
session policy and the remaining live-verification scope. The grid client supplies
`before_request` to rate-limit every HTTP request, including pagination and approval.

## REST

Existing `future_order(symbol, side, night=False, quantity=1)` remains market execution.
Pass `price="1350.5"` to place a limit order; use decimal strings. Zero, negative and nonfinite limit prices are rejected locally.
`future_capacity(..., price=...)` queries capacity for the same limit price.
`future_cancel(order_id, night=False, quantity=None)` cancels all remaining quantity by default; a positive integer requests a partial cancellation.
`future_modify(order_id, price, night=False, quantity=None)` modifies a limit order. Returned order ID belongs to the amendment/cancellation request; retain its link to the original order ID.

| Operation | Path suffix under `/uapi/domestic-futureoption/v1/` | Day | KRX night |
|---|---|---|---|
| Order | trading/order | TTTO1101U | STTN1101U |
| Modify/cancel | trading/order-rvsecncl | TTTO1103U | TTTN1103U |
| History | trading/inquire-ccnl / inquire-ngt-ccnl | TTTO5201R | STTN5201R |

Order acknowledgements are not fills; cancel acknowledgements are not terminal cancellation. Reconcile the original and amendment IDs using existing `future_orders` and account snapshots. History queries remain bounded and fail on incomplete pagination. Persist trading date + session + account + order ID, not order ID alone.
Transport failures after a mutation have `outcome_unknown=True`. Never blindly retransmit. Explicit API rejection preserves the sanitized `msg_cd` and is not reported as an uncertain accepted order. HTTP connect/read deadlines remain 3/5 seconds; no automatic HTTP retry.

## WebSocket

```python
from src.exchanges.kis.futures_websocket import KisFuturesWebSocket
stream = KisFuturesWebSocket(rest, [contract_symbol], hts_id=hts_id, night=False)
stream.connect()
# Call receive() from the existing account owner loop. It returns a list.
# stream.ready becomes True only after all subscription acknowledgements.
# finally: stream.close()
```

Day commodity quotes: H0CFASP0; day account notices: H0IFCNI0.
KRX night futures quotes: H0MFASP0; night account notices: H0MFCNI0.
Scope: real domestic commodity/dollar futures; no claim of overseas/options or paper-trading support.

Approval is issued by `get_ws_approval()` via `/oauth2/Approval`. HTS ID is required in addition to API credentials and account. Current app credentials UI still needs HTS-ID persistence before application integration.

The stream implements AES-256-CBC/PKCS7 notice decryption, PINGPONG pong responses, count-aware multi-record decoding, account/product filtering, duplicate-subscription suppression, and cipher/ACK reset on reconnection. No secret or raw account packet is logged.

`order_notice.eventQuantity` is an event quantity, NOT cumulative executed volume. `orderKind`, `executionFlag`, `acceptanceFlag`, `amendCancelCode` and `rejected` preserve exchange semantics; acceptance/amend/cancel packets must never count as fills. `reportedQuantity` has different semantics depending on notice kind. The application must reconcile/deduplicate before determining terminal execution; `requiresReconciliation=True` makes this explicit. Quotes with absent one-sided prices have `tradable=False`.

Each instance has one socket and no background thread or implicit infinite retry loop. Connect timeout: 5s; subscription sending budget: 20s; ACK deadline: 20s from socket connection; each receive: at most 5s; silent connection deadline: 90s; close handshake: 1s. Failed connections close and expose RECONNECT_WAIT plus `retry_at`, with 10/20/40/80/120s backoff. Only 60s of acknowledged operation resets the failure count. The application owns an absolute recovery deadline/attempt budget, cancellation, shared account ownership and status/alert policy; do not create per-bot sockets for a shared account. A reconnect calls `connect()` on the same object after `retry_at` and restores subscriptions. It does not replay orders.

For day/night transition, close the previous session stream and create one with the next `night` value. Trading-calendar/session selection is application business logic, not a hardcoded clock switch in the API library. Reconcile orders/positions before resuming. No live orders were used to verify this change.

## Verification

Offline protocol tests: `tests/test_kis_grid.py`; existing token/market-order and application spread/credential tests also run. Results: ignored `tests/results/kis/grid/`. Synthetic credentials only.

## Primary references

- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_futureoption/order/order.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_futureoption/order_rvsecncl/order_rvsecncl.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_futureoption/domestic_futureoption_functions_ws.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py

The portal URL was attempted but timed out; the official repository supplied protocol evidence. API availability by contract/session still requires authenticated integration verification during the appropriate market session.
