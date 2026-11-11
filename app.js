/* Desk · plan B static app. Data files are AES-256-GCM; key arrives once as #d=<base64url>, lives in localStorage. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const KEYSTORE = "desk.k";
const FORCE = new URLSearchParams(location.search).get("force"); // loading|empty|error|locked (screenshots/QA)
let KEY = null, HKEY = null, STATE = null, RANGE = "24H", timer = null, lastBalance = null;
const AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"];
const LAYERS = ["Mint authority","Freeze authority","Dev wallet","Top 10 holders","Liquidity","Pool age","Bundled wallets","Delegations"];

function b64u(s){s=s.replace(/-/g,"+").replace(/_/g,"/");while(s.length%4)s+="=";return Uint8Array.from(atob(s),c=>c.charCodeAt(0));}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function usd(v,d=2){if(v==null||isNaN(v))return "—";const a=Math.abs(v);if(a>=1e6)return "$"+(v/1e6).toFixed(1)+"M";if(a>=1e4)return "$"+(v/1e3).toFixed(1)+"K";return (v<0?"-$":"$")+a.toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d});}
function pct(v){if(v==null)return "—";return (v>=0?"+":"")+Number(v).toFixed(1)+"%";}
function price(v){if(v==null)return "—";return "$"+(v<0.01?Number(v).toPrecision(4):Number(v).toFixed(4));}
function ago(t){if(!t)return "never";const s=Math.max(0,Date.now()/1000-t);if(s<60)return Math.round(s)+"s ago";if(s<3600)return Math.round(s/60)+"m ago";return Math.round(s/3600)+"h ago";}
function hm(t){return new Date(t*1000).toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"});}

async function initKey(){
  const h = location.hash;
  if (h.startsWith("#d=")) {
    const k = h.slice(3).split("&")[0];
    try { if (b64u(k).length === 32) localStorage.setItem(KEYSTORE, k); } catch(e){}
    history.replaceState(null, "", location.pathname + location.search + "#/home");
  }
  const k = localStorage.getItem(KEYSTORE);
  if (!k || FORCE === "locked") return false;
  const raw = b64u(k);
  KEY = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  HKEY = await crypto.subtle.importKey("raw", raw, {name:"HMAC", hash:"SHA-256"}, false, ["sign"]);
  return true;
}
async function fname(name){
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", HKEY, new TextEncoder().encode(name)));
  return Array.from(sig).map(b=>b.toString(16).padStart(2,"0")).join("").slice(0,32) + ".bin";
}
class NoData extends Error {}
async function load(name){
  if (FORCE === "error") throw new Error("forced");
  const r = await fetch("data/" + await fname(name) + "?t=" + Date.now(), {cache:"no-store"});
  if (r.status === 404) throw new NoData(name);
  if (!r.ok) throw new Error("HTTP " + r.status);
  const b = new Uint8Array(await r.arrayBuffer());
  let pt;
  try { pt = await crypto.subtle.decrypt({name:"AES-GCM", iv:b.slice(0,12)}, KEY, b.slice(12)); }
  catch(e){ throw new Error("Can't decrypt. Open the one-time link again."); }
  return JSON.parse(new TextDecoder().decode(pt));
}

/* ---------- states ---------- */
function loading(n=3){view.innerHTML = `<div class="skel" style="height:120px"></div>` + Array(n).fill(`<div class="skel" style="height:64px"></div>`).join("");}
function empty(icon, h, s){view.innerHTML = `<div class="state"><div class="big">${icon}</div><div class="h">${esc(h)}</div><div>${esc(s)}</div></div>`;}
function error(e){view.innerHTML = `<div class="state"><div class="big">⚠︎</div><div class="h">Couldn't load</div><div>${esc(e&&e.message||e)}</div><button class="btn ghost" onclick="route()">Try again</button></div>`;}
function locked(){
  setTitle("Desk"); $("#tabs").classList.add("hidden");
  view.innerHTML = `<div class="state"><div class="big">🔒</div><div class="h">Locked</div><div>Open the one-time link from your desk once on this phone. The key stays on this device.</div></div>`;
}
function setTitle(t, back){
  $("#title").innerHTML = back ? `<button id="back" onclick="history.back()">‹ Back</button>${esc(t)}` : esc(t);
}
function pill(){
  const p = $("#modepill"); if (!STATE) return p.classList.add("hidden");
  p.classList.remove("hidden"); p.textContent = STATE.mode; p.classList.toggle("live", STATE.mode === "LIVE");
}
function toast(t){const d=document.createElement("div");d.className="toast";d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.remove(),1800);}

