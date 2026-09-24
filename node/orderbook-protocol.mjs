// Exchange wire frames become bounded, sorted order books before the app uses them.
import { PUBLIC_STREAMS } from "./public-streams.mjs"

const MAX_LEVELS = 15

function numericLevel(price, quantity) {
  const p = Number(price)
  const q = Number(quantity)
  if (!Number.isFinite(p) || p <= 0 || !Number.isFinite(q) || q < 0) return null
  return { price: p, quantity: q }
}

function sortedLevels(levels, side) {
  const unique = new Map()
  for (const level of levels) {
    if (level && level.quantity > 0) unique.set(level.price, level)
  }
  return [...unique.values()]
    .sort((left, right) => side === "ask" ? left.price - right.price : right.price - left.price)
    .slice(0, MAX_LEVELS)
}

function timestampMilliseconds(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return Date.now()
  return numeric > 100_000_000_000_000 ? Math.floor(numeric / 1000) : numeric
}

function parseUpbitStyleOrderbook(payload) {
  const message = Array.isArray(payload) ? payload[0] : payload
  const units = message?.orderbook_units ?? message?.obu
  if (!Array.isArray(units)) return null
  return {
    market: String(message?.code ?? message?.cd ?? ""),
    asks: sortedLevels(units.map((unit) => numericLevel(unit?.ask_price ?? unit?.ap, unit?.ask_size ?? unit?.as)), "ask"),
    bids: sortedLevels(units.map((unit) => numericLevel(unit?.bid_price ?? unit?.bp, unit?.bid_size ?? unit?.bs)), "bid"),
    timestamp: timestampMilliseconds(message?.timestamp ?? message?.tms),
  }
}

function parseCoinoneOrderbook(payload) {
  const responseType = String(payload?.response_type ?? payload?.r ?? "").toUpperCase()
  const channel = String(payload?.channel ?? payload?.c ?? "").toUpperCase()
  if (responseType !== "DATA" || channel !== "ORDERBOOK") return null
  const data = payload?.data ?? payload?.d
  const asksRaw = data?.asks ?? data?.a
  const bidsRaw = data?.bids ?? data?.b
  if (!Array.isArray(asksRaw) || !Array.isArray(bidsRaw)) return null
  return {
    market: `${String(data?.quote_currency ?? data?.qc ?? "KRW").toUpperCase()}-${String(data?.target_currency ?? data?.tc ?? "").toUpperCase()}`,
    asks: sortedLevels(asksRaw.map((level) => numericLevel(level?.price ?? level?.p, level?.qty ?? level?.q)), "ask"),
    bids: sortedLevels(bidsRaw.map((level) => numericLevel(level?.price ?? level?.p, level?.qty ?? level?.q)), "bid"),
    timestamp: timestampMilliseconds(data?.timestamp ?? data?.t),
  }
}

export function parseExchangeOrderbook(exchangeId, payload) {
  if (!Object.hasOwn(PUBLIC_STREAMS, exchangeId)) throw new Error("UNSUPPORTED_EXCHANGE")
  if (exchangeId === "korbit") {
    if (payload?.type !== "orderbook" || !/^[a-z0-9]+_krw$/.test(payload?.symbol ?? "")) return null
    const { asks, bids } = payload.data ?? {}
    if (!Array.isArray(asks) || !Array.isArray(bids)) return null
    return { market: `KRW-${payload.symbol.split("_")[0].toUpperCase()}`,
      asks: sortedLevels(asks.map((row) => numericLevel(row.price, row.qty)), "ask"),
      bids: sortedLevels(bids.map((row) => numericLevel(row.price, row.qty)), "bid"),
      timestamp: timestampMilliseconds(payload.data.timestamp ?? payload.timestamp) }
  }
  if (exchangeId.startsWith("binance_")) {
    const message = payload?.data ?? payload
    const asks = message?.asks ?? message?.a
    const bids = message?.bids ?? message?.b
    if (!Array.isArray(asks) || !Array.isArray(bids)) return null
    return { market: message.s ?? "", asks: sortedLevels(asks.map(([p, q]) => numericLevel(p, q)), "ask"),
      bids: sortedLevels(bids.map(([p, q]) => numericLevel(p, q)), "bid"), timestamp: timestampMilliseconds(message.E) }
  }
  return exchangeId === "coinone" ? parseCoinoneOrderbook(payload) : parseUpbitStyleOrderbook(payload)
}
