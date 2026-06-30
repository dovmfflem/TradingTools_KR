from __future__ import annotations

from typing import Any, Final


SPOT_DOC_BASE: Final = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints"
FUTURES_DOC_BASE: Final = "https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api"


API_SURFACE: Final[dict[str, Any]] = {
    "exchange": "binance",
    "documentation": {
        "spot_account_url": SPOT_DOC_BASE,
        "futures_balance_url": f"{FUTURES_DOC_BASE}/Futures-Account-Balance-V3",
        "checked_date": "2026-07-01",
        "notes": [
            "Initial Binance surface focuses on spot and USD-M futures asset balance reads.",
            "Signed endpoints use Binance HMAC SHA256 query-string authentication.",
            "No live account request was executed during this surface check.",
        ],
    },
    "modules": {
        "spot_rest": {
            "client": "src.exchanges.binance.binance_spot_rest.BinanceSpotRest",
            "source_file": "src/exchanges/binance/binance_spot_rest.py",
            "base_url": "https://api.binance.com",
            "implemented": {
                "from_info_yaml(file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load binance_api_key/binance_secret_key, with legacy futures-key fallback.",
                },
                "from_config(source='auto', file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load credentials from env, keyring, then info.yaml.",
                },
                "get_account(omit_zero_balances=None)": {
                    "category": "Spot / Account",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/api/v3/account",
                    "doc": SPOT_DOC_BASE,
                },
                "get_balances(omit_zero_balances=True)": {
                    "category": "Spot / Account",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/api/v3/account",
                    "doc": SPOT_DOC_BASE,
                    "description": "Return the balances list from account information.",
                },
            },
            "missing": {},
        },
        "futures_rest": {
            "client": "src.exchanges.binance.binance_futures_rest.BinanceFuturesRest",
            "source_file": "src/exchanges/binance/binance_futures_rest.py",
            "base_url": "https://fapi.binance.com",
            "implemented": {
                "from_info_yaml(file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load binance_futures_api_key/binance_futures_secret_key, with legacy binance-key fallback.",
                },
                "from_config(source='auto', file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load credentials from env, keyring, then info.yaml.",
                },
                "get_balances()": {
                    "category": "USD-M Futures / Account",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/fapi/v3/balance",
                    "doc": f"{FUTURES_DOC_BASE}/Futures-Account-Balance-V3",
                },
                "get_balances_v2()": {
                    "category": "USD-M Futures / Account",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/fapi/v2/balance",
                    "doc": f"{FUTURES_DOC_BASE}/Futures-Account-Balance-V2",
                    "description": "Compatibility helper for older Binance Futures balance response.",
                },
            },
            "missing": {},
        },
    },
}


__all__ = ["API_SURFACE"]
