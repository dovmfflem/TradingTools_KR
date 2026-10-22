# Bithumb wallet bridge contract

Implemented in `src.exchanges.bithumb.bithumb_wallet`. All I/O delegates to
`BithumbRest`; there is no endpoint passthrough, credentials lookup, retry, polling,
or implicit network/beneficiary inference. Tested only with synthetic responses.

```python
from src.exchanges.bithumb.bithumb_wallet import (
    BithumbSpotWallet, withdrawal_beneficiary, withdrawal_fee, withdrawal_fee_upper_bound,
)
wallet = BithumbSpotWallet(existing_bithumb_rest)
```

`BithumbSpotWallet` is also re-exported by `src.exchanges.arbitrage_spot`.
`wrap_spot` remains the trading factory and preserves raw Upbit identity; it does
not expose withdrawal operations through the trading adapter.

## Methods

All arguments are keyword-only except the optional first `uuid_value` on lookup:

```python
get_accounts()
get_krw_price(currency, max_age_seconds=5.0) # currency positional; max_age keyword-only
list_wallet_statuses()                    # get_wallet_status alias
list_withdraw_addresses(currency=None, net_type=None)
get_withdraw_chance(currency, net_type)
list_withdraws(currency=None, state=None, uuids=None, txids=None,
               page=None, limit=None, order_by=None)
get_withdraw(uuid_value=None, *, currency, txid=None, uuid=None)
create_coin_withdraw(currency, net_type, amount, address,
                     secondary_address=None, transaction_type=None,
                     exchange_name=None, receiver_type=None,
                     receiver_ko_name=None, receiver_en_name=None,
                     receiver_corp_ko_name=None, receiver_corp_en_name=None)
cancel_coin_withdraw(uuid_value)
get_deposit_address(currency, net_type)
create_deposit_address(currency, net_type)
list_deposit_addresses(currency=None, net_type=None)
get_deposit(uuid_value=None, *, currency, txid=None, uuid=None)
list_deposits(currency=None, state=None, uuids=None, txids=None,
              page=None, limit=None, order_by=None)
list_krw_deposits(state=None, page=None, limit=None, order_by=None)
list_krw_withdraws(state=None, page=None, limit=None, order_by=None)
```

`list_coin_withdraws` aliases `list_withdraws`; `list_coin_deposits` aliases
`list_deposits`. `api_url`, positive finite `timeout_seconds` setter,
`last_response_metadata`, and `close()` delegate to the inner client.
`capabilities` explicitly identifies supported operations and missing address
registration, consent, withdrawal idempotency, and history network/time filters.

## Responses and failure handling

Lists return lists of dictionaries; individual reads/submissions return dictionaries.
Native fields and nulls are preserved, including decimal strings and unknown networks.
Address keys include `currency`, `net_type`, `withdraw_address`, `secondary_address`,
`exchange_name`, and `owner_*`. Deposit addresses use `deposit_address`.
Transfer keys include `uuid`, `currency`, `net_type`, `state`, `amount`, `fee`,
`txid`, `created_at`, `done_at`. Nothing invents a missing txid/network/fee.

Wrapper states are uppercase; `raw_state` preserves the original. Deposits map
`DEPOSIT_ACCEPTED -> ACCEPTED`, `DEPOSIT_PROCESSING -> PROCESSING`,
`DEPOSIT_CANCELLED -> CANCELLED`, `REFUNDED_ACCEPTED -> REFUNDED`.
Other states, including pending/rejected travel-rule review states, remain native.
Normalized coin-deposit filters map back to the native values. Raw REST histories
retain native states for cashflow consumers.

All four raw coin/KRW history methods raise `BithumbRestError` with
`exchange=bithumb, code=INVALID_RESPONSE` on a non-list or invalid row type.
An actual empty list is valid. Limits are 1..100, pages positive, sort asc/desc.
No history silently turns malformed data into a healthy empty cursor.

Chance preserves `currency.wallet_state`, fixed/rate/min/max fee fields,
`account.balance`, `withdraw_limit.can_withdraw/remaining_daily/minimum`, and
`member_level`. `withdrawal_fee(chance, amount)` returns an unrounded Decimal,
including rate clamps; unknown/ambiguous fees raise ValueError, never become zero.
Exchange precision/rounding and debit-budget policy remain application-owned.

### Consistent conservative budget estimates

`withdrawal_fee_upper_bound(chance, amount) -> Decimal` applies ROUND_CEILING to
the calculated fee at `withdraw_limit.fixed` decimal places (integer 0..18).
This is an explicit library budgeting convention, not an official Bithumb fee
rounding rule and not a guaranteed bound on a future exchange charge. The official
field describes withdrawal amount precision; its use for fee budgeting is our
conservative choice. Unknown fee or precision fails closed. Clamps are applied
before rounding; the estimate can therefore slightly exceed the raw fee maximum.
`withdrawal_fee()` remains the unrounded fee calculation.

