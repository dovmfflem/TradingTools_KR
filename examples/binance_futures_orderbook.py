from __future__ import annotations

from src.exchanges.binance.binance_futures_rest import BinanceFuturesRest


def main() -> None:
    client = BinanceFuturesRest.from_info_yaml("info.yaml")
    orderbook = client.get_orderbook("xrp-usdt", limit=20)
    print(orderbook)


if __name__ == "__main__":
    main()
