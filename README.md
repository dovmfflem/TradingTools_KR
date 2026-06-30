# TradingTools KR

[![CI](https://github.com/dovmfflem/TradingTools_KR/actions/workflows/ci.yml/badge.svg)](https://github.com/dovmfflem/TradingTools_KR/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Runtime deps: 3](https://img.shields.io/badge/Runtime%20deps-3-brightgreen.svg)](requirements.txt)

Language: **English** | [Korean](README_ko.md)

TradingTools KR is a collection of exchange API clients for trading, account access, market data, orderbook collection, private order streams, and Telegram notifications.

The project keeps exchange API surfaces documented in code so implementations can be reviewed and updated as each exchange changes its official API pages.

## Included Modules

- Upbit: REST trading/account/pocket APIs, public orderbook feed, private order stream
- Bithumb: REST trading/account/deposit/withdrawal APIs, public orderbook feed, private order stream
- Coinone: REST public data/trading/account/transaction APIs, public orderbook feed, private order stream
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

## Credentials

Credential loading supports four modes:

```text
auto: env -> keyring -> info.yaml
env: environment variables only
keyring: OS credential store only
info_yaml: local info.yaml only
```

Use `from_config()` for the automatic loader:

```python
from src.exchanges.upbit import UpbitRest

client = UpbitRest.from_config(source="auto")
```

The existing `from_info_yaml("info.yaml")` methods still work.

### Environment Variables

```text
TRADINGTOOLS_UPBIT_API_KEY
TRADINGTOOLS_UPBIT_SECRET_KEY
TRADINGTOOLS_BITHUMB_API_KEY
TRADINGTOOLS_BITHUMB_SECRET_KEY
TRADINGTOOLS_COINONE_ACCESS_TOKEN
TRADINGTOOLS_COINONE_SECRET_KEY
TRADINGTOOLS_BINANCE_API_KEY
TRADINGTOOLS_BINANCE_SECRET_KEY
TRADINGTOOLS_BINANCE_FUTURES_API_KEY
TRADINGTOOLS_BINANCE_FUTURES_SECRET_KEY
```

The `TRADINGTOOLS` prefix is the default only. For another project namespace, pass `env_prefix`:

```python
from src.exchanges.upbit.upbit_rest import UpbitRest

client = UpbitRest.from_config(source="env", env_prefix="MYAPP")
```

This reads `MYAPP_UPBIT_API_KEY` and `MYAPP_UPBIT_SECRET_KEY`.

You can also override the exact credential names:

```python
client = UpbitRest.from_config(
    source="env",
    env_primary="MY_UPBIT_KEY",
    env_secret="MY_UPBIT_SECRET",
)

client = UpbitRest.from_config(
    source="info_yaml",
    yaml_primary="my_upbit_key",
    yaml_secret="my_upbit_secret",
)
```

Upbit Pocket API keys support up to 5 slots:

```text
TRADINGTOOLS_UPBIT_POCKET_1_API_KEY
TRADINGTOOLS_UPBIT_POCKET_1_SECRET_KEY
TRADINGTOOLS_UPBIT_POCKET_2_API_KEY
TRADINGTOOLS_UPBIT_POCKET_2_SECRET_KEY
...
TRADINGTOOLS_UPBIT_POCKET_5_API_KEY
TRADINGTOOLS_UPBIT_POCKET_5_SECRET_KEY
```

### OS Keyring

Keyring support is optional and useful on desktop systems with an OS credential store.

```powershell
python -m pip install -r requirements-keyring.txt
python -m tools.credentials set upbit
python -m tools.credentials set bithumb
python -m tools.credentials set coinone
python -m tools.credentials set binance
python -m tools.credentials set binance_futures
python -m tools.credentials list
```

Use `--keyring-service` to store credentials under a different OS keyring namespace:

```powershell
python -m tools.credentials --keyring-service MyApp set upbit
python -m tools.credentials --keyring-service MyApp list
```

Keyring item names can also be overridden:

```powershell
python -m tools.credentials --keyring-service MyApp --keyring-primary my.key --keyring-secret my.secret set upbit
```

Upbit Pocket API keys can be registered as separate keyring entries:

```powershell
python -m tools.credentials set upbit_pocket_1
python -m tools.credentials set upbit_pocket_2
python -m tools.credentials set upbit_pocket_3
python -m tools.credentials set upbit_pocket_4
python -m tools.credentials set upbit_pocket_5
```

Load a specific Upbit Pocket slot:

```python
from src.exchanges.upbit import UpbitRest

client = UpbitRest.from_pocket_config(source="auto", pocket_index=1)
```

## API Surface Files

Each implemented Korean exchange has an `api_surface.py` file that acts like a lightweight header/documentation map:

```text
src/exchanges/upbit/api_surface.py
src/exchanges/bithumb/api_surface.py
src/exchanges/coinone/api_surface.py
src/exchanges/binance/api_surface.py
```

These files record implemented methods, endpoint paths, authentication requirements, and official documentation URLs.

## Examples

```python
from src.exchanges.bithumb.bithumb_rest import BithumbRest

client = BithumbRest.from_config(source="auto")
orderbook = client.get_orderbook("xrp-krw")
print(orderbook)
```

```python
from src.notifications.telegram_messenger import TelegramMessenger

messenger = TelegramMessenger.from_info_yaml_key("default", "info.yaml")
messenger.send_message("TradingTools KR notification test")
```

Run Upbit live API smoke tests with real credentials:

```powershell
python examples/upbit_quotation_test.py
python examples/upbit_exchange_test.py --source keyring
python examples/bithumb_exchange_test.py --source keyring
python examples/coinone_exchange_test.py --source keyring
python examples/binance_account_test.py --source keyring
python examples/upbit_live_test.py --private-read
python examples/upbit_live_test.py --source keyring --private-read
python examples/upbit_live_test.py --source keyring --private-read --trade
python examples/upbit_live_test.py --source keyring --use-pocket-key --pocket-index 1 --pocket-read
```

You can also run only the single deposit-address lookup. Use a currency/net_type pair that already has an address on your account.

```powershell
python examples/upbit_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX
python examples/bithumb_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX
```

Exchange API tests sleep for 1 second after each executed API request by default. Adjust with `--request-sleep` when needed.

The trade test uses `btc-krw`, checks the price before buy/sell, buys `6000` KRW with a market buy, checks the BTC balance before selling, then market-sells the bought BTC amount.

## Tests

```powershell
python -m unittest discover -s tests
```

The test suite avoids live exchange calls. Live API checks are kept in `examples/`.
All test workflows should save useful result artifacts for future API maintenance. Public API responses can be stored as fixtures, while private/authenticated responses should avoid sensitive fields by default. When explicitly requested for local debugging, unmasked responses may be written under ignored `tests/results/` directories.

## Repository Hygiene

The repository should contain source code, lightweight tests, and documentation only. Runtime artifacts such as `data/`, `logs/`, `*.db`, `*.sqlite3`, `*.csv`, `*.jsonl`, `.venv/`, and `info.yaml` are ignored.
