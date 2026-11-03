// ULTRON floor view — plain JS + canvas + inline SVG, no dependencies.
// Floor: live paper floor in plain English. Research: the original backtest replay & stats.
"use strict";
const FAM = {donchian_breakout:["#ff4d5e","Br","Breakout"], bollinger_reversion:["#4da3ff","Sn","Snapback"],
  ema_trend_cross:["#25d695","TR","Trend Rider"], session_orb:["#f5c84c","OR","Opening Range"], rsi_pullback:["#b286ff","DB","Dip Buyer"],
  vwap_reversion:["#38d6e0","FP","Fair-Price Snapback"], momentum_burst:["#ff8a3d","MB","Momentum Burst"], ema_pullback:["#9be15d","TP","Trend Pullback"],
  lvl_retest:["#e06cff","LR","Level Retest"], lvl_sweep:["#ff5fa2","SF","Sweep Fader"], lvl_pullback:["#7ee0ff","LP","Level Pullback"], indicator_combo:["#ffc94d","Cx","Combo"]};
const fam = f => FAM[f] || ["#8892a0", "?", f || "Bot"];
const ASSET = {XAUUSD:"Gold", BTCUSD:"Bitcoin"};
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
let VIEW = "floor", FLOOR = location.hash.includes("scalp") ? "scalp" : "main", STATE = null, RESULTS = null, LEARNED = null, REPLAY = null;
let SYM = "XAUUSD", frameI = 0, replayTimer = null, cardFilter = "all", boardAll = false;
const openRules = new Set(), lastWeights = {};

// ---------------- formatting ----------------
const fmt = (x, d=2) => x==null || isNaN(x) ? "—" : (+x).toFixed(d);
const N = x => (+(x||0)).toLocaleString();
const usd = (x, sign=true) => { if (x==null || isNaN(x)) return "—"; const s = x<0 ? "−" : (sign && x>0 ? "+" : "");
  return s + "$" + Math.abs(x).toLocaleString(undefined,{minimumFractionDigits:2, maximumFractionDigits:2}); };
const usd0 = x => (x<0?"−":"") + "$" + Math.abs(x||0).toLocaleString(undefined,{maximumFractionDigits:0});
const rr2 = x => (x>0?"+":x<0?"−":"") + fmt(Math.abs(x||0),1) + "R";
const cls = x => x>0 ? "pos" : x<0 ? "neg" : "";
const esc = s => String(s??"").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ago = ts => { const s = Math.max(0, Date.now()/1000 - ts); return s<90 ? `${s|0}s` : s<5400 ? `${(s/60)|0}m` : s<172800 ? `${(s/3600).toFixed(1)}h` : `${(s/86400).toFixed(0)}d`; };
const TZ = "Australia/Sydney";
const sydT = (ts, sec) => new Date(ts*1000).toLocaleString("en-AU",{timeZone:TZ, day:"numeric", month:"short", hour:"numeric", minute:"2-digit", ...(sec?{second:"2-digit"}:{})});
const sydS = s => new Date(String(s).replace(/(\d\d)(\d\d)$/,"$1:$2")).toLocaleString("en-AU",{timeZone:TZ, day:"numeric", month:"short", hour:"numeric", minute:"2-digit"});
const px = (sym, x) => x==null ? "—" : (+x).toLocaleString(undefined, {minimumFractionDigits: sym==="XAUUSD"?2:1, maximumFractionDigits: sym==="XAUUSD"?2:1});
const dur = s => { s = Math.max(0, s|0); const d = (s/86400)|0, h = ((s%86400)/3600)|0, m = ((s%3600)/60)|0; return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${s%60}s`; };
const floorTitle = () => FLOOR==="scalp" ? "Scalp Pit · experimental" : "Main Floor";

// ---------------- theme (auto / dark / light) ----------------
let TK = {};
function tokens(){ const cs = getComputedStyle(document.documentElement); const g = n => cs.getPropertyValue(n).trim();
  TK = {bg:g("--canvas"), grid:g("--grid"), line:g("--line"), line2:g("--line2"), tx:g("--tx"), tx2:g("--tx2"), mut:g("--mut"), card:g("--card"), card2:g("--card2"),
        grn:g("--grn"), neg:g("--neg"), gold:g("--gold"), blu:g("--blu"), accent:g("--accent"), red:g("--red"), org:g("--org")}; }
function setTheme(t){ const r = document.documentElement; if (t==="auto") delete r.dataset.theme; else r.dataset.theme = t;
  try { localStorage.setItem("ultron-theme", t); } catch(e){}
  const ic = {auto:'<path d="M8 1.5a6.5 6.5 0 0 0 0 13z" fill="currentColor"/><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.4"/>',
              dark:'<path d="M13.5 9.6A6 6 0 0 1 6.4 2.5a6 6 0 1 0 7.1 7.1z" fill="currentColor"/>',
              light:'<circle cx="8" cy="8" r="3.2" fill="currentColor"/><g stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9 13 13M3 13l1.1-1.1M11.9 4.1 13 3"/></g>'}[t];
  $("#theme").title = "Theme: " + t + " (click to change)"; $("#theme").innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">${ic}</svg>`; tokens(); redrawStatic(); }
$("#theme").onclick = () => { const cur = document.documentElement.dataset.theme || "auto"; setTheme({auto:"dark", dark:"light", light:"auto"}[cur]); };
matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => { tokens(); redrawStatic(); });

// ---------------- canvas helpers ----------------
function fit(cv){ const r = cv.getBoundingClientRect(), d = devicePixelRatio || 1, w = Math.max(1, Math.round(r.width*d)), h = Math.max(1, Math.round(r.height*d));
  if (cv.width !== w || cv.height !== h){ cv.width = w; cv.height = h; }        // only re-allocate the bitmap when the size changes
  const c = cv.getContext("2d"); c.setTransform(d,0,0,d,0,0); c.clearRect(0,0,r.width,r.height); return [c, r.width, r.height]; }
function rrect(c,x,y,w,h,r){ c.beginPath(); c.moveTo(x+r,y); c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r); c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath(); }
function clip(c, s, x, y, mw){ s = String(s); if (c.measureText(s).width <= mw){ c.fillText(s,x,y); return; } while (s.length>1 && c.measureText(s+"…").width>mw) s = s.slice(0,-1); c.fillText(s+"…",x,y); }
const easeOut = t => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
const FONT = "system-ui,-apple-system,Segoe UI,Roboto,sans-serif";

