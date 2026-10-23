# Coinone / DigitalX Adapter Verification Contract

Checked 2026-10-22/23 against official sources, using synthetic HTTP and WebSocket
fixtures only. No credentials, live API, orders, withdrawals, build or release.

## Source Comparison

| Source | Wire behavior | Adapter contract |
| --- | --- | --- |
| [Coinone placement](https://docs.coinone.co.kr/reference/place-order) | LIMIT/MARKET/STOP_LIMIT; post_only; user_order_id; market limit_price; no documented FOK/IOC | Native limit or price-limited market only. Reject FOK/IOC/best/STOP_LIMIT. Require identifier. |
| [Coinone order detail](https://docs.coinone.co.kr/reference/order-detail) | executed_qty is cumulative; market buy volume may be absent; special partial-fill cancellation states | Exact executed_volume; explicit terminal; no requested-volume fallback. Unknown states raise. |
| [Coinone tickers](https://docs.coinone.co.kr/reference/tickers) | best_bids/best_asks, ticker timestamp | One all-ticker GET, filter requested markets. Never last price. |
| [Coinone orderbook](https://docs.coinone.co.kr/reference/orderbook) | Supported size 5/10/15/16 | Positive requested depth is a maximum; cap native request at16. |
| [Coinone public socket](https://docs.coinone.co.kr/reference/public-websocket-1) | Per-topic subscription, JSON PING/PONG | One connection, recv-driven heartbeat, bounded PONG deadline. |
| [DigitalX specification](https://docs.digitalx.miraeasset.com/llms-full.txt) | timeInForce gtc/ioc/fok/po; bestNth for best; clientOrderId | Native fields through existing KorbitRest.request, no retries or emulation. |
| DigitalX orders/tickers in same specification | filled can be partial; order detail lacks individual trades/fees; ticker lastTradedAt is not book timestamp | Use filledQty/filledAmt, not requested qty. Unknown fee stays None. No ticker timestamp synthesized. |
| [Coinone fills](https://docs.coinone.co.kr/reference/find-completed-orders) | completed_orders: trade_id/order_id/price/qty/timestamp/fee; size<=100; to_trade_id cursor; 90-day maximum query span | Separate bounded evidence read; strict order filter and sum checks. |
| DigitalX myTrades in same specification | tradeId/orderId/price/qty/amt/tradedAt/optional fees; last36h only; max1000; newest-first; exclusive endTime; possible seconds of lag | Overlap boundary millisecond and deduplicate; saturation/incomplete history is not proof. |
| [Coinone balances](https://docs.coinone.co.kr/reference/find-balance), DigitalX balance in same specification | Coinone average_price; DigitalX avgPrice (not avgBuyPrice) | Preserve as avg_buy_price; never manufacture a missing average. |

## Public Entry Points

`wrap_spot('coinone' | 'korbit' | 'digitalx', raw_client)` preserves repository ID
`korbit` for DigitalX. Existing Upbit identity and Bithumb behavior remain intact.

- `get_accounts()`: uppercase currency, exact available `balance` and `locked`.
  Preserves official `average_price` / `avgPrice` as `avg_buy_price` when present;
  missing stays absent, invalid values fail closed. One native balance read.
- `list_trading_pairs(is_details=False)`: `market`, `trading_available`; optional
  `native` details, without invented names or warning classifications.
- `get_best_quotes(tickers)`: 1..599 base symbols or KRW market codes; one public
  all-ticker GET. Missing/empty quotes omitted; malformed requested quotes raise.
  Returns `market`, `orderbook_units:[{bid_price,ask_price}]`; only Coinone has a
  native ticker snapshot `timestamp`. No sizes are invented.
- `get_orderbook_parse(ticker,count=5)`: bids/asks with numeric price/qty. Positive
  integer count is maximum desired depth, not a native wire size. Runner count20
  is accepted: Coinone caps at16; DigitalX has no size parameter and returns at
  most count actual levels. Thin/unequal books are never padded or duplicated.
- `get_orderbooks(tickers)`: 1..10 explicitly bounded sequential reads, NOT a
  native batch. Do not use this for full-universe scanning or valuation.
- `get_trading_policy(ticker)`: existing `spot_trading.Policy` with DigitalX
  currencyPairs status/value bounds overlaid. Includes fee reads. DigitalX
  quantity step remains the pre-existing library default, not newly verified
  per-market precision. Caller chooses rounding; `Policy.validate` rejects bad
  steps/minimums. No automatic order adjustment or POST.

## Order Contract

Coinone example (SDK contract only, do not run against a live account in tests):

```python
ack = spot.place_order(ticker="KRW-XRP", side="bid", order_type="price",
                       price="6000", limit_price="3000", identifier="intent-1")
detail = spot.get_order(ack["uuid"], ticker="KRW-XRP")
```

Buy `price` is quote amount; sell uses `order_type='market'`, `volume` and
`limit_price`. `time_in_force` must be omitted on Coinone market orders.
Coinone identifier: lowercase letters/digits/underscore/dot/hyphen, max150.
DigitalX identifier: letters/digits/dot/colon/underscore/hyphen, max36.

`get_order` and `cancel_order` accept exactly one uuid or identifier. They require
`ticker` after restart or cache eviction. Submitted intent IDs are remembered
before the request for ambiguous-outcome lookup; this bounded in-memory cache
is NOT persistence or a retry authorization. Errors retain native metadata.

Placement/cancellation acknowledgements do not assert terminal or fill state.
Detail returns `state`, `native_state`, `terminal`, `executed_volume`,
`executed_funds`, `volume` (nullable), `remaining_volume` (nullable),
`average_executed_price`, `paid_fee` (nullable), and `trades_complete=False`.
This path remains one order-detail request, with no fill-history request.
No individual trade IDs/timestamps are fabricated. Coinone amount derived from
average price is flagged `executed_funds_estimated=True`; reported traded_amount
and DigitalX filledAmt are not estimated. Reported fill quantity is not adjusted
for possible base-denominated fees. Hedge/inventory policy belongs to the app.

### Separate Fill Evidence

`get_order_fill_evidence(order_id, *, ticker, order, max_pages=4)` accepts an
already-confirmed normalized terminal order from `get_order`. It validates the
exact UUID, market and side, never re-queries the order, and returns a copied
order without mutating the input. Only invoke this during reconciliation, not
before the actual-quantity hedge. `individual_fills=True` advertises this method,
not an unconditional guarantee of retained/completely available history.

The helper reuses raw `list_completed_orders` / `get_my_trades`. Default4 pages,
allowed1..8; page size100 /1000. Shared I/O budget at most5s, shortened by a
configured urllib3 total timeout or connect+read budget. Each request receives
the remaining total/connect/read limits on a shallow transport-sharing client;
the caller's client timeout is not changed. No retry, wait or mutation calls.
Socket timeout semantics still require an outer absolute job deadline.

Complete evidence returns `trades_complete=True` and `trades` with real `uuid`,
`order_id`, `market`, `side`, exact `volume`, `price`, `funds`, native millisecond
`timestamp`, UTC ISO `created_at`, and optional native `fee`/`fee_currency`.
It deduplicates trade IDs, rejects conflicting duplicates, validates positive
price/quantity and in-range millisecond time, and requires exact quantity sum
equality with the terminal snapshot. Non-estimated amount must also match.
Coinone funds use actual per-fill price*quantity, not average-price synthesis.
Complete fills replace estimated aggregate funds/average; all known KRW fill
fees are summed as paid_fee (existing reported fee must agree). Missing or
non-KRW fees are not converted to zero/KRW; fee coverage is separate
`fees_complete`. Confirmed zero fills return an empty list without HTTP.

Incomplete history returns no `trades`, `trades_complete=False`,
`fill_evidence_pages` and `fill_evidence_error`: `FILL_HISTORY_INCOMPLETE`,
`FILL_PAGE_LIMIT`, `FILL_CURSOR_NOT_PROGRESSING` or
`FILL_TIMESTAMP_PAGE_SATURATED`. Do not credit such an order. DigitalX retains
only36h and can lag; a saturated millisecond cannot be fully paged through this
API. Coinone reads one bounded recent90-day window, not unlimited history.
There is no fabricated aggregate fill or fallback to order/update/receive time.
Invalid identities/times/values/totals raise; transport/auth/rate errors retain
the existing typed error; exhausted shared deadline raises TimeoutError.

`is_order_missing` delegates exact library error classification; missing does
NOT prove no acceptance/fills, especially after ambiguous submission or beyond
the venue's lookup retention. `list_orders` supports 1..10 explicit IDs only.
Closed-order history scans remain unsupported and raise. For maintenance health
checks use `list_order_history_probe(ticker,limit=1)`: Coinone returns native fill
rows (not closed orders), DigitalX returns recent native orders. Neither is
absence/terminal evidence. Coinone supplies explicit 24h from/to bounds.

`list_open_orders(limit=1)` uses current Coinone active_orders with omitted
currencies for all markets; it does not call deprecated/undocumented aliases.
DigitalX requires `ticker` for openOrders. Both validate actual list responses;
bad responses do not become empty lists. Response limits are explicit 1..1000.

`list_wallet_statuses()` reads public currency availability. Coinone yields
asset-level states without inventing net_type. DigitalX uses each networkList
route (fiat uses fiat status). Unknown states/missing crypto routes raise.
`wallets=False` still means no withdrawal/deposit operations on this adapter;
`wallet_statuses=True` advertises the read-only method.

Both wrappers accept positive scalar, connect/read tuple, or bounded
`urllib3.util.Timeout`. DigitalX now honors that value in its existing transport
and charges signing/server-time synchronization against a supplied total before
the signed request; expiration before send is not an unknown submitted order.
Default timeout remains (3,3). Requests/urllib3 socket budgets do not replace the
caller's absolute end-to-end job deadline.

## Depth Stream

`open_spot_depth_stream(exchange, symbols, timeout_seconds=1.0)` accepts Coinone,
Korbit and DigitalX alias. All retain the local 600-input bound; this is not a
claimed official exchange limit. 104 markets plus reference-market workflows
are tested. It returns a stream with `recv()` / idempotent `close()`.

`recv()` returns one snapshot with `ticker` (uppercase base), `market`, canonical
`exchange`, `source='orderbook'`, `asks`/`bids` (up to15 price/qty levels),
`sourceTimestampMs`, `receivedAtMs`, `updated_at`. Missing native book time stays
zero. DigitalX outer send timestamp is not substituted for data.timestamp.
Timeout/control/unrelated frames return None. Bad books/errors/closed sockets
raise. No background threads, sleeps, retries or REST fallback.

Connection deadline10s, per-I/O timeout configurable. Coinone PING every20s while
recv is called, PONG deadline15s; DigitalX has no invented JSON heartbeat.
Application owns first-book/stale deadlines and reconnection.

## Reproducible Focused Verification

DigitalX historical wallet valuation uses the documented public `GET /v2/candles`
through `KorbitRest.get_candles(symbol, *, interval="1", start=None, end=None,
limit=200)`. Native intervals are 1/5/15/30/60/240/1D/1W; limit1..200; boundaries
are epoch milliseconds and end must exceed start. No signing/time-sync is used.
The earlier blanket historical-price unsupported guard was an implementation
gap, not an exchange API limitation, and has been removed.

`GlobalSpotWallet.historical_krw_price` retains the Coinone convention: exact
event-minute close, not a trade price at the event's second. DigitalX requests
that minute with interval1/start/end/limit2; only exactly one matching candle's
positive finite close is accepted. Timezone offsets normalize to UTC. A minute
that has not closed, empty/gapped history, duplicate matching rows, malformed
times or invalid prices fail closed. Never use nearest/latest price or a synthetic
candle. One public call per lookup, existing wallet deadline checked before and
after I/O; rate-limit/timeout errors propagate without retry. KRW remains1.
The docs do not promise availability for every historical minute; actual absent
history still returns unavailable rather than guessed valuation.

Run `python -B tests/run_arbitrage_native_contract.py` from the library root.
Each run is capped at120s and records source hashes, HEAD, outputs and status in
`tests/results/arbitrage-native-spot/contract-*.json`; sanitized fake HTTP and
WebSocket evidence is saved beside it. This is not a release/full-suite run and
does not claim real exchange or Ubuntu runtime verification.