Use the same upper-bound helper for manual and automatic preview/revalidation.
Display the value as an estimated fee (`예상수수료`), reserve amount plus estimate,
and reconcile against the actual receipt fee. Do not change the requested transfer
amount to compensate silently. Refresh chance before dispatch; require renewed
confirmation when the estimate changes. A matching rounded estimate does not
prove the underlying fee schedule has stayed unchanged; persist fee metadata too
when that distinction matters to the application's approval policy.

`wallet.get_krw_price(currency, *, max_age_seconds=5.0) -> Decimal` reads the
existing public ticker endpoint with internal base/quote conversion. It requires
exactly one matching KRW market, finite positive price and a snapshot timestamp
within the requested age in either clock direction. Missing/invalid/stale prices
raise exchange-tagged errors, with no retry or cross-market fallback. KRW itself
returns Decimal(1) without I/O. The last trade timestamp is not snapshot freshness.

This returns `trade_price`, not max(last, ask): ticker has no ask and the official
chance schema does not specify the integrated-limit valuation oracle. For caller
preflight, conservatively convert amount plus fee budget to KRW, round upward to
whole KRW, and compare all applicable fiat limits independently, including
`remaining_daily_krw`. Apply any additional margin as explicit application policy.
Do not compare asset quantities directly to KRW fields or claim this estimate
guarantees exchange acceptance. Unknown required limits/valuation must fail closed.

Lookup requires currency plus uuid or txid. It never scans all currencies to
guess. Networks must be exact Bithumb `net_type` identifiers, independently matched
to Binance metadata; currency alone is not a network mapping.

## Beneficiaries

`withdrawal_beneficiary(registered_address, *, travel_rule_type=None)` is pure.
It maps ONLY `exchange_name` and official `owner_*` fields from a freshly selected
registry row to `exchange_name` and corresponding `receiver_*` request fields.
It ignores arbitrary extra keys and never substitutes names from other accounts.

- Default: return available nonempty metadata, possibly incomplete. The submit
  method rejects receiver metadata missing required personal/corporate names.
- `travel_rule_type="code"`: require complete personal names and, for corporation,
  both corporate names; raise with missing field names before any I/O.
- `"id_connect"` or `"whitelist"`: return exchange_name only; ID Connect rejects
  corporate owner metadata.
- `"internal"`: return no beneficiary fields.

The route is caller-verified configuration, never inferred from exchange_name
and never sent as an invented API parameter. Missing CODE names must be collected,
verified and preview-confirmed by the application, then passed explicitly as
receiver fields; do not silently downgrade to another route. Registry metadata
does not itself prove a route is currently eligible.

Legacy transaction_type None/default is accepted but omitted from the wire:
it is not a documented Bithumb withdrawal request parameter. Other values fail.
The response's native transaction_type is retained.

## Operational limitations

Address registration/approval and travel-rule consent remain Bithumb web/app
actions. A 422 `travel_rule_consent_required` propagates with actual exchange/code;
there is no automated consent call or replay. Mutation timeout, 5xx, invalid JSON,
or missing withdrawal acknowledgment UUID is uncertain and must be reconciled
before considering a new submission. Existing transport rate-limit metadata is
preserved. No real account, withdrawal, or transfer acceptance has been tested.

A generic HTTP 4xx plus outcome_unknown=False is not a sufficient durable
"safe to resubmit" classification: a duplicate/conflict can refer to an existing
operation. The application should use a narrow verified no-effect rejection
allowlist; preserve UNKNOWN for unrecognized/conflict/duplicate outcomes after
dispatch and reconcile rather than create a new request. Bithumb withdrawal has
no client identifier request field; order identifier semantics cannot be reused.

Chance documents wallet_state as a string with example working, not an exhaustive
enum. Wallet-status documents working, withdraw_only, deposit_only, paused.
Withdrawal preflight should require working/withdraw_only AND can_withdraw true
plus caller balance/limit checks; unknown states must not silently pass.

## Official references (checked 2026-10-22)

- [Withdrawal request and beneficiary matrix](https://apidocs.bithumb.com/reference/가상-자산-출금-요청)
- [Registered withdrawal addresses](https://apidocs.bithumb.com/reference/출금-허용-주소-리스트-조회)
- [Withdrawal chance and fees](https://apidocs.bithumb.com/reference/출금-가능-정보)
- [Individual withdrawal](https://apidocs.bithumb.com/reference/개별-출금-조회)
- [Withdrawal history](https://apidocs.bithumb.com/reference/출금-리스트-조회)
- [Deposit history](https://apidocs.bithumb.com/reference/입금-리스트-조회)
- [Wallet status](https://apidocs.bithumb.com/reference/입출금-현황)
- [Public ticker snapshot](https://apidocs.bithumb.com/reference/현재가-조회)

## Focused verification

`python -B tests/run_arbitrage_spot_contract.py` has a 120-second deadline per
child and saves stdout/stderr/result metadata under `tests/results/arbitrage-spot`.
Wallet tests additionally save synthetic request/response evidence without auth
headers, JWTs, real addresses, account identifiers or transaction IDs.