// sparkline of cumulative R (inline SVG)
function spark(rs, w=120, h=34){
  if (!rs || rs.length < 2) return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><line x1="0" y1="${h/2}" x2="${w}" y2="${h/2}" stroke="var(--line2)" stroke-dasharray="3 3"/></svg>`;
  let c = 0; const pts = [0, ...rs.map(r => c += r)], lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), sp = (hi - lo) || 1;
  const X = i => (i / (pts.length - 1)) * w, Y = v => h - 3 - (h - 6) * (v - lo) / sp;
  const d = pts.map((v,i) => `${i?"L":"M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const col = c >= 0 ? "var(--grn)" : "var(--neg)";
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><line x1="0" y1="${Y(0)}" x2="${w}" y2="${Y(0)}" stroke="var(--line2)" stroke-dasharray="3 3"/>
    <path d="${d}L${w},${Y(0)}L0,${Y(0)}Z" fill="${col}" opacity=".12"/><path d="${d}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

// ---------------- animated floor canvas (shared by live + replay) ----------------
function mkFloor(id){ return {id, desks:new Map(), particles:[]}; }
const LIVE = mkFloor("floor"), REP = mkFloor("rfloor");
function layout(n, W, H){ const cols = W < 560 ? 2 : W < 900 ? 3 : 4, rows = Math.max(2, Math.ceil(n/cols)); const top = 64, cw = W/cols, ch = (H-top-8)/rows;
  return {cw, ch, pos: i => [cw*(i%cols) + cw/2, top + ch*Math.floor(i/cols) + ch/2]}; }
function setDesks(F, list){
  const now = performance.now(), ids = new Set(list.map(d=>d.id));
  for (const [id,d] of F.desks) if (!ids.has(id) && !d.dying){ d.dying = now; d.killed = !!d.willDie; }
  const used = new Set([...F.desks.values()].filter(d=>!d.dying).map(d=>d.slot));
  for (const d of list){ let e = F.desks.get(d.id);
    if (!e || e.dying){ let s = 0; while (used.has(s)) s++; used.add(s); e = {slot:s, born:now}; F.desks.set(d.id, e); }
    Object.assign(e, d); }
}
function flow(F, toId, amount){ if (!F.desks.get(toId)) return; for (let i=0; i<Math.min(14, 2+amount*40); i++) F.particles.push({to:toId, t:-i*0.06, sp:0.6+Math.random()*0.5}); }
function drawFloor(F){
  const cv = document.getElementById(F.id); if (!cv || !cv.offsetParent) return;
  {   // height follows the number of desks so they never overlap (rows of >= 62px on phones, 96px on desktop)
    const Wc = cv.clientWidth, cols = Wc < 560 ? 2 : Wc < 900 ? 3 : 4, n = Math.max([...F.desks.values()].filter(d=>!d.dying).length, 6);
    const want = Math.round(Math.max(Wc * 0.42, 72 + Math.ceil(n/cols) * (Wc < 560 ? 62 : 96)));
    if (cv.style.height !== want + "px"){ cv.style.aspectRatio = "auto"; cv.style.height = want + "px"; }
  }
  const [c, W, H] = fit(cv), now = performance.now(), narrow = W < 560;
  const live = [...F.desks.values()].filter(d=>!d.dying), L = layout(Math.max(live.length, 6), W, H);
  c.fillStyle = TK.bg; c.fillRect(0,0,W,H);
  c.fillStyle = TK.grid; for (let x=12; x<W; x+=24) for (let y=12; y<H; y+=24) c.fillRect(x, y, 1.4, 1.4);     // dot grid
  const ax = W/2, ay = 30, pulse = 1 + 0.07*Math.sin(now/320);
  const g = c.createRadialGradient(ax,ay,2,ax,ay,34*pulse); g.addColorStop(0, TK.accent); g.addColorStop(1, "rgba(255,45,70,0)");
  c.fillStyle = g; c.beginPath(); c.arc(ax,ay,34*pulse,0,7); c.fill();
  c.strokeStyle = TK.accent; c.lineWidth = 2; c.setLineDash([22, 9]); c.lineDashOffset = -now/40; c.beginPath(); c.arc(ax,ay,19,0,7); c.stroke(); c.setLineDash([]);
  c.fillStyle = "#fff"; c.font = `900 9px ${FONT}`; c.textAlign = "center"; c.fillText("ULTRON", ax, ay+3);
  for (const [id,d] of F.desks){
    const [x,y] = L.pos(d.slot); d.x = x; d.y = y; let a = 1, sc = 1;
    const age = (now-d.born)/600; if (age < 1) sc = 0.5 + 0.5*easeOut(age);
    if (d.dying){ const k = (now-d.dying)/900; if (k > 1){ F.desks.delete(id); continue; } a = 1-k; sc = 1-0.3*k; }
    const w = Math.min(L.cw-14, 250), h = Math.min(L.ch-12, 86), f = fam(d.fam);
    c.save(); c.globalAlpha = a; c.translate(x,y); c.scale(sc,sc);
    c.shadowColor = "rgba(0,0,0,.25)"; c.shadowBlur = 10; c.shadowOffsetY = 3;
    c.fillStyle = TK.card; rrect(c,-w/2,-h/2,w,h,12); c.fill(); c.shadowColor = "transparent";
    c.strokeStyle = d.pos ? (d.pos.dir>0 ? TK.grn : TK.neg) : TK.line2; c.lineWidth = d.pos ? 2 : 1; rrect(c,-w/2,-h/2,w,h,12); c.stroke();
    c.fillStyle = f[0]; rrect(c,-w/2,-h/2,4,h,2); c.fill();
    const r = Math.min(narrow ? 11 : 14, h/4), cx = -w/2+12+r, cy = -h/2+10+r;
    c.globalAlpha = a*0.2; c.fillStyle = f[0]; rrect(c, cx-r, cy-r, 2*r, 2*r, 7); c.fill(); c.globalAlpha = a;
    c.fillStyle = f[0]; c.font = `800 ${r*0.82}px ${FONT}`; c.textAlign = "center"; c.fillText(f[1], cx, cy+r*0.3);
    c.textAlign = "left"; c.fillStyle = TK.tx; c.font = `650 ${narrow?10.5:12}px ${FONT}`; clip(c, d.title || `#${id}`, cx+r+8, -h/2+19, w-2*r-30);
    if (h >= 60){ c.fillStyle = TK.mut; c.font = `${narrow?9.5:10.5}px ${FONT}`; clip(c, d.label || "", cx+r+8, -h/2+33, w-2*r-30); }
    c.font = `750 ${narrow?12:13.5}px ${FONT}`; c.fillStyle = d.pnl>0 ? TK.grn : d.pnl<0 ? TK.neg : TK.tx2; c.fillText(d.pnlTxt || "", -w/2+12, h/2-11);
    if (d.pos){ c.fillStyle = d.pos.dir>0 ? TK.grn : TK.neg; c.font = `800 10.5px ${FONT}`; c.textAlign = "right"; c.fillText(d.pos.dir>0 ? "▲ BUY" : "▼ SELL", w/2-10, h/2-11); }
    if (d.w){ c.fillStyle = TK.card2; c.fillRect(-w/2+12, h/2-6, w-24, 2.5); c.fillStyle = TK.gold; c.fillRect(-w/2+12, h/2-6, (w-24)*Math.min(1, d.w/0.25), 2.5); }
    if (age < 1.6 && !d.dying){ const k = 3 + age*8; c.strokeStyle = TK.grn; c.globalAlpha = a*(1-age/1.6); c.lineWidth = 2.5; rrect(c,-w/2-k,-h/2-k,w+2*k,h+2*k,14); c.stroke();
      c.globalAlpha = a; c.fillStyle = TK.grn; c.font = `800 10.5px ${FONT}`; c.textAlign = "center"; c.fillText("✦ BORN", 0, -h/2-5); }
    if (d.dying && d.killed){ c.strokeStyle = TK.neg; c.lineWidth = 4; c.lineCap = "round"; c.beginPath(); c.moveTo(-w/4,-h/3); c.lineTo(w/4,h/3); c.moveTo(w/4,-h/3); c.lineTo(-w/4,h/3); c.stroke(); }
    c.restore();
  }
  for (let i=F.particles.length-1; i>=0; i--){ const p = F.particles[i]; p.t += 0.016*p.sp; const d = F.desks.get(p.to);
    if (!d || p.t > 1){ F.particles.splice(i,1); continue; } if (p.t < 0) continue;
    const t = easeOut(p.t), x = ax+(d.x-ax)*t, y = ay+(d.y-ay)*t - Math.sin(t*Math.PI)*34;
    c.fillStyle = TK.gold; c.beginPath(); c.arc(x,y,2.6,0,7); c.fill(); }
}
let lastFrame = 0;
function loop(t){ if (t - lastFrame > 30 && document.visibilityState !== "hidden"){ lastFrame = t; drawFloor(LIVE); drawFloor(REP); } requestAnimationFrame(loop); }

// ---------------- equity chart ----------------
let EQ = null;      // {pts, X, Y, W, H}
function drawEquity(){
  const cv = $("#eqchart"); if (!cv || !cv.offsetParent) return;
  const [c, W, H] = fit(cv); const pts = (STATE && STATE.curve) || [];
  const start = (STATE && STATE.summary.paper_account_usd) || 10000;
  if (pts.length < 2){ c.fillStyle = TK.mut; c.font = `13px ${FONT}`; c.textAlign = "center";
    c.fillText(STATE ? "The equity curve appears after the first closed paper trades" : "Loading…", W/2, H/2); EQ = null; $("#eq-range").textContent = ""; return; }
  const now = STATE.now, last = pts[pts.length-1][1] + (STATE.summary.open_usd || 0);
  const P = [...pts, [now, last]], t0 = P[0][0], t1 = Math.max(now, P[P.length-1][0]);
  const vs = P.map(p=>p[1]), lo = Math.min(start, ...vs), hi = Math.max(start, ...vs), pad = (hi-lo)*0.12 || 50;
  const L = 52, R = 10, T = 8, B = 20, X = t => L + (W-L-R)*(t-t0)/((t1-t0)||1), Y = v => T + (H-T-B)*(1 - (v-(lo-pad))/((hi+pad)-(lo-pad)));
  c.font = `10.5px ${FONT}`; c.fillStyle = TK.mut; c.strokeStyle = TK.grid; c.lineWidth = 1; c.textAlign = "right";
  for (let k=0; k<=3; k++){ const v = lo-pad + (hi-lo+2*pad)*k/3, y = Y(v); c.beginPath(); c.moveTo(L,y); c.lineTo(W-R,y); c.stroke(); c.fillText(usd0(v), L-6, y+3); }
  c.strokeStyle = TK.line2; c.setLineDash([4,4]); c.beginPath(); c.moveTo(L, Y(start)); c.lineTo(W-R, Y(start)); c.stroke(); c.setLineDash([]);
  const up = last >= start, col = up ? TK.grn : TK.neg;
  const gr = c.createLinearGradient(0, T, 0, H-B); gr.addColorStop(0, up ? "rgba(37,214,149,.28)" : "rgba(255,77,94,.28)"); gr.addColorStop(1, "rgba(0,0,0,0)");
  c.beginPath(); P.forEach((p,i) => i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))); c.lineTo(X(P[P.length-1][0]), H-B); c.lineTo(X(t0), H-B); c.closePath(); c.fillStyle = gr; c.fill();
  c.beginPath(); P.forEach((p,i) => i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))); c.strokeStyle = col; c.lineWidth = 2; c.lineJoin = "round"; c.stroke();
  const lx = X(P[P.length-1][0]), ly = Y(last); c.fillStyle = col; c.beginPath(); c.arc(lx, ly, 3.5, 0, 7); c.fill();
  c.textAlign = "left"; c.fillStyle = TK.mut; const fd = t => new Date(t*1000).toLocaleDateString("en-AU",{timeZone:TZ, day:"numeric", month:"short"});
  c.fillText(fd(t0), L, H-5); c.textAlign = "right"; c.fillText("now", W-R, H-5);
  $("#eq-range").innerHTML = `since ${fd(t0)}<br>high ${usd0(Math.max(...vs))} · low ${usd0(Math.min(...vs))}`;
  EQ = {P, X, Y, W, H, L, R, T, B};
  if (EQ.hover != null) hoverEq(EQ.hover);
}
function hoverEq(mx){
  const tip = $("#eqtip"); if (!EQ){ tip.hidden = true; return; }
  const {P, X, Y} = EQ; let best = 0; for (let i=1; i<P.length; i++) if (Math.abs(X(P[i][0])-mx) < Math.abs(X(P[best][0])-mx)) best = i;
  const p = P[best], x = X(p[0]), y = Y(p[1]); const c = $("#eqchart").getContext("2d");
  c.strokeStyle = TK.mut; c.setLineDash([3,3]); c.beginPath(); c.moveTo(x, EQ.T); c.lineTo(x, EQ.H-EQ.B); c.stroke(); c.setLineDash([]);
  c.fillStyle = TK.tx; c.beginPath(); c.arc(x, y, 3.5, 0, 7); c.fill();
  tip.hidden = false; tip.innerHTML = `<b>${usd(p[1], false)}</b> <span class="${cls(p[1]-10000)}">${usd(p[1]-10000)}</span><br><span class="mut">${best === P.length-1 ? "now (incl. open trades)" : sydT(p[0])}</span>`;
  tip.style.left = Math.min(Math.max(0, x - tip.offsetWidth/2), EQ.W - tip.offsetWidth) + "px"; tip.style.top = Math.max(0, y - tip.offsetHeight - 12) + "px";
}
$("#eqchart").addEventListener("pointermove", e => { if (!EQ) return; const r = e.target.getBoundingClientRect(); EQ.hover = e.clientX - r.left; drawEquity(); });
$("#eqchart").addEventListener("pointerleave", () => { if (EQ) EQ.hover = null; $("#eqtip").hidden = true; drawEquity(); });

