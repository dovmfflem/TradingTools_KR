from __future__ import annotations

from src.exchanges.bithumb.bithumb_rest import BithumbRest


def main() -> None:
    client = BithumbRest.from_info_yaml("info.yaml")
    orderbook = client.get_orderbook("xrp-krw")
    print(orderbook)


if __name__ == "__main__":
    main()
