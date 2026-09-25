"""One read per probe, strict identity and no mutation replay."""
import unittest
from unittest.mock import Mock
from src.exchanges.spot_trading import UpbitSpot
from src.exchanges.upbit.upbit_rest import UpbitRest
from src.exchanges.api_error import ExchangeRequestError


class UpbitMissingOrderTests(unittest.TestCase):
    def test_closed_filled_and_partial_cancel_use_exact_identity(self):
        client = Mock()
        adapter = UpbitSpot(client)
        row = dict(uuid='order-1', identifier='fixture-1', market='KRW-USDT', state='done',
                   side='bid', volume='5', executed_volume='5')
        client.list_closed_orders.return_value = [row]
        order, windows = adapter.resolve_closed_order('KRW-USDT', None, 'fixture-1', [(1000, 2000)])
        self.assertEqual((order.status, order.filled, windows), ('FILLED', '5', []))
        client.list_closed_orders.assert_called_once_with(ticker='KRW-USDT', states=['done', 'cancel'],
            start_time='1000', end_time='2000', limit=1000, order_by='desc')
        row.update(state='cancel', executed_volume='2')
        order, _ = adapter.resolve_closed_order('KRW-USDT', 'order-1', 'fixture-1', [(1000, 2000)])
        self.assertEqual((order.status, order.filled), ('CANCELED', '2'))

    def test_closed_does_not_match_price_or_mismatched_identifiers(self):
        client = Mock()
        row = dict(uuid='other', identifier='other-client', market='KRW-USDT', state='done',
                   side='bid', volume='5', executed_volume='5', price='1358')
        client.list_closed_orders.return_value = [row]
        self.assertEqual(UpbitSpot(client).resolve_closed_order('KRW-USDT', 'order-1', 'fixture-1', [(1000, 2000)]), (None, []))
        row['identifier'] = 'fixture-1'
        with self.assertRaisesRegex(ValueError, 'IDENTITY'):
            UpbitSpot(client).resolve_closed_order('KRW-USDT', 'order-1', 'fixture-1', [(1000, 2000)])

    def test_closed_full_window_splits_with_boundary_overlap(self):
        client = Mock()
        client.list_closed_orders.return_value = [dict(uuid=str(i), market='KRW-USDT', state='done') for i in range(1000)]
        order, windows = UpbitSpot(client).resolve_closed_order('KRW-USDT', None, 'fixture-1', [(1000, 2000), (0, 1000)])
        self.assertIsNone(order)
        self.assertEqual(windows, [(1500, 2000), (1000, 1500), (0, 1000)])
        client.list_closed_orders.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'TRUNCATED'):
            UpbitSpot(client).resolve_closed_order('KRW-USDT', None, 'fixture-1', [(1000, 1001)])

    def test_closed_malformed_and_open_orders_are_not_empty_history(self):
        client = Mock()
        for payload in ({}, None, [None], [dict(uuid='1', market='KRW-USDT', state='wait')]):
            client.list_closed_orders.return_value = payload
            with self.assertRaises(ValueError):
                UpbitSpot(client).resolve_closed_order('KRW-USDT', None, 'fixture-1', [(1000, 2000)])
        rest = UpbitRest.__new__(UpbitRest)
        rest._request = Mock(return_value={})
        with self.assertRaises(ExchangeRequestError):
            rest.list_closed_orders(ticker='KRW-USDT')

    def test_only_explicit_upbit_not_found_is_classified(self):
        adapter = UpbitSpot(Mock())
        self.assertTrue(adapter.is_order_missing(ExchangeRequestError('upbit', 'order_not_found', status_code=404)))
        for error in (TimeoutError(), ExchangeRequestError('upbit', 'out_of_scope', status_code=401),
                      ExchangeRequestError('bithumb', 'order_not_found', status_code=404)):
            self.assertFalse(adapter.is_order_missing(error))

    def test_empty_list_is_absence_and_no_market_filter_hides_identity(self):
        client = Mock()
        client.list_orders_by_ids.return_value = []
        self.assertIsNone(UpbitSpot(client).resolve_missing_order('KRW-USDT', 'fixture-1'))
        client.list_orders_by_ids.assert_called_once_with(identifiers=['fixture-1'])
        client.place_order.assert_not_called()

    def test_found_order_and_wrong_identifier(self):
        client = Mock()
        row = dict(uuid='order-1', market='KRW-USDT', side='ask', volume='5', executed_volume='5', state='done', identifier='fixture-1')
        client.list_orders_by_ids.return_value = [row]
        self.assertEqual(UpbitSpot(client).resolve_missing_order('KRW-USDT', 'fixture-1').status, 'FILLED')
        row['identifier'] = 'other'
        with self.assertRaisesRegex(ValueError, 'CLIENT_ID'):
            UpbitSpot(client).resolve_missing_order('KRW-USDT', 'fixture-1')

    def test_malformed_list_never_becomes_absence(self):
        client = Mock()
        for payload in ({}, None, [None], [{}, {}]):
            client.list_orders_by_ids.return_value = payload
            with self.assertRaises(ValueError):
                UpbitSpot(client).resolve_missing_order('KRW-USDT', 'fixture-1')

    def test_rest_list_does_not_swallow_non_list_response(self):
        client = UpbitRest.__new__(UpbitRest)
        client._request = Mock(return_value={'unexpected': 'object'})
        with self.assertRaises(ExchangeRequestError):
            client.list_orders_by_ids(identifiers=['fixture-1'])


if __name__ == '__main__':
    unittest.main()
