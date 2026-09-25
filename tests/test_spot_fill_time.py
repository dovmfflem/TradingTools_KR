import unittest
from unittest.mock import Mock
from src.exchanges.spot_trading import execution_time, UpbitSpot, CoinoneSpot, KorbitSpot
from src.exchanges.private_events import normalize_private_order_event


class SpotFillTimeTests(unittest.TestCase):
    def test_seconds_milliseconds_and_iso_are_equivalent_invalid_is_optional(self):
        expected = 1790350834
        for value in (expected, str(expected * 1000), '2026-09-26T00:40:34+09:00'):
            self.assertEqual(execution_time(value), expected)
        for value in (None, 0, -1, 'NaN', 'Infinity', 'bad', '2026-09-26T00:40:34', 1e30):
            self.assertIsNone(execution_time(value))

    def test_coinone_uses_execution_timestamp_not_order_or_message_timestamp(self):
        event = normalize_private_order_event('coinone', {'response_type': 'DATA', 'channel': 'MYORDER', 'data': {
            'quote_currency': 'KRW', 'target_currency': 'USDT', 'order_id': 'fixture', 'side': 'ASK',
            'type': 'LIMIT', 'status': 'trade_done', 'remain_qty': '0', 'executed_qty': '5',
            'executed_timestamp': 1790350834000, 'timestamp': 1790351102000, 'order_timestamp': 1790350000000}}, 'KRW-USDT')
        self.assertEqual(CoinoneSpot(None).event(event, '5', 'sell').filled_at, 1790350834)

    def test_upbit_rest_uses_latest_trade_not_order_creation(self):
        row = {'uuid': 'fixture', 'market': 'KRW-USDT', 'side': 'ask', 'volume': '5',
            'executed_volume': '5', 'state': 'done', 'created_at': '2026-09-25T00:00:00+09:00',
            'trades': [{'created_at': '2026-09-26T00:40:34+09:00'}, {'created_at': '2026-09-26T00:39:00+09:00'}]}
        self.assertEqual(UpbitSpot(None).normalize(row, 'KRW-USDT').filled_at, 1790350834)

    def test_digitalx_rest_uses_last_fill(self):
        client = Mock()
        client.get_order.return_value = {'orderId': 1, 'symbol': 'usdt_krw', 'side': 'sell',
            'qty': '5', 'filledQty': '5', 'status': 'filled', 'createdAt': 1790350000000, 'lastFilledAt': 1790350834000}
        self.assertEqual(KorbitSpot(client).lookup('KRW-USDT', '1').filled_at, 1790350834)
