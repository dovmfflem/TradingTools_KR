# Global Arbitrage Spot Compatibility Contract

Implemented 2026-10-22. Synthetic verification only; no live orders, private
requests, credentials, commit, build, or deployment. Application wiring remains
outside this library change.

## Python REST

```python
from src.exchanges.arbitrage_spot import wrap_spot, SPOT_CAPABILITIES

spot = wrap_spot(exchange, existing_rest_client)
```

`exchange` accepts case-insensitive `upbit` or `bithumb`, with outer whitespace
removed. Other exchanges fail before I/O. Upbit returns the exact original
instance, preserving pocket/cashflow and all other existing behavior. Bithumb
returns `ArbitrageSpotAdapter`. Direct construction of that adapter supports both
exchanges when a deliberately bounded surface is wanted.

The wrapper exposes these methods and properties, without `__getattr__` or raw
endpoint forwarding:

```python
get_accounts()
get_orderbook_parse(ticker, count=5)
get_orderbooks(tickers)
list_trading_pairs(*, is_details=False)
get_order(uuid_value=None, *, identifier=None, uuid=None)
place_order(*, ticker, side, order_type, price=None, volume=None,
            identifier=None, time_in_force=None)
cancel_order(uuid_value=None, *, identifier=None, uuid=None)
list_orders(**native_filters)
list_open_orders(**native_filters)
list_closed_orders(**native_filters)
list_wallet_statuses()
get_wallet_status()  # alias of list_wallet_statuses
is_order_missing(error)  # existing spot_trading classification
close()
api_url  # inner origin, preserves exchange-specific valuation cache keys
timeout_seconds  # getter and setter forward to the REST client
last_response_metadata  # existing safe rate-limit metadata
capabilities  # immutable SpotCapabilities
```

- Bithumb tickers accept canonical `KRW-X`, `BTC-X`, `USDT-X` and base-quote forms.
  `KRW-BTC` and `BTC-KRW` both encode the KRW BTC market. `BTC-CHR` becomes
  `chr-btc` internally, encoding `BTC-CHR` on the wire. For ambiguous cross-quote
  pairs, uppercase means canonical (`BTC-USDT`); lowercase means base-quote
  (`btc-usdt` encodes `USDT-BTC`), matching Upbit's existing convention.
- Batch REST books keep the existing 1..100-market bound. They do not split,
  paginate, or retry. The new Node subscription helper has a separate 600-code
  bound; that is not a REST batch limit.
- `side` uses `bid`/`ask`. Bithumb `identifier` maps to `client_order_id` for
  submit, lookup and cancel. Both supplied IDs keep the native server-ID
  precedence. Conflicting `uuid`/`uuid_value` aliases fail locally.
- Bithumb response dictionaries retain every original field. Missing aliases
  are added: `order_id -> uuid`, `client_order_id -> identifier`, and
  `order_type -> ord_type`. Existing aliases are not overwritten. No state,
  executed amount, price, quantity or caller-supplied identifier is fabricated.
  Create/cancel acknowledgements still require subsequent reconciliation.
- Bithumb `list_orders` accepts native filters (`ticker`, `state`, `states`,
  `uuids`, `client_order_ids`, `side`, `limit`, `page`, `order_by`) plus the alias
  `identifiers`. Supplying both identifier spellings fails. `ticker` alone is
  supported. Open reads default to `state=wait`; closed reads default to
  `states=[done,cancel]`, using existing `/v1/orders`. Explicit state filters
  override those defaults. No time-window translation or new endpoint exists.
- The explicit Upbit wrapper keeps native ID-list semantics for `list_orders`
  and native `/v1/orders/open` and `/v1/orders/closed` methods. `wrap_spot` leaves
  Upbit entirely unchanged.
- Exchange errors propagate unchanged, including actual exchange, status,
  rate limits and outcome uncertainty. Neither adapter nor factory retries.
- `close()` calls the client's explicit close method when present, otherwise
  its existing requests session's close. No withdrawal, bulk mutation, pocket,
  or private-stream methods are forwarded by the compatibility wrapper.

`SPOT_CAPABILITIES[exchange]` is available even when Upbit is returned raw.
Its frozen fields are `fok`, `ioc`, `post_only`, `identifiers`, `wallets`,
`batch_orderbooks` (all true for these two current clients),
`time_in_force_quotes` (`("KRW",)` for Bithumb, `None` for Upbit), and
`identifier_max_length` (36 Bithumb, 64 Upbit). These describe protocol support,
not account permissions, market availability, or execution guarantees.

## Bithumb REST Validation

`BithumbRest.place_order` now accepts `time_in_force` and `best`:

- `limit`: optional `ioc`, `fok`, or `post_only`; price and volume required.
- `best`: required `ioc` or `fok`; bid requires price, ask requires volume.
- `price` (market bid) and `market` (market ask): no TIF allowed.
- TIF/best is KRW-only. Unsupported values/combinations fail before transport.
- Optional client IDs must match `[A-Za-z0-9_-]{1,36}` exactly. Validation also
  applies to lookup/cancel client IDs and supplied order-list client IDs.
- The existing signed request encoder, timeout, rate-limit errors, redirect
  rejection and retrying-mutation-transport rejection remain in control.

## Python Runtime Depth Decoder

```python
from src.exchanges.stream_transport import spot_depth_quote

quote = spot_depth_quote(exchange, message, depth=5)
# {"symbol": "XRP", "asks": [{"price": 3001.0, "size": 1.0}],
#  "bids": [{"price": 3000.0, "size": 2.0}], "sourceTimestampMs": 1725930007672}
```

Supports Upbit/Bithumb DEFAULT and SIMPLE decoded dictionaries for KRW markets.
Depth must be an integer from 1 through 30. Asks sort ascending, bids descending;
zero-size rows are omitted. Control/unrelated frames return `None`. Invalid,
empty or crossed books raise; subscription error responses retain the exchange.
Source timestamps convert microseconds/milliseconds (or seconds) to exact integer
milliseconds; missing/zero timestamps stay zero. No receive timestamp is invented
by this pure decoder. The application records arrival time and owns reconnects.

For the runtime's native Bithumb protocol, use:

```python
from src.exchanges.bithumb.bithumb_websocket import BithumbPublicWebSocket

ws = BithumbPublicWebSocket(timeout_seconds=1.0)
ws.connect()
ws.subscribe_orderbook(["xrp-krw"], format_type="DEFAULT")
# Current endpoint: wss://ws-api.bithumb.com/websocket/v1
# Native subscribe_orderbook takes BASE-QUOTE, encoding KRW-XRP on the wire.
quote = spot_depth_quote("bithumb", ws.recv_once())
# Caller must close ws and own the bounded receive/reconnect policy.
```

The earlier requested convenience API is also available:
`arbitrage_spot.open_spot_depth_stream(exchange, symbols, *, timeout_seconds=1.0)`.
It supports Bithumb only, 1..600 base symbols/canonical KRW codes, current public
transport with a 10-second connect deadline, and bounded send/receive/close.
Its `recv()` returns `None` for control/unrelated frames or receive timeout;
otherwise `{ticker, market, exchange, source, asks, bids, sourceTimestampMs,
receivedAtMs, updated_at}`. Here levels use `price`/`qty`, `ticker` is uppercase
base, and `updated_at` is local arrival time in seconds. It reuses the same pure
decoder. Closed connections raise; `close()` is idempotent. No listener thread,
sleep, polling loop, reconnect or REST fallback is started.

## Node Depth Helpers

Exports from `node/public-streams.mjs`:

```javascript
spotDepthSubscription(exchange, canonicalKrwCodes)
spotDepthQuote(exchange, decodedPayload) // {symbol, bid, ask} | null
```

Both support exact lowercase exchange names `upbit` and `bithumb` only.
Subscriptions accept 1..600 canonical uppercase `KRW-X` codes, deduplicate them,
add `.5` only for Upbit, and use `DEFAULT` for both. Input arrays stay unchanged.
The existing `upbitDepthSubscription` and 100-symbol `orderbookSubscriptions`
contracts are unchanged. Quote decoding accepts DEFAULT/SIMPLE top-of-book
fields; finite positive noncrossed bid/ask are required. Unsupported exchanges
throw; control/malformed/non-KRW payloads return null. Quotes contain uppercase
base symbols and numeric prices. No socket, heartbeat or reconnect is created.

## Evidence and References

Run `python -B tests/run_arbitrage_spot_contract.py` from the library root for the
focused Python/Node suite, with a 120-second deadline per child and no retries.
The separately requested parent valuation suite uses `--parent-positions`.
Results, synthetic request/response samples and failure diagnostics are saved
under `tests/results/arbitrage-spot/`. Python socket connects are blocked during
tests. No real credentials, Authorization headers or JWTs are saved.

Official documentation reviewed (response schemas read from public `.md` pages):

- [Bithumb order creation](https://apidocs.bithumb.com/reference/주문-요청)
- [Bithumb lookup](https://apidocs.bithumb.com/reference/개별-주문-조회)
- [Bithumb cancel](https://apidocs.bithumb.com/reference/주문-취소-접수)
- [Bithumb list orders](https://apidocs.bithumb.com/reference/주문-리스트-조회)
- [Bithumb public orderbook](https://apidocs.bithumb.com/reference/호가-orderbook)
- [Upbit order creation](https://docs.upbit.com/kr/reference/new-order)
- [Upbit public orderbook](https://docs.upbit.com/kr/reference/websocket-orderbook)
