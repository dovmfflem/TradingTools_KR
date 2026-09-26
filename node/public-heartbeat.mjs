// Official application heartbeats for native WebSocket clients (no ping() API).
// Upbit/Bithumb: literal PING -> {status: UP}; Coinone: JSON PING -> PONG.
// No undocumented application messages are sent to other exchanges.
export function startPublicHeartbeat(exchange, socket, {
  later = setTimeout, cancel = clearTimeout, onFailure = () => {},
} = {}) {
  const supported = ["upbit", "bithumb", "coinone"].includes(exchange)
  const interval = exchange === "coinone" ? 60_000 : 30_000
  let tick = null, deadline = null, stopped = false
  const stop = () => {
    stopped = true
    if (tick !== null) cancel(tick)
    if (deadline !== null) cancel(deadline)
    tick = deadline = null
  }
  const fail = code => { stop(); onFailure(code) }
  const schedule = () => {
    tick = later(() => {
      tick = null
      if (stopped || socket.readyState !== 1) return
      deadline = later(() => fail("PUBLIC_STREAM_PONG_TIMED_OUT"), 20_000)
      deadline?.unref?.()
      try { socket.send(exchange === "coinone" ? JSON.stringify({ request_type: "PING" }) : "PING") }
      catch { fail("PUBLIC_STREAM_PING_FAILED"); return }
      schedule()
    }, interval)
    tick?.unref?.()
  }
  if (supported) schedule()
  return { stop, receive(message) {
    if (stopped || !supported) return false
    const pong = exchange === "coinone" ? message?.response_type === "PONG" : message?.status === "UP"
    if (pong && deadline !== null) { cancel(deadline); deadline = null }
    return pong
  } }
}
