// Read-only public WS probe. No keys, orders or automatic reconnects.
// 10s handshake and 75s total deadline; results are always saved.
import { mkdirSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import { PUBLIC_STREAMS, createPublicSocket, orderbookSubscription } from "../node/public-streams.mjs"
import { startPublicHeartbeat } from "../node/public-heartbeat.mjs"

const output = process.argv[2] || "tests/results/public-heartbeat-live.json"
const probe = exchange => new Promise(resolve => {
  const result = { exchange, apiCategory: "public_websocket", apiName: "heartbeat", method: "WS",
    endpoint: PUBLIC_STREAMS[exchange].url, parameters: { ticker: "USDT", quoteCurrency: "KRW" },
    capturedAt: new Date().toISOString(), state: "RUNNING", heartbeatResponses: 0, messages: 0 }
  let heartbeat, done = false
  const socket = createPublicSocket(exchange)
  const finish = (state, reason) => {
    if (done) return
    done = true; clearTimeout(deadline); heartbeat?.stop()
    result.state = state; result.reason = reason; result.finishedAt = new Date().toISOString()
    socket.close(); resolve(result)
  }
  const deadline = setTimeout(() => finish(exchange === "korbit" && result.messages ? "CONNECTED" :
    result.heartbeatResponses ? "HEALTHY" : "TIMED_OUT", "75s observation complete"), 75_000)
  socket.addEventListener("open", () => {
    result.openedAt = new Date().toISOString()
    socket.send(JSON.stringify(orderbookSubscription(exchange, "KRW", "USDT")))
    heartbeat = startPublicHeartbeat(exchange, socket, { onFailure: code => finish("FAILED", code) })
  })
  socket.addEventListener("message", async event => {
    try {
      const raw = typeof event.data === "string" ? event.data : await event.data.text()
      const message = JSON.parse(raw)
      result.messages++
      if (heartbeat?.receive(message)) {
        result.heartbeatResponses++; result.responseSample = message
        if (exchange !== "korbit") finish("HEALTHY", "documented heartbeat response received")
      }
    } catch { result.decodeErrors = (result.decodeErrors || 0) + 1 }
  })
  socket.addEventListener("error", () => finish("FAILED", "socket error"))
  socket.addEventListener("close", event => { result.closeCode = event.code; finish("FAILED", "peer closed") })
})
const results = await Promise.all(["upbit", "bithumb", "coinone", "korbit"].map(probe))
mkdirSync(dirname(output), { recursive: true })
writeFileSync(output, JSON.stringify(results, null, 2))
console.log(JSON.stringify(results.map(({ exchange, state, reason, heartbeatResponses, messages }) =>
  ({ exchange, state, reason, heartbeatResponses, messages }))))
