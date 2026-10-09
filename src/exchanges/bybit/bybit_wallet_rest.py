"""Bybit V5 asset APIs; one request per call, never retry a mutation."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID

from .bybit_spot_rest import BybitSpotRest, BybitSpotRestError
from ..api_error import reject_retrying_transport


class BybitWalletRestError(Exception):
    def __init__(self, message, *, code=None, http_status=None, acceptance="UNKNOWN"):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.acceptance = acceptance


# Explicit validation/auth/balance rejections only. Duplicate IDs (10014,
# 131214), timeouts, busy/internal errors and unrecognized codes stay UNKNOWN.
# https://bybit-exchange.github.io/docs/v5/error
TRANSFER_REJECTION_CODES = frozenset({
    10001, 10002, 10003, 10004, 10005, 10007, 10010,
    131202, 131203, 131206, 131207, 131208, 131210, 131211, 131212,
    131215, 131226, 131227, 131228, 131229, 131231, 131232, 131233,
})


class BybitWalletRest(BybitSpotRest):
    def close(self):
        self._session.close()

    def _call(self, method, path, **values):
        # Do not let an injected retrying HTTP adapter replay financial requests.
        reject_retrying_transport(self._session, self.api_url + path)
        try:
            raw = self._request_signed(method, path, **{("params" if method == "GET" else "body"): values})
        except BybitSpotRestError as exc:
            # query-api may contain credentials: never propagate its raw envelope.
            rejected = (method == "POST" and path in {
                "/v5/asset/transfer/inter-transfer", "/v5/asset/transfer/universal-transfer"
            } and exc.code in TRANSFER_REJECTION_CODES
                and isinstance(exc.http_status, int) and 200 <= exc.http_status < 500)
            raise BybitWalletRestError(
                f"BYBIT_ASSET_API_ERROR:{path}", code=exc.code, http_status=exc.http_status,
                acceptance="REJECTED" if rejected else "UNKNOWN",
            ) from None
        if not isinstance(raw, dict) or raw.get("retCode") != 0 or not isinstance(raw.get("result"), dict):
            raise BybitWalletRestError("BYBIT_ASSET_RESPONSE_INVALID")
        return raw["result"]

    def get_api_identity(self):
        raw = self._call("GET", "/v5/user/query-api")
        return {key: raw.get(key) for key in ("userID", "isMaster", "parentUid", "readOnly", "permissions")}

    def list_sub_members(self):
        return self._call("GET", "/v5/asset/transfer/query-sub-member-list")

    def get_wallet_types(self, member_ids=None):
        return self._call("GET", "/v5/user/get-member-type", memberIds=member_ids)

    def get_coin_balance(self, *, coin, account_type, member_id=None, to_member_id=None, to_account_type=None):
        return self._call("GET", "/v5/asset/transfer/query-account-coin-balance", coin=coin,
                          accountType=account_type, memberId=member_id, toMemberId=to_member_id,
                          toAccountType=to_account_type, withTransferSafeAmount=1,
                          withLtvTransferSafeAmount=1 if to_account_type else 0)

    def create_transfer(self, *, transfer_id, coin, amount, from_account_type, to_account_type,
                        from_member_id=None, to_member_id=None):
        UUID(transfer_id)
        try:
            quantity = Decimal(str(amount))
        except InvalidOperation as exc:
            raise ValueError("INVALID_AMOUNT") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("INVALID_AMOUNT")
        if bool(from_member_id) != bool(to_member_id):
            raise ValueError("BOTH_MEMBER_IDS_REQUIRED")
        universal = from_member_id is not None
        if universal and str(from_member_id) == str(to_member_id):
            raise ValueError("UNIVERSAL_TRANSFER_REQUIRES_DIFFERENT_UIDS")
        if not universal and from_account_type == to_account_type:
            raise ValueError("SAME_WALLET")
        body = dict(transferId=transfer_id, coin=coin, amount=format(quantity, "f"),
                    fromAccountType=from_account_type, toAccountType=to_account_type)
        if universal:
            body.update(fromMemberId=int(from_member_id), toMemberId=int(to_member_id))
        return self._call("POST", "/v5/asset/transfer/" + ("universal-transfer" if universal else "inter-transfer"), **body)

    def list_transfers(self, *, universal=False, transfer_id=None, start_time=None, end_time=None, cursor=None):
        if start_time is not None and end_time is not None and not 0 <= end_time - start_time <= 7 * 86400000:
            raise ValueError("TRANSFER_HISTORY_WINDOW_EXCEEDS_7_DAYS")
        return self._call("GET", "/v5/asset/transfer/" + ("query-universal-transfer-list" if universal else "query-inter-transfer-list"),
                          transferId=transfer_id, startTime=start_time, endTime=end_time, cursor=cursor, limit=50)
