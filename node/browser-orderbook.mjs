// Public data only. No credentials, private requests, REST fallback or order execution.
import { createPublicSocket, orderbookSubscription, coinonePingMessage } from "./public-streams.mjs"
import { parseExchangeOrderbook } from "./orderbook-protocol.mjs"

export function subscribeBrowserOrderbook(exchange, ticker, onBook, {
  onStatus = (_status) => {}, WebSocketImpl = globalThis.WebSocket,
  later = setTimeout, cancel = clearTimeout, now = Date.now,
} = {}) {
  if (!["upbit", "bithumb", "coinone", "korbit"].includes(exchange) || !/^[A-Z0-9]{2,15}$/.test(ticker)) throw new Error("INVALID_MARKET")
  const subscription = orderbookSubscription(exchange, "KRW", ticker)
  let socket, closed = false, attempts = 0, generation = 0, lastTimestamp = 0
  let retry, first, stale, idle, heartbeat
  const clear = () => { for (const timer of [first, stale, idle, heartbeat]) cancel(timer); first = stale = idle = heartbeat = undefined }
  function connect() {
    if (closed) return
    attempts++; const current = ++generation
    let failed = false
    onStatus("CONNECTING"); onBook(null)
    function fail(reason) {
      if (closed || failed || current !== generation) return
      failed = true; clear(); onBook(null); onStatus(reason)
      socket?.close()
      if (attempts < 3) retry = later(connect, attempts * 2000)
    }
    try { socket = createPublicSocket(exchange, { WebSocketImpl, setTimeoutImpl: later, clearTimeoutImpl: cancel }) }
    catch { fail("FAILED"); return }
    socket.binaryType = "arraybuffer"
    socket.addEventListener("open", () => {
      if (closed || failed || current !== generation) return
      try { socket.send(JSON.stringify(subscription)) } catch { fail("FAILED"); return }
      first = later(() => fail("TIMED_OUT"), 10000)
      if (exchange === "coinone") {
        const ping = () => {
          if (closed || failed || current !== generation) return
          try { socket.send(JSON.stringify(coinonePingMessage())) } catch { fail("FAILED"); return }
          heartbeat = later(ping, 20000)
        }
        heartbeat = later(ping, 20000)
      }
    })
    socket.addEventListener("message", event => {
      if (closed || failed || current !== generation) return
      try {
        const raw = event.data
        if (typeof raw !== "string" && !(raw instanceof ArrayBuffer)) throw new Error("INVALID_FRAME")
        if ((typeof raw === "string" ? raw.length : raw.byteLength) > 262144) throw new Error("FRAME_LIMIT")
        const data = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw))
        if (data.error || data.response_type === "ERROR" || ["fail", "error"].includes(data.status)) throw new Error("SUBSCRIPTION_FAILED")
        const book = parseExchangeOrderbook(exchange, data)
        if (!book || book.market !== `KRW-${ticker}`) return
        if (!book.bids.length || !book.asks.length || book.bids[0].price > book.asks[0].price) throw new Error("INVALID_BOOK")
        // Browser wall clocks can differ from the exchange. Display freshness is
        // receive-based; execution uses the independent server feed and its strict clock checks.
        if (book.timestamp < lastTimestamp) return
        lastTimestamp = book.timestamp
        cancel(first); cancel(stale); cancel(idle)
        const level = row => ({ price: String(row.price), quantity: String(row.quantity) })
        onBook({ bid: String(book.bids[0].price), ask: String(book.asks[0].price),
          bids: book.bids.slice(0, 10).map(level), asks: book.asks.slice(0, 10).map(level), received_at: now() / 1000 })
        onStatus("HEALTHY")
        stale = later(() => { onBook(null); onStatus("STALE") }, 2000)
        idle = later(() => fail("TIMED_OUT"), 30000)
      } catch { fail("FAILED") }
    })
    socket.addEventListener("error", () => fail("FAILED"))
    socket.addEventListener("close", () => fail("DISCONNECTED"))
  }
  connect()
  return () => { closed = true; generation++; clear(); cancel(retry); socket?.close() }
}
