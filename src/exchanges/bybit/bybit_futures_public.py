"""USDT linear perpetual public data; no private operations or retries."""
from .bybit_spot_rest import BybitSpotRest, BybitSpotRestError


class BybitFuturesPublic:
    def __init__(self, *, timeout_seconds=5):
        self._client = BybitSpotRest("public", "public", timeout_seconds=timeout_seconds)

    def close(self):
        self._client._session.close()

    def _get(self, path, params):
        payload = self._client._request_public(path, params=params)
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise BybitSpotRestError(f"Bybit public request rejected: {payload.get('retCode') if isinstance(payload, dict) else 'invalid'}")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise BybitSpotRestError("Invalid Bybit market response")
        return result

    def get_instruments_page(self, cursor=""):
        return self._get("/v5/market/instruments-info", {
            "category": "linear", "status": "Trading", "limit": 1000, "cursor": cursor,
        })

    def get_tickers(self):
        return self._get("/v5/market/tickers", {"category": "linear"})["list"]
