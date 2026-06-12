from __future__ import annotations

"""Compatibility shim for runtime imports.

The original bot runtime expects ``bithumb_api.BithumbApi`` and
``bithumb_api.BithumbApiError``. Internally this project now keeps the
implementation in ``bithumb_rest``.
"""

from typing import cast

from src.exchanges.bithumb.bithumb_rest import BithumbRest, BithumbRestError


BithumbApiError = BithumbRestError


class BithumbApi(BithumbRest):
    """Compatibility shim expected by the bot runtime entrypoint.

    The project historically used `bithumb_api.BithumbApi`, while the real
    implementation now lives in `bithumb_rest`.
    """

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> BithumbApi:
        return cast(BithumbApi, super().from_info_yaml(file_path))


__all__ = ["BithumbApi", "BithumbRest", "BithumbRestError", "BithumbApiError"]
