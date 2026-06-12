# Coinone WebSocket Notes

## What This Module Does

- `coinone_websocket.py` subscribes to Coinone ORDERBOOK streams and stores normalized top levels in-memory.
- Stored shape per ticker key:
  - `asks`: list of `{price, qty}` sorted ascending by price
  - `bids`: list of `{price, qty}` sorted descending by price
  - `updated_at`, `asks_updated_at`, `bids_updated_at`

## Main Flow

1. Subscribe with `request_type=SUBSCRIBE`, `channel=ORDERBOOK`, and topic `{quote_currency, target_currency}`.
2. Parse incoming `DATA/ORDERBOOK` payload.
3. Normalize each level (`price|p`, `qty|q`) into floats.
4. Sort and keep top `count` levels.
5. Update in-memory book and emit `SOURCE_ORDERBOOK` event.

## Quick Usage

Use `CoinoneDataBank` to collect live orderbook snapshots in memory.

```python
import time

from src.exchanges.coinone.coinone_websocket import CoinoneDataBank


bank = CoinoneDataBank(["usdt-krw"], count=5, auto_start=True)
time.sleep(3)
print(bank.get_orderbook("usdt-krw"))
bank.stop()
```
