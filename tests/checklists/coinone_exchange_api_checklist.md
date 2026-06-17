# Coinone Exchange API Test Checklist

이 체크리스트는 코인원 Exchange API 라이브 테스트에 무엇을 넣고, 무엇을 수동 확인으로 남길지 정하기 위한 작업표입니다.

기본 원칙:

- 실제 거래 테스트는 `btc-krw` 기준 6000원 단위 매수/매도만 허용한다.
- 코인을 전부 팔거나 전액 출금하는 테스트는 넣지 않는다.
- 출금, 주소 등록/생성, 위험한 주문 생성처럼 자산 상태를 크게 바꾸는 API는 기본 자동 테스트에 넣지 않는다.
- 모든 Exchange API 테스트 호출 사이에는 기본 1초 sleep을 둔다.
- 테스트 결과는 `tests/results/coinone/exchange/` 아래 timestamp 디렉터리에 저장한다.
- 저장 결과에는 API 이름, HTTP method, endpoint, 요청 파라미터, 성공/실패/건너뜀, 응답 또는 에러 요약을 포함한다.
- 실계좌 값이 포함되는 결과는 Git에 커밋하지 않는다.

## 자동 테스트 후보

조회 위주라 기본 라이브 테스트에 넣기 좋은 항목이다.

- [ ] 전체 잔고 조회: `POST /v2.1/account/balance/all`
- [ ] 특정 자산 잔고 조회: `POST /v2.1/account/balance`
- [ ] 전체 수수료 조회: `POST /v2.1/account/trade_fee`
- [ ] 개별 종목 수수료 조회: `POST /v2.1/account/trade_fee/market`
- [ ] 가상자산 입금 주소 조회: `POST /v2.1/account/deposit_address`
- [ ] 미체결 주문 조회: `POST /v2.1/order/active_orders`
- [ ] 주문 정보 조회: `POST /v2.1/order/order_info`
  - 필요 값: 실제 테스트 주문의 `order_id` 또는 `user_order_id`
- [ ] 전체 체결 주문 조회: `POST /v2.1/order/completed_orders/all`
- [ ] 종목 별 체결 주문 조회: `POST /v2.1/order/completed_orders/market`
- [ ] 전체 미체결 주문 조회: `POST /v2.1/order/active_orders/all`
- [ ] 종목 별 미체결 주문 조회: `POST /v2.1/order/active_orders/market`
- [ ] 특정 주문 정보 조회: `POST /v2.1/order`
  - 필요 값: 실제 테스트 주문의 `order_id`
- [ ] 원화 입출금 내역 조회: `POST /v2.1/transaction/krw/history`
- [ ] 가상자산 입출금 내역 조회: `POST /v2.1/transaction/coin/history`
- [ ] 가상자산 입출금 단건 조회: `POST /v2.1/transaction/coin`
  - 필요 값: 실제 `transaction_id`
- [ ] 가상자산 출금 한도 조회: `POST /v2.1/transaction/coin/withdrawal/limit`
- [ ] 출금 주소 목록 조회: `POST /v2.1/transaction/coin/withdrawal/address`
- [ ] 주문 리워드 종목 정보 조회: `POST /v2.1/order/reward/markets`
- [ ] 주문 리워드 내역 조회: `POST /v2.1/order/reward/history`

## 거래 테스트 후보

명시 옵션이 있을 때만 실행한다.

- [ ] 매수/매도 주문: `POST /v2.1/order`
  - 매수 전 현재가 조회
  - 6000원 시장가 매수
  - 매수 후 BTC 잔고 조회
  - 매도 전 현재가 조회
  - 매수로 증가한 BTC 수량만 시장가 매도
  - 전량 매도 금지
- [ ] 개별 주문 취소: `POST /v2.1/order/cancel`
  - 별도 지정가 주문을 만든 뒤 해당 주문만 취소
- [ ] 종목 별 전체 주문 취소: `POST /v2.1/order/cancel/all`
  - 기본 자동 테스트 금지
  - 테스트용 주문만 존재하는 계정 또는 별도 확인 후 실행

## 수동 확인 또는 별도 승인 필요

자산 이동 또는 계정 상태 변경 가능성이 있어 기본 자동 테스트에는 넣지 않는다.

- [ ] 지정가 매매 주문: `POST /v2.1/order/limit`
  - 문서상 주문 권한 API지만 기본 자동 테스트에서는 제외
- [ ] 가상자산 출금: `POST /v2.1/transaction/coin/withdrawal`
  - 출금 주소, 네트워크, 수량이 필요하며 자산 이동 발생
- [ ] 출금 주소 등록/관리 흐름
  - 문서와 계정 상태에 따라 추가 검토 필요

## WebSocket 테스트 후보

실시간 연결 테스트는 REST 테스트와 분리해서 짧게 실행한다.

- [ ] Public ORDERBOOK 구독
- [ ] Public TICKER 구독
- [ ] Public TRADE 구독
- [ ] Public CHART 구독
- [ ] Private MYORDER 구독
- [ ] Private MYASSET 구독

## 아직 결정할 것

- [ ] 주문 생성 후 주문 ID로 `order_info`와 `order` 단건 조회까지 자동 연결할지
- [ ] 입금 주소 조회 대상 currency 기본값을 `BTC`로 둘지, 설정값으로 받을지
- [ ] 출금 관련 조회 API를 기본 테스트에 포함할지, `--include-withdrawal-queries` 같은 옵션으로 분리할지
- [ ] WebSocket 결과 샘플을 `tests/results/coinone/websocket/`에 저장할지
