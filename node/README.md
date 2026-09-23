# Public market streams for Node

Native ESM entry point for Node's WebSocket runtime. No private credentials are
accepted here. This module centralizes the exchange endpoints/subscriptions used
by the dashboard without routing every market frame through a Python process.

```js
import { createPublicSocket, orderbookSubscription } from './public-streams.mjs'

const socket = createPublicSocket('upbit')
socket.addEventListener('open', () => {
  socket.send(JSON.stringify(orderbookSubscription('upbit', 'KRW', 'BTC')))
})
// Consumer owns message parsing, heartbeat/staleness, bounded reconnects and close.
```

Supported products: upbit, bithumb, coinone, korbit, binance_spot,
binance_futures. The last uses the public depth20 feed by default. Passing
`{ streams: ['btcusdt@depth5'] }` explicitly selects the existing combined depth5
feed. A 10s connect timeout sets `tradingToolsState=TIMED_OUT` and closes the
socket. No implicit reconnect or REST fallback occurs. Closing a socket cancels
its handshake timer. `CONNECTED` only means transport open, not verified data.

The application migration tests exercise endpoints, subscription payloads,
timeouts and consumer cleanup. Saved results are documented in the parent repo's
`docs/tradingtools-api-migration.md`.