/* ---------- odometer (600ms, 30ms stagger) ---------- */
function odometer(el, text){
  const prev = el.dataset.v || "";
  el.dataset.v = text;
  el.innerHTML = "";
  [...text].forEach((ch, i) => {
    if (!/[0-9]/.test(ch)) { const s=document.createElement("span"); s.textContent=ch; el.appendChild(s); return; }
    const w = document.createElement("span"); w.className = "odo";
    const col = document.createElement("span");
    col.innerHTML = Array.from({length:10},(_,d)=>`<i>${d}</i>`).join("");
    const from = /[0-9]/.test(prev[i]) ? +prev[i] : 0;
    col.style.transform = `translateY(-${from*1.1}em)`;
    col.style.transitionDelay = (i*30) + "ms";
    w.appendChild(col); el.appendChild(w);
    requestAnimationFrame(()=>requestAnimationFrame(()=>{col.style.transform = `translateY(-${(+ch)*1.1}em)`;}));
  });
}

/* ---------- canvas charts ---------- */
function setupCanvas(c, h){
  const dpr = window.devicePixelRatio || 1, w = c.clientWidth || 358;
  c.width = w*dpr; c.height = h*dpr; c.style.height = h+"px";
  const g = c.getContext("2d"); g.scale(dpr,dpr); return [g,w,h];
}
function lineChart(c, pts, base, marks){
  const [g,w,h] = setupCanvas(c, 180);
  if (!pts || pts.length < 2){ g.fillStyle="#8e8e93"; g.font="14px -apple-system,sans-serif"; g.textAlign="center"; g.fillText("Not enough data yet", w/2, h/2); return; }
  const xs = pts.map(p=>p[0]), ys = pts.map(p=>p[1]);
  const x0=Math.min(...xs), x1=Math.max(...xs); let y0=Math.min(...ys, base??Infinity), y1=Math.max(...ys, base??-Infinity);
  if (y1-y0 < 0.01){y0-=1;y1+=1;}
  const X=t=>(t-x0)/(x1-x0||1)*w, Y=v=>h-8-(v-y0)/(y1-y0)*(h-16);
  const up = ys[ys.length-1] >= (base ?? ys[0]); const col = up ? "#30d158" : "#ff453a";
  if (base!=null){g.strokeStyle="#38383a";g.setLineDash([3,4]);g.beginPath();g.moveTo(0,Y(base));g.lineTo(w,Y(base));g.stroke();g.setLineDash([]);}
  const grad = g.createLinearGradient(0,0,0,h); grad.addColorStop(0, up?"rgba(48,209,88,.25)":"rgba(255,69,58,.25)"); grad.addColorStop(1,"rgba(0,0,0,0)");
  g.beginPath(); pts.forEach((p,i)=> i?g.lineTo(X(p[0]),Y(p[1])):g.moveTo(X(p[0]),Y(p[1])));
  g.strokeStyle=col; g.lineWidth=2; g.lineJoin="round"; g.stroke();
  g.lineTo(X(x1),h); g.lineTo(X(x0),h); g.closePath(); g.fillStyle=grad; g.fill();
  (marks||[]).forEach(m=>{ if(m.t<x0||m.t>x1) return; let j=xs.findIndex(t=>t>=m.t); if(j<0)j=xs.length-1;
    g.fillStyle = m.kind==="buy"?"#0a84ff":m.kind==="sell"?"#30d158":"#ff453a"; g.beginPath(); g.arc(X(m.t),Y(ys[j]),4,0,7); g.fill(); });
}
function candleChart(c, cs, line){
  const [g,w,h] = setupCanvas(c, 220);
  if (!cs || !cs.length){ g.fillStyle="#8e8e93"; g.font="14px -apple-system,sans-serif"; g.textAlign="center"; g.fillText("No price history yet", w/2, h/2); return; }
  let lo=Math.min(...cs.map(k=>k.l)), hi=Math.max(...cs.map(k=>k.h)); if(line){lo=Math.min(lo,line);hi=Math.max(hi,line);}
  if (hi===lo){hi*=1.01;lo*=.99;}
  const bw = w/cs.length, Y=v=>h-6-(v-lo)/(hi-lo)*(h-12);
  cs.forEach((k,i)=>{ const x=i*bw+bw/2, up=k.c>=k.o; g.strokeStyle=g.fillStyle=up?"#30d158":"#ff453a";
    g.beginPath(); g.moveTo(x,Y(k.h)); g.lineTo(x,Y(k.l)); g.lineWidth=1; g.stroke();
    const t=Y(Math.max(k.o,k.c)), b=Y(Math.min(k.o,k.c)); g.fillRect(x-Math.max(1,bw*.35), t, Math.max(2,bw*.7), Math.max(1,b-t)); });
  if (line){ g.strokeStyle="#ff9f0a"; g.setLineDash([4,4]); g.beginPath(); g.moveTo(0,Y(line)); g.lineTo(w,Y(line)); g.stroke(); g.setLineDash([]);
    g.fillStyle="#ff9f0a"; g.font="11px -apple-system,sans-serif"; g.fillText("exit "+price(line), 4, Y(line)-4); }
}

