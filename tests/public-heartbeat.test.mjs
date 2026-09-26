import test from "node:test"
import assert from "node:assert/strict"
import { startPublicHeartbeat } from "../node/public-heartbeat.mjs"

function fixture(exchange) {
  let id = 0
  const jobs = new Map(), sent = [], failures = []
  const socket = { readyState: 1, send: value => sent.push(value) }
  const heartbeat = startPublicHeartbeat(exchange, socket, {
    later(fn, ms) { const key = ++id; jobs.set(key, { fn, ms }); return key },
    cancel(key) { jobs.delete(key) }, onFailure: code => failures.push(code),
  })
  const fire = ms => {
    const entry = [...jobs].find(([, job]) => job.ms === ms)
    assert.ok(entry, `missing timer ${ms}`)
    jobs.delete(entry[0]); entry[1].fn()
  }
  return { jobs, sent, failures, socket, heartbeat, fire }
}

for (const exchange of ["upbit", "bithumb", "coinone"]) {
  test(`${exchange}: correct wire heartbeat, response deadline and cleanup`, () => {
    const h = fixture(exchange), interval = exchange === "coinone" ? 60_000 : 30_000
    h.fire(interval)
    assert.equal(h.sent[0], exchange === "coinone" ? '{"request_type":"PING"}' : "PING")
    assert.equal(h.heartbeat.receive({ type: "orderbook" }), false)
    assert.ok([...h.jobs.values()].some(job => job.ms === 20_000))
    assert.equal(h.heartbeat.receive(exchange === "coinone" ? { response_type: "PONG" } : { status: "UP" }), true)
    assert.equal(h.jobs.size, 1)
    h.fire(interval)
    h.fire(20_000)
    assert.deepEqual(h.failures, ["PUBLIC_STREAM_PONG_TIMED_OUT"])
    assert.equal(h.jobs.size, 0)
    assert.equal(h.heartbeat.receive({ status: "UP" }), false)
  })
}
test("send failure is terminal; disposal cancels both pending timers", () => {
  const h = fixture("upbit")
  h.socket.send = () => { throw new Error("private transport detail") }
  h.fire(30_000)
  assert.deepEqual(h.failures, ["PUBLIC_STREAM_PING_FAILED"])
  assert.equal(h.jobs.size, 0)
  const other = fixture("coinone")
  other.fire(60_000); other.heartbeat.stop(); other.heartbeat.stop()
  assert.equal(other.jobs.size, 0)
  assert.deepEqual(other.failures, [])
})
test("no undocumented application heartbeat on DigitalX or Binance", () => {
  for (const exchange of ["korbit", "binance_spot", "binance_futures"]) {
    const h = fixture(exchange)
    assert.equal(h.jobs.size, 0)
    assert.equal(h.sent.length, 0)
  }
})
