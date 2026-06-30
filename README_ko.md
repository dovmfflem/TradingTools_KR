# TradingTools KR

[![CI](https://github.com/dovmfflem/TradingTools_KR/actions/workflows/ci.yml/badge.svg)](https://github.com/dovmfflem/TradingTools_KR/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Runtime deps: 3](https://img.shields.io/badge/Runtime%20deps-3-brightgreen.svg)](requirements.txt)

언어: [English](README.md) | **Korean**

TradingTools KR은 거래, 계좌 조회, 시장 데이터, 호가 수집, 개인 주문 스트림, 텔레그램 알림을 위한 거래소 API 클라이언트 모음입니다.

## 포함 모듈

- Upbit: REST 거래/계좌/포켓 API, 공개 호가 피드, 개인 주문 스트림
- Bithumb: REST 거래/계좌/입출금 API, 공개 호가 피드, 개인 주문 스트림
- Coinone: REST 공개 데이터/거래/계좌/입출금 내역 API, 공개 호가 피드, 개인 주문 스트림
- Binance Futures: REST 주문/계좌, 개인 주문 스트림, 공개 spot/futures 호가 피드
- Bybit Spot: REST 주문/계좌, 공개 spot/futures 호가 피드
- Bitget Spot: REST 주문/계좌, 공개 spot/futures 호가 피드
- Lighter: REST 주문 헬퍼와 공개 호가 피드
- Telegram: rate limit과 retry가 포함된 재사용 가능한 알림 전송기

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

템플릿을 복사한 뒤 필요한 인증정보만 채웁니다.

```powershell
Copy-Item info_template.yaml info.yaml
```

`info.yaml`은 Git에서 제외됩니다. 실제 API 키, private key, 텔레그램 토큰은 커밋하지 마세요.

## 인증정보

인증정보 로딩은 네 가지 모드를 지원합니다.

```text
auto: env -> keyring -> info.yaml
env: 환경변수만 사용
keyring: OS 보안 저장소만 사용
info_yaml: 로컬 info.yaml만 사용
```

자동 로더는 `from_config()`로 사용합니다.

```python
from src.exchanges.upbit import UpbitRest

client = UpbitRest.from_config(source="auto")
```

기존 `from_info_yaml("info.yaml")` 방식도 계속 동작합니다.

### 환경변수

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

업비트 포켓 API 키는 최대 5개 슬롯을 지원합니다.

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

Keyring 지원은 선택 사항입니다. Windows Credential Manager, macOS Keychain 같은 UI가 있는 로컬 개발 환경에서 유용합니다.

```powershell
python -m pip install -r requirements-keyring.txt
python -m tools.credentials set upbit
python -m tools.credentials set bithumb
python -m tools.credentials set coinone
python -m tools.credentials set binance
python -m tools.credentials set binance_futures
python -m tools.credentials list
```

업비트 포켓 API 키는 별도 슬롯으로 등록할 수 있습니다.

```powershell
python -m tools.credentials set upbit_pocket_1
python -m tools.credentials set upbit_pocket_2
python -m tools.credentials set upbit_pocket_3
python -m tools.credentials set upbit_pocket_4
python -m tools.credentials set upbit_pocket_5
```

특정 업비트 포켓 슬롯을 로드하는 예시입니다.

```python
from src.exchanges.upbit import UpbitRest

client = UpbitRest.from_pocket_config(source="auto", pocket_index=1)
```

## API Surface 파일

국내 거래소 구현에는 `api_surface.py` 파일이 포함되어 있습니다. C의 header 파일처럼 구현된 API 표면을 한 곳에서 확인하기 위한 문서 맵입니다.

```text
src/exchanges/upbit/api_surface.py
src/exchanges/bithumb/api_surface.py
src/exchanges/coinone/api_surface.py
src/exchanges/binance/api_surface.py
```

각 파일에는 구현된 메서드, endpoint path, 인증 필요 여부, 공식 문서 URL이 기록되어 있습니다.

## 예시

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

실제 인증정보로 업비트 라이브 smoke test를 실행할 수 있습니다.

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

입금 주소 단건 조회만 따로 확인할 수도 있습니다. `--deposit-currency`와 `--deposit-net-type`에는 실제 생성된 입금 주소 페어를 넣습니다.

```powershell
python examples/upbit_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX
python examples/bithumb_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX
```

Exchange API 테스트는 실제 API 호출 후 기본 1초씩 대기합니다. 필요하면 `--request-sleep`으로 조절합니다.

거래 테스트는 `btc-krw` 기준으로 매수/매도 전 가격을 조회하고, 6000원 시장가 매수 후 BTC 잔고를 확인한 다음 매수된 BTC 수량만큼 시장가 매도합니다.

## 테스트

```powershell
python -m unittest discover -s tests
```

기본 테스트는 실제 거래소 API를 호출하지 않습니다. 라이브 API 확인 코드는 `examples/`에 분리되어 있습니다.
모든 테스트 워크플로는 이후 API 유지보수에 쓸 수 있도록 유용한 결과물을 저장하는 것을 원칙으로 합니다. 공개 API 응답은 fixture로 저장할 수 있고, 개인/인증 API 응답은 기본적으로 민감 정보 저장을 피합니다. 사용자가 로컬 디버깅을 위해 명시적으로 요청한 경우에만 `tests/results/` 아래에 원문 응답을 저장합니다.

## 저장소 관리

저장소에는 소스 코드, 가벼운 테스트, 문서를 포함합니다. `data/`, `logs/`, `*.db`, `*.sqlite3`, `*.csv`, `*.jsonl`, `.venv/`, `info.yaml` 같은 런타임 산출물은 Git에서 제외됩니다.
