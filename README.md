# TradingTools KR

Python helpers for Korean and global exchange trading APIs, orderbook data collection, private order streams, and Telegram notifications.

This public version intentionally excludes strategy bots, arbitrage engines, GUI tools, local logs, and trading databases.

## Included Modules

- Bithumb: REST orders/account, public orderbook feed, private order stream
- Upbit: REST orders/account, public orderbook feed, private order stream
- Coinone: REST orders/account, public orderbook feed, private order stream
- Binance Futures: REST orders/account, public order stream, public spot/futures orderbook feeds
- Bybit Spot: REST orders/account, public spot/futures orderbook feeds
- Bitget Spot: REST orders/account, public spot/futures orderbook feeds
- Lighter: REST order helper and public orderbook feed
- Telegram: reusable notification sender with simple rate limiting and retry handling

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy the template and fill in only the credentials you need:

```powershell
Copy-Item info_template.yaml info.yaml
```

`info.yaml` is ignored by Git. Do not commit real API keys, private keys, or Telegram bot tokens.

## Examples

```python
from src.exchanges.bithumb.bithumb_rest import BithumbRest

client = BithumbRest.from_info_yaml("info.yaml")
orderbook = client.get_orderbook("xrp-krw")
print(orderbook)
```

```python
from src.notifications.telegram_messenger import TelegramMessenger

messenger = TelegramMessenger.from_info_yaml_key("default", "info.yaml")
messenger.send_message("TradingTools KR notification test")
```

## Repository Hygiene

The repository should contain source code, lightweight tests, and documentation only. Runtime artifacts such as `data/`, `logs/`, `*.db`, `*.sqlite3`, `*.csv`, `*.jsonl`, `.venv/`, and `info.yaml` are ignored.
