from __future__ import annotations

from typing import cast

from src.exchanges.coinone.coinone_rest import CoinoneRest, CoinoneRestError


CoinoneApiError = CoinoneRestError


class CoinoneApi(CoinoneRest):
    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> CoinoneApi:
        return cast(CoinoneApi, super().from_info_yaml(file_path))


__all__ = ["CoinoneApi", "CoinoneRest", "CoinoneRestError", "CoinoneApiError"]