// ---------------- main floor view ----------------
function indRow(b){ return `<div class="inds">${b.indicators?`<span class="ind">${esc(b.indicators)}</span>`:""}${b.rr!=null?`<span class="ind rr${b.rr<2?" off":""}" title="${b.rr<2?"older bot, tested before the 1:2 reward:risk rule":"take profit vs stop distance"}">R:R 1:${fmt(b.rr,1)}${b.rr<2?" · older bot":""}</span>`:""}</div>`; }
function renderMain(){
  if (!STATE) return; const S = STATE.summary, bots = STATE.bots, br = S.breeder;
  const fresh = Object.values(STATE.ticks).some(t => STATE.now - t.ts < 300);
  // status strip
  const top = br && br.top_reject ? ` · top reject: <b>${esc(br.top_reject)}</b>${br.top_reject_share!=null?` (${br.top_reject_share}%)`:""}` : "";
  const breed = br ? (br.running ? `<span><b>Breeding</b> · ${N(br.tested_last_hour)} tested in the last hour${top}</span><span class="mut">${N(br.tested_today)} tested today · ${N(br.promoted_today)} promoted · ${N(br.cut_today)} cut · generation ${N(br.generation)}</span>`
    : `<span class="mut">Breeder restarting…</span>`) : `<span class="mut">Breeder starting…</span>`;
  const stale = STATE.stale, hbm = STATE.heartbeat_age_s==null ? "never" : `${Math.round(STATE.heartbeat_age_s/60)} min ago`;
  const head = stale ? `<b class="neg">⚠ Floor data is stale — the live trader's last heartbeat was ${hbm}</b>` : `<b>${fresh ? "Paper trading live" : "Markets quiet"}</b>`;
  $("#status").innerHTML = `<span><span class="dot${fresh && !stale ? "" : " off"}"></span>${head} · ${S.bots_alive} bots · ${S.open_trades} in a trade</span>${breed}`;
  // hero
  const eq = (S.paper_account_usd||10000) + S.paper_profit_usd;
  $("#n-equity").textContent = usd(eq, false); $("#n-equity").className = "huge";
  const pe = $("#n-profit"); pe.textContent = usd(S.paper_profit_usd); pe.className = "chip " + cls(S.paper_profit_usd);
  $("#n-today").innerHTML = `today <b class="${cls(S.profit_today_usd)}">${usd(S.profit_today_usd)}</b> closed${S.open_usd?` · open <b class="${cls(S.open_usd)}">${usd(S.open_usd)}</b>`:""}`;
  $("#n-bots").textContent = S.bots_alive; $("#n-bots-sub").textContent = br ? `${N(br.promoted_today)} new · ${N(br.cut_today)} cut today` : " ";
  $("#n-open").textContent = S.open_trades; $("#n-open-sub").textContent = S.risk ? `of ${S.risk.max_open_trades} allowed` : " ";
  $("#n-trades").textContent = N(S.trades_today); $("#n-trades-sub").textContent = `${N(S.trades_total)} all-time · Sydney day`;
  $("#n-win").textContent = S.trades_total ? fmt(100*S.wins_total/S.trades_total, 0) + "%" : "–"; $("#n-win-sub").textContent = S.trades_total ? `${N(S.wins_total)} won of ${N(S.trades_total)}` : "no closed trades yet";
  const tph = S.trades_per_hour_expected; $("#n-tph").textContent = tph==null ? "–" : (tph < 10 ? tph.toFixed(1) : Math.round(tph));
  $("#n-tph-sub").textContent = `expected · actual ${fmt(S.trades_per_hour_actual,1)}/h (24h)`;
  $("#n-cost").textContent = usd0(S.costs_paid_usd); $("#n-cost-sub").textContent = `${usd0(S.costs_paid_today_usd)} today · before costs ${usd0(S.gross_before_costs_usd)}`;
  drawEquity();
  // risk
  const R = S.risk;
  if (R){ const used = R.open_risk_pct, cap = R.max_total_open_risk_pct, dl = R.daily_floor_loss_limit_pct, lossUsed = Math.max(0, -R.day_pnl_pct);
    $("#risk").innerHTML = `<div class="ct"><h2>Floor risk manager</h2><span class="mut small">every entry must pass these limits${R.fresh?"":" · <b class='neg'>live floor not reporting</b>"}</span></div>
      <div class="riskgrid">
        <div class="meter"><div class="top2"><span>Risk in use</span><b>${fmt(used,1)}% <span class="mut">of ${fmt(cap,0)}%</span></b></div><div class="bar"><i style="width:${Math.min(100, used/cap*100)}%"></i></div>
          <div class="mut small">equity ${usd(R.equity_usd,false)} · ${fmt(R.risk_per_trade.min_pct,2)}–${fmt(R.risk_per_trade.max_pct,1)}% per trade</div></div>
        <div class="meter"><div class="top2"><span>Today's floor result</span><b class="${cls(R.day_pnl_pct)}">${R.day_pnl_pct>=0?"+":""}${fmt(R.day_pnl_pct,2)}%</b></div><div class="bar loss"><i style="width:${Math.min(100, lossUsed/dl*100)}%"></i></div>
          <div class="mut small">new trades stop for the day at −${fmt(dl,0)}%${R.max_floor_drawdown_pct!=null?` · floor kill at −${fmt(R.max_floor_drawdown_pct,0)}% from peak`:""}</div></div>
        <div class="meter"><div class="top2"><span>Open trades</span><b>${R.open_trades} <span class="mut">of ${R.max_open_trades}</span></b></div><div class="bar slots"><i style="width:${Math.min(100, R.open_trades/R.max_open_trades*100)}%"></i></div>
          <div class="mut small">max ${R.max_same_direction_per_symbol} the same way per market · max ${R.max_bots_on_floor} bots${R.blocked_today?` · ${N(R.blocked_today)} signals blocked today`:""}</div></div>
      </div>${R.dd_halted?`<div class="halt">⛔ Floor drawdown kill active — all trades closed, no new entries until reset</div>`:R.halted?`<div class="halt">⛔ Daily floor loss limit hit — no new trades until tomorrow (Sydney time)</div>`:""}`; }
  else $("#risk").innerHTML = `<div class="ct"><h2>Floor risk manager</h2></div><div class="mut">Waiting for the live floor to report its risk state…</div>`;
  // floor canvas
  const killed = new Set((STATE.killed||[]).map(b => b.bot_id)); LIVE.desks.forEach((e,id) => e.willDie = killed.has(id));
  const narrow = innerWidth < 560;
  setDesks(LIVE, bots.map(b => { const tot = b.profit_usd + (b.unreal_usd||0); return {id:b.bot_id, fam:b.family, w:b.weight,
    title: narrow ? b.name.replace(/^(Scalp )?(Gold|Bitcoin) /, (m,s,a) => (a==="Gold"?"Au ":"₿ ")) : b.name,
    label: `${b.tf} · ${b.status==="probation" ? "on trial" : (b.weight*100).toFixed(0)+"% of money"} · ${b.live_n} trades`, pnl: tot, pnlTxt: usd(tot), pos: b.position}; }));
  for (const b of bots){ const prev = lastWeights[b.bot_id]; if (prev==null || Math.abs(prev-b.weight) > 0.005) flow(LIVE, b.bot_id, Math.abs((b.weight||0)-(prev||0))); lastWeights[b.bot_id] = b.weight; }
  renderBoard(); renderFeed(); renderCards();
  const xb = $("#scalpinfo");
  if (S.experimental){ xb.hidden = false;
    xb.innerHTML = `<b>⚗ Scalp Pit · experimental</b> — a separate $10,000 paper account. 1-minute bots trade around 1h, 4h, daily, session and round-number levels and hold 6–45 minutes. Same strict test as the main floor (profitable in training and on the unseen 6 months after costs), plus beating 75% of random bots.
      <br><span class="mut">Trading costs paid:</span> <b class="neg">${usd(-S.costs_paid_today_usd)}</b> <span class="mut">today ·</span> <b class="neg">${usd(-S.costs_paid_usd)}</b> <span class="mut">total · profit before costs</span> <b class="${cls(S.gross_before_costs_usd)}">${usd(S.gross_before_costs_usd)}</b>`; }
  else xb.hidden = true;
}
function renderBoard(){
  const B = STATE.summary.leaderboard || [], byId = Object.fromEntries(STATE.bots.map(b => [b.bot_id, b])), shown = boardAll ? B : B.slice(0, 10);
  $("#board").innerHTML = B.length ? shown.map((x,i) => { const b = byId[x.bot_id] || {}; return `<div class="lb" style="--c:${fam(x.family)[0]}"><span class="rk">${i+1}</span>
      <div style="min-width:0"><div class="nm"><span class="dot"></span>${esc(x.name)}${x.status==="probation"?'<span class="badge new">trial</span>':''}</div><div class="mut small">${N(x.trades)} paper trade${x.trades===1?"":"s"}${b.position?` · <span class="${b.position.dir>0?"pos":"neg"}">${b.position.dir>0?"buying":"selling"} now</span>`:""}</div></div>
      ${spark(b.spark, 64, 24)}<span class="p ${cls(x.profit_usd)}">${usd(x.profit_usd)}</span></div>`; }).join("")
      + (B.length > 10 ? `<button class="more" id="boardmore">${boardAll ? "Show top 10" : `Show all ${B.length}`}</button>` : "")
    : `<div class="empty">No bots on this floor yet — the breeder is still testing.</div>`;
  const m = $("#boardmore"); if (m) m.onclick = () => { boardAll = !boardAll; renderBoard(); };
}
const EV = {
  open:    (d, n, a) => ["◆", d.dir>0?"pos":"neg", `${n} ${d.dir>0?"bought":"sold"} ${a} at ${px(d.sym, d.entry)}`],
  close:   (d, n, a) => ["●", d.r>=0?"pos":"neg", `${n} closed its ${a} trade — ${({stop:"hit its stop loss", target:"hit its take profit", time:"time limit", flip:"signal reversed", "bot cut":"bot was cut", "floor dd kill":"floor drawdown kill"})[d.reason]||esc(d.reason)} · <b class="${d.r>=0?"pos":"neg"}">${d.r>=0?"win":"loss"} ${rr2(d.r)}</b>`],
  birth:   (d, n) => { const h = d.holdout; return ["✦", "gold", `<b>${n} was promoted</b>${d.origin==="backtest survivor" ? "" : h ? ` · on the 6 months it never trained on: <b>${rr2(h.net_r)}</b> over ${h.n} trades (profit factor ${fmt(h.pf,2)}) after costs · starts on trial` : " · starts on trial"}`]; },
  kill:    (d, n) => ["✕", "neg", `<b>${n} was cut</b> · ${esc(d.reason)}`],
  rebalance: () => ["⇄", "", `Ultron re-shared the money between the bots`, true],
  graduated: (d, n) => ["🎓", "pos", `<b>${n}</b> finished its trial and now gets full money`],
  blocked: (d, n, a) => ["⊘", "", `${n} wanted to ${d.dir>0?"buy":"sell"} ${a} — blocked by a floor limit (${esc(d.reason)})`, true],
  floor_halt: d => ["⛔", "neg", `<b>Floor lost ${fmt(-d.day_pnl_pct,1)}% today</b> — new trades paused until tomorrow`],
  floor_dd_kill: d => ["⛔", "neg", `<b>Floor drawdown kill</b> (${fmt(d.dd_pct,1)}% from peak) — all paper trades closed`],
};
function renderFeed(){
  const ev = (STATE.events || []).slice(0, 40);
  $("#feed").innerHTML = ev.length ? ev.map(e => { let d = {}; try { d = JSON.parse(e.detail || "{}"); } catch(x){}
    const n = esc(e.name || (e.bot_id!=null ? "bot #"+e.bot_id : "Ultron")), a = d.sym ? ASSET[d.sym].toLowerCase() : "";
    const [ic, k, txt, quiet] = (EV[e.kind] || (() => ["·", "", esc(e.kind)]))(d, n, a);
    return `<div class="ev${quiet?" quiet":""}"><span class="ic ${k}">${ic}</span><div>${txt}<span class="t">${sydS(e.ts)}</span></div></div>`; }).join("")
    : `<div class="empty">Nothing yet. Bots are waiting for their setups.</div>`;
}
function renderCards(){
  const all = STATE.bots, f = cardFilter;
  const list = all.filter(b => f==="all" || b.symbol===f || (f==="open" && b.position) || (f==="probation" && b.status==="probation"))
                  .sort((a,b) => (b.profit_usd+b.unreal_usd) - (a.profit_usd+a.unreal_usd));
  $("#cards-sub").textContent = `${list.length} of ${all.length} shown · best first`;
  $("#cards").innerHTML = list.length ? list.map(b => { const F = fam(b.family), tot = b.profit_usd + (b.unreal_usd||0), p = b.position;
    const doing = p ? `<div class="doing ${p.dir>0?"buy":"sell"}"><span class="live"></span>${p.dir>0?"Buying":"Selling"} ${ASSET[b.symbol].toLowerCase()} · open ${usd(b.unreal_usd)}${p.unreal_r!=null?` (${rr2(p.unreal_r)})`:""}</div>` : `<div class="doing wait">Waiting for a setup</div>`;
    const e = b.expected_trades_per_day, freq = e==null ? "" : ` · usually ~${e < 1 ? (e*7).toFixed(1)+"/week" : e.toFixed(1)+"/day"}`;
    const wr = b.live_n ? ` · ${fmt(100*b.wins/b.live_n,0)}% won` : "";
    return `<div class="bot${p?" trading":""}" style="--c:${F[0]};--side:${p?(p.dir>0?"var(--grn)":"var(--neg)"):"transparent"}"><div class="top"><div class="av">${F[1]}</div>
      <div style="min-width:0"><div class="nm">${esc(b.name)}${b.status==="probation"?'<span class="badge new">on trial</span>':''}</div><div class="meta">${b.tf} · gen ${b.gen ?? "–"} · ${(b.weight*100).toFixed(0)}% of the money</div></div>
      <div class="pf ${cls(tot)}">${usd(tot)}<small>${b.live_n} trade${b.live_n===1?"":"s"}${wr}</small></div></div>${doing}
      <div class="spark">${spark(b.spark)}<div class="s">last ${b.spark.length} trades<br><b class="${cls(b.spark.reduce((a,x)=>a+x,0))}">${rr2(b.spark.reduce((a,x)=>a+x,0))}</b>${freq}</div></div>
      ${indRow(b)}<details class="rule" data-id="${b.bot_id}"${openRules.has(b.bot_id)?" open":""}><summary>How it trades</summary><p>${esc(b.rule)}</p></details></div>`; }).join("")
    : `<div class="card empty">${all.length ? "No bots match this filter." : "No bots on this floor yet — the breeder is still testing."}</div>`;
  $$("#cards details.rule").forEach(d => d.ontoggle = () => { const id = +d.dataset.id; d.open ? openRules.add(id) : openRules.delete(id); });
}
$$("#cardfilter button").forEach(b => b.onclick = () => { cardFilter = b.dataset.k; $$("#cardfilter button").forEach(x => x.classList.toggle("on", x===b)); if (STATE) renderCards(); });

