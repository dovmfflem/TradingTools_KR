# Upbit Exchange API Test Checklist

이 체크리스트는 업비트 Exchange API 라이브 테스트에 무엇을 넣고, 무엇을 수동 확인으로 남길지 결정하기 위한 작업표입니다.

기본 원칙:

- 실제 거래는 `btc-krw` 기준 6000원 매수 후, 테스트로 증가한 BTC 수량만 매도한다.
- 코인을 전부 팔거나, 전체 주문을 취소하거나, 출금/전송/입금주소 생성처럼 자산 상태를 크게 바꾸는 API는 기본 자동 테스트에 넣지 않는다.
- 사용자가 명시한 로컬 디버깅 테스트 결과는 `tests/results/` 아래에 원문 그대로 저장할 수 있다. 이 경로는 Git에서 제외한다.
- 테스트 결과에는 요청 API, HTTP method, endpoint, params, 성공/실패/건너뜀 사유, 응답 또는 오류를 남긴다.
- Exchange API 테스트는 실제 API 호출 후 기본 1초 sleep을 둔다. 필요하면 `--request-sleep`으로 조절한다.

## 현재 자동 테스트 포함

`examples/upbit_exchange_test.py`에서 기본 실행되는 항목이다.

- [x] 계좌 조회: `GET /v1/accounts`
- [x] 주문 가능 정보 조회: `GET /v1/orders/chance`
- [x] 미체결 주문 조회: `GET /v1/orders/open`
- [x] 종료 주문 조회: `GET /v1/orders/closed`
- [x] 지갑 상태 조회: `GET /v1/status/wallet`
- [x] API 키 조회: `GET /v1/api_keys`
- [x] 출금 가능 정보 조회: `GET /v1/withdraws/chance`
- [x] 출금 주소 조회: `GET /v1/withdraws/coin_addresses`
- [x] 입금 주소 목록 조회: `GET /v1/deposits/coin_addresses`
- [x] 입금 가능 정보 조회: `GET /v1/deposits/chance/coin`
- [x] 입금 주소 조회: `GET /v1/deposits/coin_address`
  - 기본 `BTC/BTC` 주소가 없으면 실패가 아니라 건너뜀 처리
  - 단독 테스트: `python examples/upbit_exchange_test.py --only deposit_address --deposit-currency USDT --deposit-net-type TRX`
- [x] 주문 테스트 API: `POST /v1/orders/test`
- [x] 실제 6000원 매수/매도 테스트: `POST /v1/orders`
  - 기본값은 건너뜀
  - `--execute-trade` 지정 시에만 실행
  - 매수 전 현재가 조회, 매수 전 잔고 조회, 매도 전 잔고 조회, 매도 전 현재가 조회, 거래 후 잔고 조회 포함

## 자동 테스트 후보

조회 API라 자동화 가능성이 높지만, 안전한 기본 파라미터나 선행 데이터가 필요하다.

- [ ] 주문 단건 조회: `GET /v1/order`
  - 필요 값: 실제 주문 `uuid` 또는 `identifier`
  - 후보 방식: 6000원 테스트 주문 응답의 `uuid`로 바로 조회
- [ ] 주문 UUID 목록 조회: `GET /v1/orders/uuids`
  - 필요 값: 주문 `uuid[]` 또는 `identifier[]`
  - 후보 방식: 테스트 주문 응답 `uuid` 사용
- [ ] 출금 단건 조회: `GET /v1/withdraw`
  - 필요 값: 출금 `uuid` 또는 `txid`
  - 기본 자동화 보류
- [ ] 출금 목록 조회: `GET /v1/withdraws`
  - 기본 파라미터 없이 조회 가능한지 확인 필요
- [ ] 입금 단건 조회: `GET /v1/deposit`
  - 필요 값: 입금 `uuid` 또는 `txid`
  - 기본 자동화 보류
- [ ] 입금 목록 조회: `GET /v1/deposits`
  - 기본 파라미터 없이 조회 가능한지 확인 필요
- [ ] 트래블룰 VASP 목록 조회: `GET /v1/travel_rule/vasps`
  - 자산 이동 없음, 자동화 후보

## 포켓 API 테스트 후보

포켓 API 키와 포켓 권한이 있을 때만 의미가 있다.

- [ ] 포켓 정보 조회: `GET /v1/pockets`
- [ ] 포켓 API 키 조회: `GET /v1/pockets/api_keys`
- [ ] 서브포켓 잔고 조회: `GET /v1/pockets/asset`
  - 필요 값: 포켓 `uuid`
- [ ] 메인/서브포켓 이체 목록 조회: `GET /v1/pockets/transfers`
- [ ] 메인포켓 이체 생성: `POST /v1/pockets/transfers`
  - 자산 이동이므로 기본 자동화 금지
- [ ] 서브포켓 이체 생성: `POST /v1/pockets/transfers`
  - 자산 이동이므로 기본 자동화 금지

## 수동 확인 또는 별도 승인 필요

자산 이동, 주소 생성, 대량 취소, 외부 검증 API라 기본 테스트에 넣지 않는다.

- [ ] 주문 취소: `DELETE /v1/order`
  - 후보 방식: 테스트용 지정가 주문을 만들고 해당 주문만 취소
  - 전체 주문 취소와 혼동 금지
- [ ] 주문 UUID 목록 취소: `DELETE /v1/orders/uuids`
  - 후보 방식: 테스트용 주문 UUID만 취소
- [ ] 전체 미체결 주문 취소: `DELETE /v1/orders/open`
  - 기본 자동화 금지
- [ ] 취소 후 재주문: `POST /v1/orders/cancel_and_new`
  - 주문 상태 변화가 커서 별도 승인 필요
- [ ] 코인 출금 생성: `POST /v1/withdraws/coin`
  - 기본 자동화 금지
- [ ] 원화 출금 생성: `POST /v1/withdraws/krw`
  - 기본 자동화 금지
- [ ] 출금 취소: `DELETE /v1/withdraws/coin`
  - 실제 출금 UUID 필요
- [ ] 입금 주소 생성: `POST /v1/deposits/generate_coin_address`
  - 계정 상태 변경이므로 기본 자동화 금지
- [ ] 원화 입금 생성: `POST /v1/deposits/krw`
  - 실계좌 입금 플로우라 기본 자동화 금지
- [ ] 트래블룰 입금 UUID 검증: `POST /v1/travel_rule/deposit/uuid`
  - 필요 값: 입금 `deposit_uuid`, `vasp_uuid`
- [ ] 트래블룰 입금 TXID 검증: `POST /v1/travel_rule/deposit/txid`
  - 필요 값: `txid`, `currency`, `net_type`, `vasp_uuid`

## 아직 테스트 스크립트에 반영할지 결정 필요

- [ ] 실제 매수 후 주문 UUID로 `GET /v1/order`까지 조회할지
- [ ] 실제 매수 후 `GET /v1/orders/uuids`까지 조회할지
- [ ] `GET /v1/withdraws`와 `GET /v1/deposits`를 파라미터 없이 기본 조회해도 되는지
- [ ] `GET /v1/travel_rule/vasps`를 기본 자동 테스트에 넣을지
- [ ] 포켓 API는 별도 `--use-pocket-key --pocket-index` 옵션으로 분리할지
- [ ] 포켓 조회 API를 일반 API 키 테스트와 포켓 API 키 테스트 중 어디에 둘지