/* ---------- rows ---------- */
function actRow(r){
  const k = r.kind || "", L = k==="buy"?"B":k==="sell"?"S":k==="stop"?"↓":"✕";
  const cls = r.positive===1?"pos":r.positive===0&&k!=="reject"?"neg":"";
  const href = r.mint ? `#/coin/${encodeURIComponent(r.mint)}` : "#/activity";
  return `<a class="row" href="${href}"><div class="ico ${esc(k)}">${L}</div><div class="grow"><div class="t">${esc(r.title)}</div><div class="s">${esc(r.subtitle)} · ${hm(r.ts)}</div></div><div class="v ${cls}">${esc(r.value)}</div></a>`;
}
function agentBody(a){
  const L = a.live || {};
  if (a.name==="scanner") return (L.feed||[]).slice(0,4).map(f=>`<div class="${f.verdict==="pass"?"pos":""}">${esc(f.verdict==="pass"?f.text:"✕ "+f.name+" · "+f.reason)}</div>`).join("") || "Watching migrations";
  if (a.name==="auditor"){ const ls=L.layers||[]; const beam=`<div class="beam">${Array.from({length:8},(_,i)=>`<i class="${ls[i]===true?"ok":ls[i]===false?"red":""}"></i>`).join("")}</div>`;
    return (L.running?`Checking ${esc(L.name)}`:L.last?`${esc(L.last.name)} · ${L.last.passed?"8/8 pass":esc(L.last.reason)}`:"Waiting for a coin") + beam; }
  if (a.name==="analyst") return L.name ? `${esc(L.name)} · score <b>${L.score??"…"}</b>` : "Waiting";
  if (a.name==="executor") return L.name ? `${esc(L.name)} · $${L.size} · ${L.pct}%<div class="bar"><i style="width:${L.progress||0}%"></i></div>` : "No signal yet";
  if (a.name==="risk") return L.guarding ? "Guarding open positions" : (L.last || "Nothing open");
  return "";
}