// ---------------- live trades ----------------
function progress(p, sym){
  const lo = Math.min(p.stop, p.tgt), hi = Math.max(p.stop, p.tgt), span = hi - lo || 1, pos = v => Math.min(100, Math.max(0, (v - lo) / span * 100));
  const cur = p.current ?? p.entry, good = p.dir > 0 ? cur >= p.entry : cur <= p.entry;
  const [lL, rL] = p.dir > 0 ? ["stop", "target"] : ["target", "stop"];
  return `<div class="prog"><span class="e" style="left:${pos(p.entry)}%"></span><span class="m" style="left:${pos(cur)}%;background:${good?"var(--grn)":"var(--neg)"}"></span></div><div class="prog-l"><span>${lL} ${px(sym, lo)}</span><span>${rL} ${px(sym, hi)}</span></div>`;
}
function renderTrades(){
  if (!STATE) return; const now = STATE.now;
  $$(".fl-title").forEach(e => e.textContent = floorTitle());
  const open = STATE.bots.filter(b => b.position);
  $("#tr-open").innerHTML = open.length ? `<div class="scroll"><table><thead><tr><th>Bot</th><th>Market</th><th>Side</th><th>Opened</th><th>Entry</th><th>Now</th><th style="text-align:center">Stop → target</th><th>R:R</th><th>Size</th><th>Risk</th><th>Live P&amp;L</th><th>Open for</th></tr></thead><tbody>` +
    open.map(b => { const p = b.position, s = b.symbol, u = b.unreal_usd || 0;
      return `<tr><td><b>${esc(b.name)}</b></td><td>${ASSET[s]}</td><td><span class="side ${p.dir>0?"b":"s"}">${p.dir>0?"BUY":"SELL"}</span></td><td>${sydT(p.since)}</td><td>${px(s,p.entry)}</td><td>${px(s,p.current)}</td>
        <td>${progress(p, s)}</td><td>${p.rr!=null?"1:"+fmt(p.rr,1):"—"}</td><td>${p.size!=null?fmt(p.size, s==="XAUUSD"?2:4)+" "+p.unit:"—"}</td><td>${p.risk_usd!=null?usd(p.risk_usd,false):"—"}</td>
        <td class="${cls(u)}"><b>${usd(u)}</b>${p.unreal_r!=null?`<br><span class="small">${rr2(p.unreal_r)}</span>`:""}</td><td>${dur(now-p.since)}</td></tr>`; }).join("") + `</tbody></table></div>`
    : `<div class="empty">No trades open right now.</div>`;
  const R = {stop:"stop loss", target:"take profit", time:"time limit", flip:"signal reversed", "bot cut":"bot was cut", "floor dd kill":"floor drawdown kill"};
  const closed = (STATE.trades || []).slice(0, 80);
  $("#tr-closed").innerHTML = closed.length ? `<div class="scroll"><table><thead><tr><th>Bot</th><th>Market</th><th>Side</th><th>Opened</th><th>Entry</th><th>Closed</th><th>Exit</th><th>Held</th><th>R:R</th><th>Result</th><th>Why it closed</th></tr></thead><tbody>` +
    closed.map(t => `<tr><td>${esc(t.name)}</td><td>${ASSET[t.symbol]}</td><td><span class="side ${t.dir>0?"b":"s"}">${t.dir>0?"BUY":"SELL"}</span></td><td>${sydT(t.entry_ts)}</td><td>${px(t.symbol,t.entry)}</td><td>${sydT(t.exit_ts)}</td><td>${px(t.symbol,t.exit)}</td>
      <td>${dur(t.exit_ts-t.entry_ts)}</td><td>${t.rr!=null?"1:"+fmt(t.rr,1):"—"}</td><td class="${cls(t.usd)}"><b>${usd(t.usd)}</b><br><span class="small">${rr2(t.r)}</span></td><td>${esc(R[t.reason]||t.reason)}</td></tr>`).join("") + `</tbody></table></div>`
    : `<div class="empty">No closed trades yet on this floor.</div>`;
}

