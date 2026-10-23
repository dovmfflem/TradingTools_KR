import test, { after } from "node:test"
import assert from "node:assert/strict"
import { mkdirSync, writeFileSync } from "node:fs"
import {
  spotDepthSubscription, spotDepthSubscriptions, spotDepthQuote, upbitDepthSubscription, orderbookSubscriptions,
} from "../node/public-streams.mjs"

const evidence = []
after(() => {
  const directory = new URL("./results/arbitrage-spot/", import.meta.url)
  mkdirSync(directory, { recursive: true })
  const stamp = new Date().toISOString().replaceAll(":", "-")
  writeFileSync(new URL(`node-frames-${stamp}.json`, directory), JSON.stringify(evidence, null, 2))
})

for (const exchange of ["upbit", "bithumb"]) {
  test(`${exchange}: 1, 101 and 600 canonical markets use DEFAULT and correct suffix`, () => {
    for (const count of [1, 101, 600]) {
      const codes = Array.from({ length: count }, (_, index) => `KRW-TEST${index}`)
      const frame = spotDepthSubscription(exchange, codes)
      assert.equal(frame[1].type, "orderbook")
      assert.deepEqual(frame[1].codes, codes.map(code => exchange === "upbit" ? `${code}.5` : code))
      assert.deepEqual(frame[2], { format: "DEFAULT" })
      assert.ok(codes.every(code => !code.includes(".")), "input unchanged")
      evidence.push({ exchange, api_category: "public", api_name: "spotDepthSubscription",
        method: "WebSocket subscribe", endpoint: exchange === "upbit" ? "wss://api.upbit.com/websocket/v1" :
          "wss://ws-api.bithumb.com/websocket/v1", request_parameters: { count, first: codes[0], last: codes.at(-1) },
        captured_at: new Date().toISOString(), state: "HEALTHY", response_summary: {
          transport: "synthetic, not sent", first: frame[1].codes[0], last: frame[1].codes.at(-1), format: frame[2].format,
        } })
    }
  })

  test(`${exchange}: deduplication, validation and legacy cap are separate`, () => {
    assert.equal(spotDepthSubscription(exchange, ["KRW-BTC", "KRW-BTC"])[1].codes.length, 1)
    for (const codes of [[], null, "KRW-BTC", [null], Array(2), ["KRW-BTC.5"], ["krw-btc"],
      ["BTC-ETH"], ["KRW-"], ["KRW-BTC\n"], Array(601).fill("KRW-BTC")]) {
      assert.throws(() => spotDepthSubscription(exchange, codes), /INVALID_MARKET_CODES/)
    }
    assert.throws(() => orderbookSubscriptions(exchange, "KRW", Array(101).fill("BTC")), /INVALID_MARKET_SYMBOLS/)
  })

  test(`${exchange}: DEFAULT and SIMPLE quote frames normalize identically`, () => {
    const payloads = [
      { type: "orderbook", code: "KRW-XRP", orderbook_units: [{ bid_price: 3000, ask_price: 3001, bid_size: 2, ask_size: 3 }] },
      { ty: "orderbook", cd: "KRW-XRP", obu: [{ bp: "3000", ap: "3001", bs: 2, as: 3 }] },
      { code: "KRW-XRP", orderbook_units: [{ bid_price: 3000, ask_price: 3001 }] },
    ]
    for (const payload of payloads) {
      const before = structuredClone(payload)
      const quote = spotDepthQuote(exchange, payload)
      assert.deepEqual(quote, { symbol: "XRP", bid: 3000, ask: 3001 })
      assert.deepEqual(payload, before)
      evidence.push({ exchange, api_category: "public", api_name: "spotDepthQuote", method: "WebSocket receive",
        endpoint: "orderbook", request_parameters: { format: payload.obu ? "SIMPLE" : "DEFAULT" },
        captured_at: new Date().toISOString(), state: "HEALTHY", response: payload, normalized: quote })
    }
    assert.deepEqual(spotDepthQuote(exchange, { cd: "KRW-BTC", obu: [{ bp: 100, ap: 100 }] }),
      { symbol: "BTC", bid: 100, ask: 100 })
  })

  test(`${exchange}: non-quotes and malformed prices return null`, () => {
    const base = { type: "orderbook", code: "KRW-XRP", orderbook_units: [{ bid_price: 3000, ask_price: 3001 }] }
    for (const payload of [null, undefined, "PING", [], [base], { status: "UP" }, { error: { name: "INVALID" } },
      { ...base, type: "ticker" }, { ...base, code: "BTC-XRP" }, { ...base, code: "KRW-XRP.5" },
      { ...base, orderbook_units: [] }, { ...base, orderbook_units: [null] },
      ...[NaN, Infinity, 0, -1, "", "bad", true, [], {}, null].map(bid_price => ({ ...base,
        orderbook_units: [{ bid_price, ask_price: 3001 }] })),
      { ...base, orderbook_units: [{ bid_price: 3002, ask_price: 3001 }] },
      { ...base, orderbook_units: [{ bid_price: 3000, ask_price: Infinity }] }]) {
      assert.equal(spotDepthQuote(exchange, payload), null)
    }
  })
}

