import test, { after } from "node:test"
import assert from "node:assert/strict"
import { mkdirSync, writeFileSync } from "node:fs"
import { subscribeBrowserOrderbook } from "../node/browser-orderbook.mjs"

const captures = []
after(() => {
  const dir = new URL("./results/domestic-arbitrage/", import.meta.url)
  mkdirSync(dir, { recursive: true })
  writeFileSync(new URL("browser-orderbook-requests.json", dir), JSON.stringify({ mocked: true, capturedAt: new Date().toISOString(), requests: captures }, null, 2))
})
function harness(exchange = "upbit") {
  let time = 1800000000000, id = 0
  const timers = new Map(), sockets = [], books = [], statuses = []
  const later = (fn, ms) => { timers.set(++id, { fn, at: time + ms }); return id }
  const cancel = id => timers.delete(id)
  function advance(ms) { const end = time + ms; for(let n=0;n<1000;n++){const item=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!item||item[1].at>end)break;time=item[1].at;timers.delete(item[0]);item[1].fn()}time=end }
  class Socket extends EventTarget {
    constructor(url) { super(); this.url=url;this.readyState=0;this.sent=[];sockets.push(this) }
    open() { this.readyState=1;this.dispatchEvent(new Event("open")) }
    send(frame) { this.sent.push(frame === "PING" ? frame : JSON.parse(frame)) }
    message(data,binary=false) { const text=JSON.stringify(data);this.dispatchEvent(new MessageEvent("message",{data:binary?new TextEncoder().encode(text).buffer:text})) }
    close() { if(this.readyState===3)return;this.readyState=3;this.dispatchEvent(new Event("close")) }
  }
  const close = subscribeBrowserOrderbook(exchange,"XRP",b=>books.push(b),{onStatus:s=>statuses.push(s),WebSocketImpl:Socket,later,cancel,now:()=>time})
  const frame = () => exchange === "coinone" ? {response_type:"DATA",channel:"ORDERBOOK",data:{quote_currency:"KRW",target_currency:"XRP",timestamp:time,asks:[{price:"101",qty:"600"}],bids:[{price:"100",qty:"700"}]}} : exchange === "korbit" ? {type:"orderbook",symbol:"xrp_krw",data:{timestamp:time,asks:[{price:"101",qty:"600"}],bids:[{price:"100",qty:"700"}]}} : {code:"KRW-XRP",timestamp:time,orderbook_units:[{bid_price:100,ask_price:101,bid_size:700,ask_size:600}]}
  return {sockets,books,statuses,close,timers,advance,frame,now:()=>time}
}
test("four exchange public subscriptions deliver binary/text books immediately",()=>{
  for(const exchange of ["upbit","bithumb","coinone","korbit"]){
    const h=harness(exchange), socket=h.sockets[0];socket.open();socket.message(h.frame(),exchange==="upbit")
    assert.equal(h.books.at(-1).bid,"100");assert.equal(h.books.at(-1).asks[0].quantity,"600")
    captures.push({exchange,category:"public",api:"orderbook",method:"WEBSOCKET",endpoint:socket.url,parameters:socket.sent[0],state:"PASSED",response:h.frame()})
    h.close();assert.equal(h.timers.size,0)
  }
})
test("quiet and disconnected feeds retain prices, late frames ignored and retries stop at three",()=>{
  const h=harness();h.sockets[0].open();h.sockets[0].message(h.frame())
  const good=h.books.at(-1)
  h.advance(2000);assert.equal(h.books.at(-1),good);assert.equal(h.statuses.at(-1),"HEALTHY")
  h.advance(28000);h.sockets[0].message({status:"UP"});h.advance(19000)
  assert.equal(h.books.at(-1),good);assert.equal(h.statuses.at(-1),"HEALTHY");assert.equal(h.sockets.length,1)
  h.sockets[0].close();h.advance(2000);assert.equal(h.sockets.length,2)
  h.sockets[0].message(h.frame());assert.equal(h.books.at(-1),good)
  h.sockets[1].close();h.advance(4000);h.sockets[2].close();h.advance(60000)
  assert.equal(h.sockets.length,3);assert.equal(h.timers.size,0);h.close()
})
test("all exchanges keep quiet books; heartbeat failure changes status without clearing prices",()=>{
  for(const exchange of ["upbit","bithumb","coinone","korbit"]){
    const h=harness(exchange), s=h.sockets[0];s.open();assert.equal(h.statuses.at(-1),"CONNECTED");s.message(h.frame())
    const good=h.books.at(-1), interval=exchange==="coinone"?60000:30000
    for(let n=0;n<3;n++) { h.advance(interval);s.message(exchange==="coinone"?{response_type:"PONG"}:{status:"UP"}) }
    assert.equal(h.sockets.length,1);assert.equal(h.books.at(-1),good);assert.equal(h.statuses.at(-1),"HEALTHY")
    if(exchange!=="korbit") {h.advance(interval+20000);assert.equal(h.statuses.at(-1),"TIMED_OUT");assert.equal(h.books.at(-1),good)}
    h.close();assert.equal(h.timers.size,0)
  }
})
test("handshake and first-book deadlines cleanly close the transport",()=>{
  const h=harness();h.advance(10000);assert.equal(h.sockets[0].readyState,3);h.close();assert.equal(h.timers.size,0)
  const first=harness();first.sockets[0].open();first.advance(10000)
  assert.equal(first.statuses.at(-1),"TIMED_OUT");first.close();assert.equal(first.timers.size,0)
})
test("mismatched market, out-of-order and invalid books cannot replace current prices",()=>{
  const h=harness();const s=h.sockets[0];s.open();s.message(h.frame());const good=h.books.at(-1)
  s.message({...h.frame(),code:"KRW-BTC"});assert.equal(h.books.at(-1),good)
  s.message({...h.frame(),timestamp:h.now()-1});assert.equal(h.books.at(-1),good)
  s.message({...h.frame(),orderbook_units:[{bid_price:200,ask_price:100,bid_size:1,ask_size:1}]});assert.equal(h.books.at(-1),good);assert.equal(h.statuses.at(-1),"FAILED")
  h.close()
})