// ---------------- results ----------------
function testLine(lab, m){ return `<tr><td>${lab}</td><td>${m.n}</td><td class="${cls(m.net_r)}"><b>${rr2(m.net_r)}</b><br><span class="mut small">${m.ret_pct>=0?"+":""}${fmt(m.ret_pct,1)}% at 1% risk</span></td><td>${fmt(m.win,0)}%<br><span class="mut small">${m.wins} won / ${m.losses} lost</span></td><td>${fmt(m.pf,2)}</td><td class="neg">${fmt(m.max_dd_pct,1)}%</td></tr>`; }
function whyCard(b){
  const e = b.evidence, L = b.live, F = fam(b.family);
  const head = `<div class="top"><div class="av">${F[1]}</div><div style="min-width:0"><div class="nm">${esc(b.name)}${b.status==="probation"?'<span class="badge new">on trial</span>':''}${FLOOR==="scalp"?'<span class="badge exp">experimental</span>':''}</div>
    <div class="meta">${b.tf} · promoted ${b.added_ts?sydS(b.added_ts):"–"}${b.origin==="breeder"?" by the nonstop breeder":" from the research run"}</div></div>
    <div class="pf ${cls(L.profit_usd)}">${usd(L.profit_usd)}<small>live since promotion</small></div></div>${indRow(b)}<details class="rule"><summary>How it trades</summary><p>${esc(b.rule)}</p></details>`;
  if (!e) return `<div class="bot why" style="--c:${F[0]}">${head}<p class="mut">Test numbers are being recorded…</p></div>`;
  const g = e.gate, H = e.holdout, T = e.train, R = e.random;
  const why = `<p><b>Why it got in:</b> on ${e.train_window[0].slice(0,4)}–${e.train_window[1].slice(0,7)} history it made <b>${rr2(T.net_r)}</b> over ${T.n} trades (won ${T.wins}, lost ${T.losses}), after trading costs.
    It stayed profitable when its settings were nudged. On the <b>last 6 months it never trained on</b> it made <b>${rr2(H.net_r)}</b> over ${H.n} trades (${fmt(H.win,0)}% wins, worst drop ${fmt(H.max_dd_pct,1)}%),
    which beat <b>${fmt(R.beat_pct,0)}% of ${R.n} random bots</b> on the same months (the typical random bot made ${rr2(R.median_net_r)}).
    The bar on this floor: at least ${g.n} recent trades, profit factor ≥ ${g.pf}, net ${g.net_r>=0?"> "+g.net_r+"R":"≥ "+g.net_r+"R"}, worst drop no worse than ${g.dd_pct}%${g.beat_random_pct?`, and beat at least ${g.beat_random_pct}% of random bots`:""}; and it is not a copy of a bot already on the floor.${e.backfilled?' <span class="mut">(numbers re-checked after it joined)</span>':''}</p>`;
  const sealed = e.sealed ? testLine("Sealed final test", e.sealed) : "";
  const tbl = `<div class="scroll"><table><thead><tr><th>Test</th><th>trades</th><th>profit</th><th>win rate</th><th>profit factor</th><th>worst drop</th></tr></thead><tbody>${testLine("History (training)", T)}${testLine("Last 6 months (holdout)", H)}${sealed}</tbody></table></div>`;
  const live = `<div class="livebox"><b>Live paper results since promotion:</b> ${L.trades ? `${L.trades} trades · ${L.wins} won / ${L.losses} lost (${fmt(L.win_pct,0)}%) · <b class="${cls(L.profit_usd)}">${usd(L.profit_usd)}</b> (${rr2(L.net_r)}) · costs paid ${usd(-L.costs_usd)} · worst drop ${fmt(L.worst_drop_r,1)}R` : "no finished trades yet"}</div>`;
  return `<div class="bot why" style="--c:${F[0]}">${head}${why}${tbl}${live}</div>`;
}
let resultsAt = 0;
async function loadResults(force){
  if (!force && RESULTS && RESULTS.floor===FLOOR && Date.now()-resultsAt < 30000) return;
  try { const f = FLOOR, r = await (await fetch(`api/results?floor=${f}`, {cache:"no-store"})).json(); if (f !== FLOOR) return; RESULTS = r; resultsAt = Date.now(); } catch(e){ return; }
  const R = RESULTS; $$(".fl-title").forEach(e => e.textContent = floorTitle());
  $("#res-promoted").innerHTML = R.promoted.length ? R.promoted.map(whyCard).join("") : `<div class="card empty">No bots promoted yet — the breeder is still testing.</div>`;
  $("#res-killed").innerHTML = R.killed.length ? R.killed.map(b => `<div class="krow"><b>✕ ${esc(b.name)}</b> <span class="mut small">${b.tf} · cut ${b.removed_ts?sydS(b.removed_ts):""}</span><br><span class="mut small">${esc(b.reason||"")}${b.live.trades?` · live: ${b.live.trades} trades, <span class="${cls(b.live.profit_usd)}">${usd(b.live.profit_usd)}</span>`:""}</span></div>`).join("") : `<div class="empty">No bots cut yet.</div>`;
}

