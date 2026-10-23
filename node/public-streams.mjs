// Native Node entry point for public streams. Authentication stays in Python.
// The owner manages subscription acknowledgement, heartbeat, reconnection and cleanup.
// Each socket handshake has a 10s deadline; this entry point never retries.
import { parseExchangeOrderbook } from "./orderbook-protocol.mjs"

// Web Crypto keeps this public-only entry point usable in browsers as well as Node.
const randomUUID = () => globalThis.crypto.randomUUID()

export const PUBLIC_STREAMS = {
  binance_spot: { url: "wss://stream.binance.com:9443/ws", source: "binance-spot-websocket" },
  binance_futures: { url: "wss://fstream.binance.com/public/stream", source: "binance-futures-websocket" },
  upbit: { url: "wss://api.upbit.com/websocket/v1", source: "upbit-websocket" },
  bithumb: { url: "wss://ws-api.bithumb.com/websocket/v1", source: "bithumb-websocket" },
  coinone: { url: "wss://stream.coinone.co.kr", source: "coinone-websocket" },
  korbit: { url: "wss://ws-api.digitalx.miraeasset.com/v2/public", source: "korbit-websocket" },
}

export function orderbookSubscriptions(exchangeId, quoteCurrency, symbols) {
  if (!Object.hasOwn(PUBLIC_STREAMS, exchangeId)) throw new Error("UNSUPPORTED_EXCHANGE")
  if (!Array.isArray(symbols) || !symbols.length || symbols.length > 100 ||
      symbols.some((symbol) => typeof symbol !== "string" || !/^[A-Z0-9]{2,15}$/.test(symbol))) {
    throw new Error("INVALID_MARKET_SYMBOLS")
  }
  const unique = [...new Set(symbols)]
  if (exchangeId === "korbit") {
    return [[{ requestId: 1, method: "subscribe", type: "orderbook",
      symbols: unique.map((symbol) => `${symbol.toLowerCase()}_${quoteCurrency.toLowerCase()}`) }]]
  }
  if (exchangeId.startsWith("binance_")) {
    return [{ method: "SUBSCRIBE",
      params: unique.map((symbol) => `${symbol.toLowerCase()}${quoteCurrency.toLowerCase()}@depth20@100ms`),
      id: "gridlab-depth" }]
  }
  if (exchangeId === "coinone") {
    return unique.map((symbol) => ({
      request_type: "SUBSCRIBE",
      channel: "ORDERBOOK",
      topic: { quote_currency: quoteCurrency, target_currency: symbol },
      format: "DEFAULT",
    }))
  }
  return [[
    { ticket: `gridlab-${randomUUID()}` },
    { type: "orderbook", codes: unique.map((symbol) => `${quoteCurrency}-${symbol}`) },
    { format: "DEFAULT" },
  ]]
}

export function orderbookSubscription(exchangeId, quoteCurrency, symbol) {
  return orderbookSubscriptions(exchangeId, quoteCurrency, [symbol])[0]
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

export function coinonePingMessage() {
  return { request_type: "PING" }
}

export function upbitDepthSubscription(codes, { ticket = "gridlab-overseas-arbitrage", format = "SIMPLE" } = {}) {
  return [{ ticket }, { type: "orderbook", codes }, { format }]
}

function requireSpotExchange(exchange) {
  if (!["upbit", "bithumb", "coinone", "korbit"].includes(exchange)) throw new Error("UNSUPPORTED_EXCHANGE")
}

export function spotDepthSubscriptions(exchange, codes) {
  requireSpotExchange(exchange)
  if (!Array.isArray(codes) || !codes.length || codes.length > 600 ||
      Array.from(codes).some(code => typeof code !== "string" || !/^KRW-[A-Z0-9]{1,20}$/.test(code))) {
    throw new Error("INVALID_MARKET_CODES")
  }
  if (exchange === "upbit" || exchange === "bithumb") return [spotDepthSubscription(exchange, codes)]
  const symbols = [...new Set(codes.map(code => code.slice(4)))]
  if (exchange === "coinone") return symbols.map(symbol => ({ request_type: "SUBSCRIBE", channel: "ORDERBOOK",
    topic: { quote_currency: "KRW", target_currency: symbol }, format: "DEFAULT" }))
  return [[{ requestId: 1, method: "subscribe", type: "orderbook",
    symbols: symbols.map(symbol => `${symbol.toLowerCase()}_krw`) }]]
}

export function spotDepthSubscription(exchange, codes) {
  requireSpotExchange(exchange)
  if (exchange === "coinone" || exchange === "korbit") {
    const messages = spotDepthSubscriptions(exchange, codes)
    if (messages.length !== 1) throw new Error("MULTIPLE_SUBSCRIPTIONS_REQUIRED")
    return messages[0]
  }
  if (!Array.isArray(codes) || !codes.length || codes.length > 600 ||
      Array.from(codes).some(code => typeof code !== "string" || !/^KRW-[A-Z0-9]{1,20}$/.test(code))) {
    throw new Error("INVALID_MARKET_CODES")
  }
  const unique = [...new Set(codes)]
  return upbitDepthSubscription(
    unique.map(code => exchange === "upbit" ? `${code}.5` : code),
    { format: "DEFAULT" },
  )
}

// Top-of-book quotes only: do not fabricate quantities for the full-depth parser.
export function spotDepthQuote(exchange, payload) {
  requireSpotExchange(exchange)
  if (exchange === "coinone" || exchange === "korbit") {
    const book = parseExchangeOrderbook(exchange, payload)
    if (!book || !/^KRW-[A-Z0-9]{1,20}$/.test(book.market)) return null
    const bid = book.bids[0]?.price, ask = book.asks[0]?.price
    if (!Number.isFinite(bid) || !Number.isFinite(ask) || bid <= 0 || ask <= 0 || bid > ask) return null
    return { symbol: book.market.slice(4), bid, ask }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null
  const type = payload.type ?? payload.ty
  if (type !== undefined && type !== "orderbook") return null
  const code = payload.code ?? payload.cd
  if (typeof code !== "string" || !/^KRW-[A-Z0-9]{1,20}$/.test(code)) return null
  const units = payload.orderbook_units ?? payload.obu
  if (!Array.isArray(units) || !units.length) return null
  const unit = units[0]
  const bidValue = unit?.bid_price ?? unit?.bp
  const askValue = unit?.ask_price ?? unit?.ap
  if (![bidValue, askValue].every(value => typeof value === "number" || typeof value === "string")) return null
  const bid = Number(bidValue), ask = Number(askValue)
  if (!Number.isFinite(bid) || !Number.isFinite(ask) || bid <= 0 || ask <= 0 || bid > ask) return null
  return { symbol: code.slice(4), bid, ask }
}
