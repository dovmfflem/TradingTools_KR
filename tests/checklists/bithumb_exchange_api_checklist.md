# Bithumb Exchange API Test Checklist

이 체크리스트는 빗썸 Exchange API 라이브 테스트에 무엇을 넣고, 무엇을 수동 확인으로 남길지 결정하기 위한 작업표입니다.

기본 원칙:

- 실제 거래는 `btc-krw` 기준 6000원 매수 후, 테스트로 증가한 BTC 수량만 매도한다.
- 코인을 전부 팔거나, 다건 주문/다건 취소, 출금/전송/입금주소 생성처럼 자산 상태를 크게 바꾸는 API는 기본 자동 테스트에 넣지 않는다.
- 사용자가 명시한 로컬 디버깅 테스트 결과는 `tests/results/` 아래에 원문 그대로 저장할 수 있다. 이 경로는 Git에서 제외한다.
- 테스트 결과에는 요청 API, HTTP method, endpoint, params, 성공/실패/건너뜀 사유, 응답 또는 오류를 남긴다.
- Exchange API 테스트는 실제 API 호출 후 기본 1초 sleep을 둔다. 필요하면 `--request-sleep`으로 조절한다.

## 현재 자동 테스트 포함

`examples/bithumb_exchange_test.py`에서 기본 실행되는 항목이다.

- [x] 계좌 조회: `GET /v1/accounts`
- [x] 주문 가능 정보 조회: `GET /v1/orders/chance`
- [x] 미체결 주문 조회: `GET /v1/orders`, `state=wait`
- [x] 주문 리스트 조회: `GET /v1/orders`
- [x] 지갑 상태 조회: `GET /v1/status/wallet`
- [x] API 키 조회: `GET /v1/api_keys`
- [x] 출금 가능 정보 조회: `GET /v1/withdraws/chance`
- [x] 출금 주소 조회: `GET /v1/withdraws/coin_addresses`
- [x] 코인 출금 목록 조회: `GET /v1/withdraws`
- [x] 원화 출금 목록 조회: `GET /v1/withdraws/krw`
- [x] 입금 주소 목록 조회: `GET /v1/deposits/coin_addresses`
- [x] 입금 주소 조회: `GET /v1/deposits/coin_address`
  - 지정한 `currency/net_type` 주소가 없으면 실패가 아니라 건너뜀 처리
  - 단독 테스트: `python examples/bithumb_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX`
- [x] 코인 입금 목록 조회: `GET /v1/deposits`
- [x] 원화 입금 목록 조회: `GET /v1/deposits/krw`
- [x] 실제 6000원 매수/매도 테스트: `POST /v2/orders`
  - 기본값은 건너뜀
  - `--execute-trade` 지정 시에만 실행
  - 매수 전 현재가 조회, 매수 전 잔고 조회, 매도 전 잔고 조회, 매도 전 현재가 조회, 거래 후 잔고 조회 포함

## 자동 테스트 후보

조회 API라 자동화 가능성이 높지만, 안전한 기본 파라미터나 선행 데이터가 필요하다.

- [ ] 주문 단건 조회: `GET /v1/order`
  - 필요 값: 주문 `uuid` 또는 `client_order_id`
  - 후보 방식: 6000원 테스트 주문 응답의 주문 ID 사용 가능 여부 확인
- [ ] 출금 단건 조회: `GET /v1/withdraw`
  - 필요 값: 출금 `uuid` 또는 `txid`
  - 기본 자동화 보류
- [ ] 입금 단건 조회: `GET /v1/deposit`
  - 필요 값: 입금 `uuid` 또는 `txid`
  - 기본 자동화 보류

## 수동 확인 또는 별도 승인 필요

자산 이동, 주소 생성, 주문 상태 변경이 큰 API라 기본 테스트에 넣지 않는다.

- [ ] 주문 취소: `DELETE /v2/order`
  - 후보 방식: 테스트용 지정가 주문을 만들고 해당 주문만 취소
- [ ] 다건 주문 요청: `POST /v2/orders`
  - 기본 자동화 금지
- [ ] 다건 주문 취소: `DELETE /v2/orders`
  - 기본 자동화 금지
- [ ] 코인 출금 생성: `POST /v1/withdraws/coin`
  - 기본 자동화 금지
- [ ] 원화 출금 생성: `POST /v1/withdraws/krw`
  - 기본 자동화 금지
- [ ] 코인 출금 취소: `DELETE /v1/withdraws/coin`
  - 실제 출금 UUID 필요
- [ ] 입금 주소 생성: `POST /v1/deposits/generate_coin_address`
  - 계정 상태 변경이므로 기본 자동화 금지
- [ ] 원화 입금 생성: `POST /v1/deposits/krw`
  - 실계좌 입금 플로우라 기본 자동화 금지

## 아직 테스트 스크립트에 반영할지 결정 필요

- [ ] 실제 매수 후 주문 ID로 `GET /v1/order`까지 조회할지
- [ ] `GET /v1/withdraws`, `GET /v1/deposits`를 currency 없이도 조회할지
- [ ] 지정가 주문 생성 후 단건 취소 테스트를 별도 옵션으로 둘지
- [ ] 다건 주문/다건 취소는 영구 수동 테스트로 둘지