// ---------------- what Ultron learned ----------------
let learnedAt = 0;
async function loadLearned(force){
  if (!force && LEARNED && LEARNED.floor===FLOOR && Date.now()-learnedAt < 60000) return;
  try { const f = FLOOR, r = await (await fetch(`api/learned?floor=${f}`, {cache:"no-store"})).json(); if (f !== FLOOR) return; LEARNED = r; learnedAt = Date.now(); } catch(e){ return; }
  renderLearned();
}
function tbl(head, rows){ return rows.length ? `<div class="scroll"><table><thead><tr>${head.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="mut small">Not enough data yet.</div>`; }
function lineChart(id, series, labels, colors, unit){
  const cv = document.getElementById(id); if (!cv || !cv.offsetParent) return; const [c, W, H] = fit(cv);
  c.fillStyle = TK.bg; c.fillRect(0,0,W,H);
  const all = series.flat().filter(v => v!=null); if (!all.length || !series[0] || series[0].length < 2){ c.fillStyle = TK.mut; c.font = `12px ${FONT}`; c.fillText("Not enough hours of data yet for a chart", 12, 22); return; }
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi-lo)*0.1 || 1, y0 = lo-pad, y1 = hi+pad, n = series[0].length;
  const X = i => 44 + (W-56)*i/(n-1), Y = v => H-22 - (H-34)*(v-y0)/(y1-y0);
  c.strokeStyle = TK.grid; c.fillStyle = TK.mut; c.font = `10px ${FONT}`;
  for (let k=0; k<=3; k++){ const v = y0+(y1-y0)*k/3; c.beginPath(); c.moveTo(44,Y(v)); c.lineTo(W-12,Y(v)); c.stroke(); c.fillText(fmt(v,1)+unit, 4, Y(v)+3); }
  c.fillText(labels[0], 44, H-6); c.textAlign = "right"; c.fillText(labels[n-1], W-12, H-6); c.textAlign = "left";
  series.forEach((sr,j) => { c.strokeStyle = colors[j]; c.lineWidth = 2; c.beginPath(); let st = false; sr.forEach((v,i) => { if (v==null) return; st ? c.lineTo(X(i),Y(v)) : c.moveTo(X(i),Y(v)); st = true; }); c.stroke(); });
}
function renderLearned(){
  const L = LEARNED; if (!L) return; $$(".fl-title").forEach(e => e.textContent = floorTitle());
  $("#ln-basis").innerHTML = `Based on <b>${N(L.sample)}</b> bots Ultron tested in the last ${L.hours} hours (a 35% random sample of everything tested is logged), every gate run, and every bot on this floor. Claims about traits only use the randomly created bots, so Ultron's own favourites can't flatter themselves. A claim needs at least ${N(L.markets.XAUUSD?.min_sample || 1000)} random bots per market.`;
  const KIND = {winning:["✅","Winning"], dying:["🪦","Dying out"], setting:["⚙️","Settings"], "kill pattern":["✕","Why bots die"], trend:["📈","Shifting"]};
  $("#ln-lessons").innerHTML = L.lessons.length ? L.lessons.map(l => `<div class="lesson"><span class="mut small">${new Date(l.ts).toLocaleString("en-AU",{timeZone:TZ, day:"numeric", month:"short", hour:"numeric", minute:"2-digit"})}</span> ${esc(l.text)} ${l.still_true?'<span class="badge pass">still true</span>':'<span class="badge dim">no longer seen</span>'}</div>`).join("")
    : `<div class="empty">No lessons yet — Ultron writes one down the first time the data clearly shows something (checked every 10 minutes).</div>`;
  $("#ln-now").innerHTML = L.claims.length ? L.claims.map(c => { const k = KIND[c.kind] || ["•", c.kind]; return `<div class="lesson"><b>${k[0]} ${k[1]}</b> · ${esc(c.text)}</div>`; }).join("") : `<div class="empty">Nothing clear enough to claim yet.</div>`;
  $("#ln-markets").innerHTML = ["XAUUSD","BTCUSD"].map(sym => { const m = L.markets[sym] || {};
    const lift = rows => tbl(["", "random bots tested", "% of tested", "% of best 5%", "× more likely"], (rows||[]).map(r => [esc(r.value), N(r.tested), r.share_all+"%", r.share_best+"%", `<b class="${r.lift>=1.5?"pos":r.lift<=0.4?"neg":""}">${fmt(r.lift,1)}×</b>`]));
    return `<section class="card"><div class="ct"><h2>${ASSET[sym]}</h2><span class="mut small">${N(m.tested_sample)} logged · ${N(m.random_sample)} random${m.enough?"":` (claims start at ${N(m.min_sample)})`}${m.share_positive_fit!=null?` · ${m.share_positive_fit}% scored above zero on history`:""}${m.gate?` · ${N(m.gate.runs)} reached the gate, ${N(m.gate.passed_holdout)} passed the 6-month test, ${N(m.gate.promoted)} promoted`:""}</span></div>
      <div class="grid2"><div><h4>Strategy families</h4>${lift(m.families)}</div><div><h4>Timeframes</h4>${lift(m.timeframes)}<h4>Trading hours</h4>${lift(m.sessions)}</div></div>
      <div class="grid2"><div><h4>Settings: best 5% vs all</h4>${tbl(["setting","all random (median)","best 5% (median)"], (m.settings||[]).map(r => [r.setting, fmt(r.all,2)+r.unit, fmt(r.best,2)+r.unit]))}</div>
        <div><h4>Trading costs by timeframe</h4>${tbl(["timeframe","bots (≥30 trades)","profitable before costs","turned losers by costs","cost / trade"], (m.costs||[]).map(r => [r.tf, N(r.tested), N(r.gross_profitable), `${N(r.turned_losers_by_costs)} (${r.gross_profitable?Math.round(100*r.turned_losers_by_costs/r.gross_profitable):0}%)`, fmt(r.cost_r_per_trade,2)+"R"]))}</div></div>
      <h4>On this floor</h4>${tbl(["family","alive","cut","live paper profit"], (m.floor_families||[]).map(r => [esc(r.family), r.alive, r.cut, `<span class="${cls(r.live_profit_usd)}">${usd(r.live_profit_usd)}</span>`]))}
      <div class="grid2"><div><h4>Best 10% of new bots, hour by hour</h4><div class="mkey"><span><i class="k" style="background:${TK.gold}"></i>stop (× ATR)</span><span><i class="k" style="background:${TK.grn}"></i>target (× ATR)</span><span><i class="k" style="background:${TK.blu}"></i>max hold (h)</span></div><canvas class="lchart" id="lc-${sym}"></canvas></div>
        <div><h4>Family share of the best 10%</h4><div class="legend" id="lfl-${sym}"></div><canvas class="lchart" id="lf-${sym}"></canvas></div></div></section>`; }).join("");
  const K = L.kills; $("#ln-kills").innerHTML = K.total ? tbl(["why cut","bots"], Object.entries(K.by_reason).sort((a,b) => b[1]-a[1]).map(([k,v]) => [esc(k), v])) + `<div class="mut small" style="margin-top:8px">${K.total} bots cut on this floor · ${N(K.live_trades_of_cut_bots)} live paper trades between them · ${usd(-K.costs_paid_usd)} trading costs paid</div>` : `<div class="empty">No bots cut yet.</div>`;
  drawLearnedCharts();
}
function drawLearnedCharts(){
  if (!LEARNED) return;
  for (const sym of ["XAUUSD","BTCUSD"]){ const S = (LEARNED.shifts||{})[sym] || []; const lab = S.map(x => new Date(x.t*1000).toLocaleTimeString("en-AU",{timeZone:TZ, hour:"numeric"}));
    lineChart("lc-"+sym, [S.map(x=>x.sl), S.map(x=>x.tp), S.map(x=>x.hold_h)], lab, [TK.gold, TK.grn, TK.blu], "");
    const fams = [...new Set(S.flatMap(x => Object.keys(x.fam)))], col = n => (Object.values(FAM).find(f => f[2]===n) || ["#888"])[0];
    lineChart("lf-"+sym, fams.map(f => S.map(x => x.fam[f] || 0)), lab, fams.map(col), "%");
    const lg = document.getElementById("lfl-"+sym); if (lg) lg.innerHTML = fams.map(f => `<span><i style="background:${col(f)}"></i>${esc(f)}</span>`).join(""); }
}