test("legacy Upbit helper remains unchanged; new helper rejects unsupported exchange", () => {
  assert.deepEqual(upbitDepthSubscription(["KRW-BTC.5"]), [
    { ticket: "gridlab-overseas-arbitrage" }, { type: "orderbook", codes: ["KRW-BTC.5"] }, { format: "SIMPLE" },
  ])
  assert.deepEqual(upbitDepthSubscription(["KRW-BTC"], { ticket: "custom", format: "DEFAULT" }), [
    { ticket: "custom" }, { type: "orderbook", codes: ["KRW-BTC"] }, { format: "DEFAULT" },
  ])
  assert.throws(() => spotDepthSubscription("unknown", ["KRW-BTC"]), /UNSUPPORTED_EXCHANGE/)
  assert.throws(() => spotDepthQuote("unknown", {}), /UNSUPPORTED_EXCHANGE/)
})

for (const exchange of ["coinone", "korbit"]) {
  test(`${exchange}: native subscription and quote normalization`, () => {
    const frames = spotDepthSubscriptions(exchange, ["KRW-XRP", "KRW-XRP", "KRW-P"])
    assert.equal(frames.length, exchange === "coinone" ? 2 : 1)
    if (exchange === "coinone") {
      assert.deepEqual(frames.map(frame => frame.topic.target_currency), ["XRP", "P"])
      assert.throws(() => spotDepthSubscription(exchange, ["KRW-XRP", "KRW-P"]), /MULTIPLE_SUBSCRIPTIONS_REQUIRED/)
    } else assert.deepEqual(frames[0][0].symbols, ["xrp_krw", "p_krw"])
    const data = { asks: [{ price: "101", qty: "2" }], bids: [{ price: "100", qty: "3" }] }
    const payload = exchange === "coinone" ? { response_type: "DATA", channel: "ORDERBOOK",
      data: { ...data, quote_currency: "KRW", target_currency: "XRP" } }
      : { type: "orderbook", symbol: "xrp_krw", data }
    assert.deepEqual(spotDepthQuote(exchange, payload), { symbol: "XRP", bid: 100, ask: 101 })
    assert.equal(spotDepthQuote(exchange, {}), null)
    assert.equal(spotDepthQuote(exchange, { ...payload, data: { ...payload.data, bids: [] } }), null)
    assert.equal(spotDepthQuote(exchange, { ...payload, data: { ...payload.data, bids: [{ price: "102", qty: "3" }] } }), null)
    assert.throws(() => spotDepthSubscriptions(exchange, ["BTC-XRP"]), /INVALID_MARKET_CODES/)
    evidence.push({ exchange, api_category: "public", api_name: "spotDepthSubscriptions/spotDepthQuote",
      method: "WebSocket synthetic", endpoint: "orderbook", request_parameters: { markets: ["KRW-XRP", "KRW-P"] },
      captured_at: new Date().toISOString(), state: "HEALTHY", response: payload, subscriptions: frames })
  })
}