/* ---------- screens ---------- */
async function home(){
  setTitle("Desk"); if(!STATE) loading();
  let s; try { s = STATE = await load("state"); } catch(e){ if (e instanceof NoData) return empty("◔","Starting up","The desk hasn't published yet. Give it a minute."); return error(e); }
  pill();
  if (FORCE==="empty") { s = {...s, positions:[], recent:[]}; }
  const ch = await load("chart_"+RANGE).catch(()=>null);
  view.innerHTML = `
    <div class="sub">${s.mode==="PAPER"?"Paper balance":"Balance"} · updated ${ago(s.updated)}</div>
    <div class="balance" id="bal"></div>
    <div class="sub"><span class="${s.todayPct>=0?"pos":"neg"}">${pct(s.todayPct)}</span> tonight · ${Number(s.multiplier).toFixed(2)}×</div>
    <canvas id="chart"></canvas>
    <div class="ranges">${["1H","6H","24H","ALL"].map(r=>`<button class="${r===RANGE?"on":""}" data-r="${r}">${r}</button>`).join("")}</div>
    <h2>Open</h2>
    <div class="card" id="open">${s.positions.length? s.positions.map(p=>`<a class="row" href="#/coin/${encodeURIComponent(p.mint)}"><div class="ico buy">${esc((p.symbol||p.name||"?")[0])}</div><div class="grow"><div class="t">${esc(p.name)}</div><div class="s">${usd(p.size)} · exit ${price(p.exitLine)}${p.status==="exiting"?" · tap Sell":""}</div></div><div class="v ${p.pct>=0?"pos":"neg"}">${pct(p.pct)}</div></a>`).join("") : `<div class="small" style="padding:8px 0">Nothing open. Risk is idle.</div>`}</div>
    <h2>Recent</h2>
    <div class="card">${s.recent.length? s.recent.map(actRow).join("") : `<div class="small" style="padding:8px 0">No activity yet tonight.</div>`}</div>
    <div class="small" style="margin:16px 0">Heartbeat ${ago(s.heartbeat)} · published ${ago(s.lastPush)}</div>`;
  const bal = $("#bal"); bal.dataset.v = lastBalance || ""; odometer(bal, usd(s.balance)); lastBalance = usd(s.balance);
  lineChart($("#chart"), ch && ch.points, ch && ch.base, ch && ch.marks);
  view.querySelectorAll(".ranges button").forEach(b=>b.onclick=()=>{RANGE=b.dataset.r; home();});
}
async function agents(){
  setTitle("Agents"); if(!STATE) loading(2);
  let s; try { s = STATE = await load("state"); } catch(e){ if (e instanceof NoData) return empty("◔","No agents yet","The desk hasn't published yet."); return error(e); }
  pill();
  view.innerHTML = `<div class="grid">${s.agents.map(a=>`<a class="agent" href="#/agent/${a.name}"><div class="n"><span class="dot ${a.paused?"off":a.alive?"":"dead"}"></span>${a.name}</div><div class="ct">${a.today}</div><div class="small">${a.paused?"paused":"tonight"}</div><div class="body">${agentBody(a)}</div></a>`).join("")}</div>
  <div class="small" style="margin:14px 0">Pause or resume an agent in Telegram: <b>pause scanner</b></div>`;
}
async function agent(name){
  setTitle(name[0].toUpperCase()+name.slice(1), true); loading();
  let a; try { a = await load("agents_"+name); } catch(e){ return error(e); }
  if (FORCE==="empty") a.decisions = [];
  view.innerHTML = `<div class="card"><div class="kv"><span>Status</span><span>${a.paused?"Paused":"Running"}</span></div><div class="kv"><span>Heartbeat</span><span>${ago(a.heartbeat&&a.heartbeat.ts)}</span></div><div class="kv"><span>Tonight</span><span>${a.today} decisions</span></div></div>
  <h2>Decisions</h2><div class="card">${a.decisions.length? a.decisions.map(d=>`<a class="row" href="${d.mint?"#/coin/"+encodeURIComponent(d.mint):"#"}"><div class="grow"><div class="t">${esc(d.name||"")}</div><div class="s">${esc(d.reason||"")} · ${hm(d.ts)}</div></div><div class="v ${/pass|bought|trail/.test(d.verdict)?"pos":"neg"}">${esc(d.verdict)}</div></a>`).join("") : `<div class="small" style="padding:8px 0">No decisions yet.</div>`}</div>
  <h2>Settings</h2><div class="card">${Object.entries(a.settings||{}).map(([k,v])=>`<div class="kv"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>`;
}
let actPage = 0, actRows = [];
async function activity(){
  setTitle("Activity"); loading(5); actPage = 0; actRows = [];
  let a; try { a = await load("activity_0"); STATE = STATE || await load("state"); } catch(e){ if (e instanceof NoData) return empty("☰","No activity yet","Buys, sells and rejections show up here."); return error(e); }
  pill();
  if (FORCE==="empty") a = {rows:[], next:null};
  actRows = a.rows; renderAct(a.next);
}
function renderAct(next){
  const coins = (STATE && STATE.coins) || [];
  view.innerHTML = `<input class="search" id="q" placeholder="Search name, symbol or address" autocomplete="off"><div id="hits"></div>
  ${actRows.length? `<div class="card">${actRows.map(actRow).join("")}</div>${next!=null?`<button class="btn ghost" id="more">Load more</button>`:""}` : `<div class="state"><div class="big">☰</div><div class="h">No activity yet</div><div>Buys, sells and rejections show up here.</div></div>`}`;
  const m = $("#more"); if (m) m.onclick = async ()=>{ m.textContent="Loading…"; try{ const a=await load("activity_"+next); actRows=actRows.concat(a.rows); renderAct(a.next);}catch(e){m.textContent="Couldn't load · try again";} };
  $("#q").oninput = e => { const q=e.target.value.trim().toLowerCase(); $("#hits").innerHTML = !q ? "" :
    `<div class="card">${coins.filter(c=>(c.name||"").toLowerCase().includes(q)||(c.symbol||"").toLowerCase().includes(q)||c.mint.toLowerCase()===q).slice(0,8).map(c=>`<a class="row" href="#/coin/${encodeURIComponent(c.mint)}"><div class="grow"><div class="t">${esc(c.name)}</div><div class="s">${esc(c.symbol)} · ${esc(c.mint.slice(0,4)+"…"+c.mint.slice(-4))}</div></div></a>`).join("")||`<div class="small" style="padding:8px 0">No coin matches.</div>`}</div>`; };
}
async function account(){
  setTitle("Account"); loading(4);
  let a, st; try { a = await load("account"); st = await load("settings"); } catch(e){ if (e instanceof NoData) return empty("◔","Not ready","The desk hasn't published yet."); return error(e); }
  view.innerHTML = `<div class="card">
    <div class="kv"><span>Mode</span><span>${esc(a.mode)}</span></div>
    <div class="kv"><span>${a.mode==="PAPER"?"Paper start":"Stack"}</span><span>${usd(a.mode==="PAPER"?a.paperStart:a.stack)}</span></div>
    <div class="kv"><span>Balance</span><span>${usd(a.balance)}</span></div>
    <div class="kv"><span>FOMO</span><span><a class="link" href="${esc(a.profileUrl)}" target="_blank" rel="noopener">@${esc(a.fomoHandle)}</a></span></div>
    <div class="kv"><span>Alerts</span><span>${a.telegram?"Telegram · connected":"Telegram · not set"}</span></div>
    <div class="kv"><span>Web push</span><span>${esc(a.webPush)}</span></div>
    <div class="kv"><span>Analyst</span><span>${esc(a.analystMode)}</span></div>
    <div class="kv"><span>Fee</span><span>${esc(a.feePct)}% per side</span></div></div>
    <button class="btn" id="invite">Invite a friend to FOMO</button>
    <h2>Settings</h2><div class="small">Read-only here. Change in Telegram: <b>set risk.stop_pct 5</b></div>
    ${Object.entries(st.settings).map(([g,kv])=>`<div class="card"><div class="small" style="text-transform:uppercase;letter-spacing:.5px">${esc(g)}</div>${Object.entries(kv).filter(([k])=>k!=="rpc_url").map(([k,v])=>`<div class="kv"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>`).join("")}
    <h2>Changes</h2><div class="card">${st.changes.length? st.changes.map(c=>`<div class="kv"><span>${esc(c.key)}</span><span>${esc(c.old)} → ${esc(c.new)}</span></div>`).join("") : `<div class="small" style="padding:8px 0">No changes yet.</div>`}</div>
    <button class="btn ghost" id="forget">Forget key on this phone</button>`;
  $("#invite").onclick = async ()=>{ const url=a.inviteUrl; try{ if(navigator.share){await navigator.share({title:"FOMO",url});return;} }catch(e){ if(e.name==="AbortError")return; }
    try{ await navigator.clipboard.writeText(url); toast("Link copied"); }catch(e){ prompt("Copy this link", url); } };
  $("#forget").onclick = ()=>{ if(confirm("Remove the key from this phone?")){ localStorage.removeItem(KEYSTORE); location.reload(); } };
}
async function coin(mint){
  setTitle("Coin", true); loading();
  let c; try { c = await load("coin_"+mint); } catch(e){ if (e instanceof NoData) return empty("◌","Coin not published","Only coins from the last day are kept."); return error(e); }
  setTitle(c.name || "Coin", true);
  const p = c.position, open = p && (p.status==="open"||p.status==="exiting");
  if (FORCE==="empty") c.candles = [];
  view.innerHTML = `<div class="sub">${esc(c.symbol)} · Solana · ${c.ageH}h old</div>
    <h1>${price(c.price)}</h1>
    ${p?`<div class="sub ${p.sinceEntryPct>=0?"pos":"neg"}">${pct(open?p.sinceEntryPct:p.pct)} ${open?"since entry":"· "+esc(p.exit_reason||"closed")}</div>`:""}
    <canvas id="cc"></canvas>
    <div class="stats"><div class="small">Market cap<b>${usd(c.stats.mcap,0)}</b></div><div class="small">Liquidity<b>${usd(c.stats.liq,0)}</b></div><div class="small">Volume 24h<b>${usd(c.stats.vol24,0)}</b></div><div class="small">Holders<b>${c.stats.holders??"—"}</b></div></div>
    ${p?`<div class="card"><div class="kv"><span>Size</span><span>${usd(p.size)}</span></div><div class="kv"><span>Entry</span><span>${price(p.entry)}</span></div>
      ${open?`<div class="kv"><span>Exit line</span><span>${price(p.exitLine)} · ${p.trail_active?"trailing "+p.trail_pct+"%":"stop -"+p.stop_pct+"%"}</span></div><div class="kv"><span>Time left</span><span>${Math.floor(p.timeLeftS/3600)}h ${Math.floor(p.timeLeftS%3600/60)}m</span></div>`
            :`<div class="kv"><span>Exit</span><span>${price(p.exit_price)}</span></div><div class="kv"><span>P&amp;L</span><span class="${p.pnl>=0?"pos":"neg"}">${usd(p.pnl)} after ${usd(p.fees)} fees</span></div>`}</div>`:""}
    ${c.audit?`<a class="card row" href="#/audit/${encodeURIComponent(mint)}" style="padding:14px 16px"><div class="grow"><div class="t">Audit</div><div class="s">${c.audit.passed?"8/8 layers passed":"stopped at layer "+c.audit.checked}</div></div><div class="v">›</div></a>`:""}
    <a class="btn ${open?"red":"green"}" href="${esc(c.link)}" target="_blank" rel="noopener">${open?"Sell on FOMO":"Open on FOMO"}</a>`;
  candleChart($("#cc"), c.candles, open ? p.exitLine : null);
}
async function audit(mint){
  setTitle("Auditor", true); loading(8);
  let a; try { a = await load("audit_"+mint); } catch(e){ if (e instanceof NoData) return empty("◌","No audit","This coin never reached the Auditor."); return error(e); }
  view.innerHTML = `<div class="sub">${esc(a.symbol)}</div><h1>${esc(a.name)}</h1>
    <div class="card">${LAYERS.map((n,i)=>{const l=a.layers[i]; return `<div class="layer" data-i="${i}" data-s="${l?(l.pass?"ok":"red"):""}"><div class="num">${i+1}</div><div class="grow"><div class="t">${n}</div><div class="small">${l?esc(l.source):"not checked"}</div></div><div class="val">${l?esc(l.value):"—"}</div></div>`;}).join("")}</div>
    <div class="card"><div class="t" style="font-weight:600">${a.passed?"Passed all 8 layers":"Rejected · "+esc(a.reason)}</div><div class="small">${a.holders!=null?a.holders+" holders · ":""}${esc(a.ts)}</div></div>
    <a class="btn ghost" href="${esc(a.link)}" target="_blank" rel="noopener">Open on FOMO</a>`;
  view.querySelectorAll(".layer").forEach((el,i)=> setTimeout(()=>{ el.classList.add("shown"); if(el.dataset.s) el.classList.add(el.dataset.s); }, 350*(i+1)));
}

/* ---------- router ---------- */
async function route(){
  if (!KEY) return locked();
  $("#tabs").classList.remove("hidden");
  const parts = (location.hash.replace(/^#\/?/,"") || "home").split("/");
  const tab = ["agent"].includes(parts[0]) ? "agents" : ["coin","audit"].includes(parts[0]) ? "" : parts[0];
  document.querySelectorAll("#tabs a").forEach(a=>a.classList.toggle("on", a.dataset.tab===tab));
  if (FORCE==="loading") { setTitle(parts[0]); return loading(); }
  window.scrollTo(0,0);
  const m = decodeURIComponent(parts[1]||"");
  switch(parts[0]){
    case "agents": return agents();
    case "agent": return agent(m);
    case "activity": return activity();
    case "account": return account();
    case "coin": return coin(m);
    case "audit": return audit(m);
    default: return home();
  }
}
window.addEventListener("hashchange", route);
(async () => {
  const ok = await initKey().catch(()=>false);
  route();
  if (ok && !FORCE) timer = setInterval(()=>{ const h=location.hash; if (h==="#/home"||h===""||h==="#/agents") route(); }, 15000);
})();