// ---------------- research: replay, timeline, equity, survivors ----------------
$("#legend").innerHTML = Object.values(FAM).map(f => `<span><i style="background:${f[0]}"></i>${f[2]}</span>`).join("") + `<span>✦ born</span><span class="neg">✕ deleted</span><span style="color:var(--gold)">• money flow</span>`;
const hasRun = () => REPLAY && REPLAY.run_id;
function drawTimeline(){
  if (!hasRun() || VIEW!=="details") return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#timeline")); c.fillStyle = TK.bg; c.fillRect(0,0,W,H);
  const gens = S.generations; if (!gens.length) return;
  const mx = Math.max(...gens.map(g=>g.gen)), lo = Math.min(...gens.map(g=>g.median)), hi = Math.max(...gens.map(g=>g.best));
  const X = (isl,gen) => 30 + (W-40)*((isl*(mx+1)+gen)/((mx+1)*4)), Y = v => H-18-(H-30)*(v-lo)/(hi-lo||1);
  c.strokeStyle = TK.grid; c.beginPath(); c.moveTo(30,Y(0)); c.lineTo(W-10,Y(0)); c.stroke(); c.fillStyle = TK.mut; c.font = `10px ${FONT}`; c.fillText("0", 18, Y(0)+3);
  for (let isl=0; isl<4; isl++){ const gi = gens.filter(g=>g.island===isl); if (!gi.length) continue;
    for (const [k,col] of [["median",TK.blu],["best",TK.red]]){ c.strokeStyle = col; c.lineWidth = 1.6; c.beginPath(); gi.forEach((g,i) => { const x = X(isl,g.gen), y = Y(g[k]); i ? c.lineTo(x,y) : c.moveTo(x,y); }); c.stroke(); }
    c.fillStyle = TK.mut; c.fillText(`island ${isl}`, X(isl,0)+2, 12); }
  const f = S.frames[Math.min(frameI, S.frames.length-1)]; if (f && replayTimer){ c.strokeStyle = TK.gold; c.beginPath(); c.moveTo(X(f.island,f.gen),16); c.lineTo(X(f.island,f.gen),H-14); c.stroke(); }
  c.fillStyle = TK.red; c.fillText("best", W-64, H-4); c.fillStyle = TK.blu; c.fillText("median", W-38, H-4);
}
function drawResearchEquity(){
  if (!hasRun() || VIEW!=="details") return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#equity")); c.fillStyle = TK.bg; c.fillRect(0,0,W,H);
  const curves = Object.entries(S.curves);
  if (!curves.length){ c.fillStyle = TK.mut; c.font = `13px ${FONT}`; c.fillText("No " + ASSET[SYM].toLowerCase() + " strategy passed the out-of-sample tests", 14, 30); return; }
  const all = curves.flatMap(([,p]) => p); if (!all.length) return;
  const t0 = Math.min(...all.map(p=>p[0])), t1 = Math.max(...all.map(p=>p[0])), vs = all.map(p=>p[1]), lo = Math.min(0,...vs), hi = Math.max(1,...vs);
  const X = t => 34+(W-44)*(t-t0)/(t1-t0||1), Y = v => H-16-(H-28)*(v-lo)/(hi-lo||1);
  c.strokeStyle = TK.grid; c.beginPath(); c.moveTo(34,Y(0)); c.lineTo(W,Y(0)); c.stroke();
  const col = {t:"#7f8ea3", v:TK.blu, h:TK.gold};
  for (const [,pts] of curves) for (let i=1; i<pts.length; i++){ c.strokeStyle = col[pts[i][2]] || "#888"; c.lineWidth = 1.6; c.beginPath(); c.moveTo(X(pts[i-1][0]),Y(pts[i-1][1])); c.lineTo(X(pts[i][0]),Y(pts[i][1])); c.stroke(); }
  c.fillStyle = TK.mut; c.font = `10px ${FONT}`; c.fillText(fmt(hi,0)+"R",2,Y(hi)+8); c.fillText(fmt(lo,0)+"R",2,Y(lo));
  for (const y of [2024,2025,2026]){ const t = Date.UTC(y,0,1)/1000; if (t>t0 && t<t1) c.fillText(y, X(t)-10, H-3); }
}
function renderSurvivors(){
  const S = REPLAY.symbols[SYM], m = S.survivor_metrics || [], W = REPLAY.windows;
  const row = (lab, x) => `<tr><td>${lab}</td><td>${x.n}</td><td class="${x.ret_pct>=0?'pos':'neg'}">${x.ret_pct>=0?"+":""}${fmt(x.ret_pct)}%</td><td>${fmt(x.max_dd_pct,1)}%</td><td>${fmt(x.pf)}</td><td>${fmt(x.win,0)}%</td><td>${rr2(x.net_r)}</td></tr>`;
  const ch = s => s.challenge ? `<tr><td colspan=7 class="desc">FTMO practice challenges pass / breach: ${["train","valid","holdout"].map(w => { const c = s.challenge.FTMO[w]; return `${w} ${(c.pass_rate*100).toFixed(0)}% / ${(c.breach_rate*100).toFixed(0)}%`; }).join(" · ")}</td></tr>` : "";
  $("#surv").innerHTML = m.length ? m.map(s => `<div class="scroll"><table><thead><tr><th>#${s.bot_id} · weight ${(s.weight*100).toFixed(0)}%</th><th>trades</th><th>return</th><th>max DD</th><th>PF</th><th>win</th><th>net R</th></tr></thead><tbody>
    ${row(`Train ${W.train[0].slice(0,7)}…${W.train[1].slice(0,7)}`, s.train)}${row("Validation (unseen)", s.valid)}${row("Held-back (sealed)", s.holdout)}
    ${s.adj_holdout ? row("Held-back, drift-adjusted", s.adj_holdout) : ""}${ch(s)}<tr><td colspan=7 class="desc">${esc(s.desc)}</td></tr></tbody></table></div>`).join("")
    : `<div class="empty">No ${ASSET[SYM].toLowerCase()} strategy survived the out-of-sample tests.</div>`;
  const wf = S.walk_forward_adj || S.walk_forward;
  $("#gates").innerHTML = `${N(S.tested)} strategies tested · passed: stability ${S.gates.plateau}/${S.gates.pool} → validation ${S.gates.valid} → walk-forward ${S.gates.wf} → not-duplicates ${S.gates.decor}.<br>
    Random-bot baseline: ${S.noise.pass_valid_gate}/${S.noise.random_tested} random bots also pass validation.` + (wf ? `<br>Walk-forward of the whole machine (drift-adjusted): ${wf.n} trades, PF ${fmt(wf.pf)}, net ${rr2(wf.net_r)}, max DD ${fmt(wf.max_dd_pct,1)}%.` : "");
}
function showFrame(){
  const S = REPLAY.symbols[SYM], f = S.frames[frameI]; if (!f) return;
  const list = f.desks.map(d => ({id:d.id, fam:d.fam, title:fam(d.fam)[2], label:`island ${f.island} · born gen ${d.born}`, pnl:d.fit, pnlTxt:`score ${fmt(d.fit)}`, w:0}));
  for (const d of f.desks){ const e = REP.desks.get(d.id); if (e) e.willDie = d.dies!=null; }
  for (const [id,e] of REP.desks) if (!list.find(x => x.id===id)) e.willDie = true;
  setDesks(REP, list); $("#rfloor-sub").textContent = `${ASSET[SYM]} · island ${f.island} · generation ${f.gen} · top 12`; drawTimeline();
}
function startReplay(){
  if (!hasRun()) return; clearInterval(replayTimer); frameI = 0; REP.desks.clear();
  replayTimer = setInterval(() => { const S = REPLAY.symbols[SYM]; if (frameI >= S.frames.length){ clearInterval(replayTimer); replayTimer = null; showSurvivors(); return; } showFrame(); frameI++; }, 300);
}
function showSurvivors(){
  const S = REPLAY.symbols[SYM]; REP.desks.forEach(d => d.willDie = true);
  setDesks(REP, S.survivors.map(s => ({id:s.id, fam:s.fam, title:fam(s.fam)[2], label:"survivor · passed tests", pnlTxt:`${(s.weight*100).toFixed(0)}% of money`, w:s.weight})));
  $("#rfloor-sub").textContent = `${ASSET[SYM]} · ${S.survivors.length} survivors of the out-of-sample tests receive money`;
  setTimeout(() => S.survivors.forEach(s => flow(REP, s.id, s.weight)), 700); drawTimeline();
}
function renderResearch(){
  $("#rs-empty").hidden = !!hasRun() || REPLAY == null; $("#rs-body").hidden = !hasRun();
  if (!hasRun()) return; $("#rs-sub").textContent = `run ${REPLAY.run_id}`;
  renderSurvivors(); drawTimeline(); drawResearchEquity(); if (!replayTimer && !REP.desks.size) startReplay();
}
$$("#tabs button[data-s]").forEach(b => b.onclick = () => { $$("#tabs button[data-s]").forEach(x => x.classList.toggle("on", x===b)); SYM = b.dataset.s; if (hasRun()){ renderSurvivors(); drawTimeline(); drawResearchEquity(); startReplay(); } });
$("#btn-replay").onclick = () => startReplay();

