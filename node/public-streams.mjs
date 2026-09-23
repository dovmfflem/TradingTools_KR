// Native Node entry point for public streams. Authentication stays in Python.
// The owner manages subscription acknowledgement, heartbeat, reconnection and cleanup.
// Each socket handshake has a 10s deadline; this entry point never retries.
import { randomUUID } from "node:crypto"

export const PUBLIC_STREAMS = {
  binance_spot: { url: "wss://stream.binance.com:9443/ws", source: "binance-spot-websocket" },
  binance_futures: { url: "wss://fstream.binance.com/public/stream", source: "binance-futures-websocket" },
  upbit: { url: "wss://api.upbit.com/websocket/v1", source: "upbit-websocket" },
  bithumb: { url: "wss://ws-api.bithumb.com/websocket/v1", source: "bithumb-websocket" },
  coinone: { url: "wss://stream.coinone.co.kr", source: "coinone-websocket" },
  korbit: { url: "wss://ws-api.korbit.co.kr/v2/public", source: "korbit-websocket" },
}

export function orderbookSubscription(exchangeId, quoteCurrency, symbol) {
  if (exchangeId === "korbit") {
    return [{ requestId: 1, method: "subscribe", type: "orderbook", symbols: [`${symbol.toLowerCase()}_${quoteCurrency.toLowerCase()}`] }]
  }
  if (exchangeId.startsWith("binance_")) {
    return { method: "SUBSCRIBE", params: [`${symbol.toLowerCase()}${quoteCurrency.toLowerCase()}@depth20@100ms`], id: "gridlab-depth" }
  }
  if (exchangeId === "coinone") {
    return {
      request_type: "SUBSCRIBE",
      channel: "ORDERBOOK",
      topic: { quote_currency: quoteCurrency, target_currency: symbol },
      format: "DEFAULT",
    }
  }
  return [
    { ticket: `gridlab-${randomUUID()}` },
    { type: "orderbook", codes: [`${quoteCurrency}-${symbol}`] },
    { format: "DEFAULT" },
  ]
}

export function createPublicSocket(exchangeId, {
  streams, WebSocketImpl = globalThis.WebSocket,
  setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
} = {}) {
  if (!Object.hasOwn(PUBLIC_STREAMS, exchangeId)) throw new Error("UNSUPPORTED_EXCHANGE")
  let url = PUBLIC_STREAMS[exchangeId].url
  if (streams !== undefined) {
    if (exchangeId !== "binance_futures" || !Array.isArray(streams) || !streams.length ||
        streams.some((stream) => !/^[a-z0-9]+@depth5$/.test(stream))) {
      throw new Error("INVALID_DEPTH_STREAMS")
    }
    // Preserve the existing runner/display depth5 feed, not the depth20 API-exchange feed.
    url = `wss://fstream.binance.com/stream?streams=${streams.join("/")}`
  }
  const socket = new WebSocketImpl(url)
  socket.tradingToolsState = "RUNNING"
  const close = socket.close.bind(socket)
  let timer = setTimeoutImpl(() => {
    timer = null
    if (socket.readyState !== 0) return
    socket.tradingToolsState = "TIMED_OUT"
    socket.tradingToolsFailure = "PUBLIC_STREAM_CONNECT_TIMED_OUT"
    close()
  }, 10_000)
  timer?.unref?.()
  const clear = () => {
    if (timer !== null) clearTimeoutImpl(timer)
    timer = null
  }
  socket.close = (...args) => { clear(); return close(...args) }
  socket.addEventListener("open", () => { clear(); socket.tradingToolsState = "CONNECTED" }, { once: true })
  socket.addEventListener("error", () => {
    clear()
    if (socket.tradingToolsState !== "TIMED_OUT") socket.tradingToolsState = "FAILED"
  }, { once: true })
  socket.addEventListener("close", () => {
    clear()
    if (!["TIMED_OUT", "FAILED"].includes(socket.tradingToolsState)) socket.tradingToolsState = "CLOSED"
  }, { once: true })
  return socket
}

export function upbitDepthSubscription(codes, { ticket = "gridlab-overseas-arbitrage", format = "SIMPLE" } = {}) {
  return [{ ticket }, { type: "orderbook", codes }, { format }]
}
