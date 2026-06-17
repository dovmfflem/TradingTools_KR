# Bithumb Full API Surface Design

## Goal

Bring the Bithumb adapter to the same API-surface completeness standard used for Upbit. The implementation should follow the official Bithumb `v2.1.5` API reference and expose every documented Bithumb REST/WebSocket API that is currently missing from the local client surface.

The user will run live tests. This work should focus on implementation completeness, parameter shape, surface documentation, and non-live unit/import tests.

## Current State

`src/exchanges/bithumb/bithumb_rest.py` already implements most public quotation, account, order, deposit, withdrawal, service, and fee endpoints. `src/exchanges/bithumb/api_surface.py` currently marks TWAP order APIs as missing. Bithumb public WebSocket support is focused on orderbook collection, and private WebSocket support currently exposes MyOrder only.

The Bithumb docs index lists these missing areas:

- TWAP REST: order request, cancel, order history
- Public WebSocket: ticker, trade, candle, plus existing orderbook
- Private WebSocket: MyAsset, plus existing MyOrder

## Recommended Approach

Implement the full missing surface now:

- Add TWAP REST methods to `BithumbRest`
- Extend Bithumb WebSocket support with generic public subscription helpers and named ticker/trade/candle methods
- Add private MyAsset subscription support to `BithumbMyOrder` or, if cleaner, rename/generalize the private class surface while keeping backward compatibility
- Update `api_surface.py` so `missing` reflects only genuinely unsupported or intentionally excluded items
- Add unit tests that verify declared methods exist and basic parameter validation works without live calls
- Update checklists/docs where needed

This is preferable to a REST-only pass because the project goal is an exchange API collection, and the current surface file is meant to work like a header file showing what exists.

## REST Design

Add these methods to `BithumbRest`:

- `create_twap_order(...)`
  - Endpoint: `POST /v1/twap`
  - Purpose: create TWAP order
  - Required fields: `market`, `side`, `duration`, `frequency`
  - Conditional fields: `price` for bid, `volume` for ask
  - Allowed `side`: `bid`, `ask`
  - Allowed `frequency`: `15`, `20`, `30`, `60`, `120`
  - `duration` is seconds, min 300 and max 43200
- `cancel_twap_order(algo_order_id)`
  - Endpoint: `DELETE /v1/twap`
  - Required query field: `algo_order_id`
- `list_twap_orders(...)`
  - Endpoint: `GET /v1/twap`
  - Query fields: `market`, `uuids`, `state`, `next_key`, `limit`, `order_by`
  - Allowed `state`: `progress`, `done`, `cancel`
  - Allowed `order_by`: `asc`, `desc`
  - `limit` max 100

Parameter names should stay close to the official API body/query names, while Python arguments use snake_case and convert to documented request fields internally.

## WebSocket Design

Public WebSocket should expose:

- `subscribe_orderbook(tickers, ticket=None)`
- `subscribe_ticker(tickers, ticket=None)`
- `subscribe_trade(tickers, ticket=None)`
- `subscribe_candle(tickers, interval="1m", ticket=None)`
- Generic helpers: `connect()`, `send_request(request_types)`, `recv_once()`, `start_listen(on_message=None)`, `close()`

Private WebSocket should expose:

- Existing `subscribe_my_order(tickers=None, ticket=None)`
- New `subscribe_my_asset(ticket=None)`
- Same generic connection/read/listen helpers where practical

Keep existing `BithumbDataBank` and `BithumbMyOrder` imports backward compatible. If new generic classes are introduced, re-export them in `src/exchanges/bithumb/__init__.py`.

## API Surface

Update `src/exchanges/bithumb/api_surface.py`:

- Move TWAP methods from `missing` to `implemented`
- Add exact doc URLs for each TWAP method
- Add public WebSocket ticker/trade/candle methods as implemented
- Add private WebSocket MyAsset as implemented
- Leave `missing` empty unless docs reveal a Bithumb endpoint we intentionally do not support

## Tests

Add or update non-live tests:

- `tests/test_bithumb_api_surface.py`
  - Assert new REST methods exist
  - Assert public WebSocket subscribe methods exist
  - Assert private MyAsset subscribe method exists
  - Assert surface `missing` is empty or only contains explicitly justified items
- Optional unit tests for request-body builders if TWAP parameter mapping becomes non-trivial

Do not run live Bithumb TWAP or WebSocket tests in CI.

## Safety

TWAP create/cancel methods are real trading APIs. They should be implemented but not added to automatic live test scripts by default. Any live test checklist should mark TWAP create/cancel as manual or separately approved.

No real credentials, account IDs, transaction IDs, addresses, or live result artifacts should be committed.

## Open Implementation Detail

The TWAP endpoint paths and main request fields have been verified against the Bithumb `v2.1.5` endpoint pages. During implementation, keep any extra undocumented optional fields out of the default method signature unless the docs page explicitly lists them.