// ---------------- polling: setTimeout chain, request timeout, backoff, instant re-poll on wake ----------------
let lastOk = 0, failCount = 0, inflight = null, pollTimer = null;
const POLL_MS = 4000;
function schedule(ms){ clearTimeout(pollTimer); pollTimer = setTimeout(poll, ms); }
function wake(){ if (document.visibilityState !== "hidden"){ if (inflight){ inflight.abort(); inflight = null; } poll(); } }
document.addEventListener("visibilitychange", wake); addEventListener("pageshow", wake); addEventListener("online", wake);
function liveInd(){ const el = $("#liveind"), sp = el.querySelector("span"), age = lastOk ? (Date.now()-lastOk)/1000 : null;
  if (age == null){ el.className = "pill amber"; sp.textContent = failCount ? "Reconnecting…" : "Connecting…"; return; }
  el.className = "pill " + (age > 30 ? "amber" : "ok");
  sp.textContent = (age > 30 ? (failCount ? "Reconnecting · " : "Stale · ") : "Live · ") + (age < 1.5 ? "just now" : `${Math.round(age)}s ago`); }
setInterval(liveInd, 1000);
async function poll(){
  clearTimeout(pollTimer); if (inflight) return;
  const ctl = new AbortController(); inflight = ctl; const to = setTimeout(() => ctl.abort(), 8000);
  try { const f = FLOOR, res = await fetch(`api/state?floor=${f}`, {cache:"no-store", signal:ctl.signal});
    if (!res.ok) throw new Error("HTTP " + res.status);
    const st = await res.json(); if (f !== FLOOR) return; STATE = st; lastOk = Date.now(); failCount = 0;
    for (const s of ["XAUUSD","BTCUSD"]){ const t = STATE.ticks[s]; $("#tk-"+s).textContent = t ? `${ASSET[s]} ${s==="XAUUSD" ? fmt(t.bid,2)+" / "+fmt(t.ask,2) : fmt(t.bid,1)} · ${ago(t.ts)} ago` : `${ASSET[s]} —`; }
    renderMain(); if (VIEW==="trades") renderTrades(); if (VIEW==="results") loadResults(); if (VIEW==="learned") loadLearned();
  } catch(e){ failCount++; if (!STATE) $("#status").innerHTML = `<span class="mut">Can't reach the floor right now — retrying…</span>`; }
  finally { clearTimeout(to); if (inflight === ctl) inflight = null; liveInd();
    schedule(failCount ? Math.min(15000, 1500 * failCount) : (document.visibilityState === "hidden" ? 15000 : POLL_MS)); }
}

// ---------------- navigation ----------------
function setFloor(f){ if (f === FLOOR) return; FLOOR = f; STATE = null; RESULTS = null; LEARNED = null; LIVE.desks.clear(); LIVE.particles.length = 0;
  for (const k in lastWeights) delete lastWeights[k];
  $$("#floortabs button").forEach(b => b.classList.toggle("on", b.dataset.f===f)); document.body.classList.toggle("scalp", f==="scalp"); tokens();
  history.replaceState(null, "", (f==="scalp" ? "#scalp" : "#") + (VIEW!=="floor" ? (f==="scalp" ? "-" : "") + VIEW : ""));
  for (const id of ["#board","#cards","#feed","#risk","#tr-open","#tr-closed","#res-promoted","#res-killed"]) $(id).innerHTML = "";
  $("#status").innerHTML = `<span class="mut">Loading ${floorTitle()}…</span>`; drawEquity();
  if (inflight){ inflight.abort(); inflight = null; } poll(); if (VIEW==="results") loadResults(true); if (VIEW==="learned") loadLearned(true); }
$$("#floortabs button").forEach(b => b.onclick = () => setFloor(b.dataset.f));
function setView(v){ VIEW = v; for (const x of ["floor","trades","results","learned","how","details"]) $("#v-"+x).hidden = x!==v;
  $$("#maintabs button").forEach(b => b.classList.toggle("on", b.dataset.v===v));
  history.replaceState(null, "", (FLOOR==="scalp" ? "#scalp" : "#") + (v!=="floor" ? (FLOOR==="scalp" ? "-" : "") + v : ""));
  if (v==="floor"){ drawEquity(); } if (v==="results") loadResults(true); if (v==="trades") renderTrades(); if (v==="learned"){ renderLearned(); loadLearned(true); }
  if (v==="details") renderResearch(); }
$$("#maintabs button").forEach(b => b.onclick = () => setView(b.dataset.v));
function redrawStatic(){ drawEquity(); drawTimeline(); drawResearchEquity(); drawLearnedCharts(); }
let rz = null; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(redrawStatic, 120); });

if (FLOOR==="scalp"){ document.body.classList.add("scalp"); $$("#floortabs button").forEach(b => b.classList.toggle("on", b.dataset.f==="scalp")); tokens(); }
setTheme(document.documentElement.dataset.theme || "auto");
const hv = (location.hash.match(/(trades|results|learned|how|details)$/) || [])[1];
requestAnimationFrame(loop); poll();
fetch("api/replay").then(r => r.json()).then(j => { REPLAY = j; if (VIEW==="details") renderResearch(); }).catch(() => { REPLAY = {run_id:null}; });
if (hv) setView(hv);
