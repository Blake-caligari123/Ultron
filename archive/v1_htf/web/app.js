// ULTRON floor view — plain JS + canvas. Replays backtest evolution, then goes LIVE (paper).
const FAM = {donchian_breakout:["#ff4d5e","D","Donchian breakout"], bollinger_reversion:["#4da3ff","B","Bollinger reversion"],
  ema_trend_cross:["#2ee59d","E","EMA trend cross"], session_orb:["#f5c84c","O","Session open-range"], rsi_pullback:["#b57bff","R","RSI pullback"]};
const $ = s => document.querySelector(s);
let SYM = "XAUUSD", MODE = "replay", REPLAY = null, STATE = null, frameI = 0, replayTimer = null;
const desks = new Map();     // id -> {slot, x, y, born, dying, fam, label, pnl, pos, w}
const particles = [];
let lastWeights = {};

function fit(cv){ const r = cv.getBoundingClientRect(), d = devicePixelRatio||1; cv.width = r.width*d; cv.height = r.height*d; const c = cv.getContext("2d"); c.setTransform(d,0,0,d,0,0); return [c, r.width, r.height]; }
const fmt = (x, d=2) => x==null||isNaN(x) ? "—" : (+x).toFixed(d);
const sgn = x => (x>0?"+":"") + fmt(x);
const ago = ts => { const s = Math.max(0, Date.now()/1000 - ts); return s<90? `${s|0}s` : s<5400? `${(s/60)|0}m` : `${(s/3600).toFixed(1)}h`; };

$("#legend").innerHTML = Object.values(FAM).map(f=>`<span><i style="background:${f[0]}"></i>${f[1]} ${f[2]}</span>`).join("") +
  `<span>✦ birth</span><span style="color:#ff4d5e">✕ kill</span><span style="color:#f5c84c">• capital flow</span>`;

// ---------------- floor ----------------
function layout(n, W, H){ const cols = W < 520 ? 3 : 4, rows = Math.max(3, Math.ceil(n/cols)); const top = 64, cw = W/cols, ch = (H-top-6)/rows;
  return {cols, cw, ch, top, pos: i => [cw*(i%cols) + cw/2, top + ch*Math.floor(i/cols) + ch/2]}; }

function setDesks(list){   // list: [{id,fam,label,pnl,pnlTxt,pos,w,gen}]
  const now = performance.now(); const ids = new Set(list.map(d=>d.id));
  for (const [id,d] of desks) if (!ids.has(id) && !d.dying) { d.dying = now; d.killed = !!d.willDie; }
  const used = new Set([...desks.values()].filter(d=>!d.dying).map(d=>d.slot));
  for (const d of list){
    let e = desks.get(d.id);
    if (!e || e.dying){ let s=0; while(used.has(s)) s++; used.add(s); e = {slot:s, born:now}; desks.set(d.id, e); }
    Object.assign(e, d);
  }
}

function flow(toId, amount){ const e = desks.get(toId); if (!e) return; for (let i=0;i<Math.min(14, 2+amount*40);i++) particles.push({to:toId, t:-i*0.06, sp:0.6+Math.random()*0.5}); }

function drawFloor(){
  const [c, W, H] = fit($("#floor")); const now = performance.now();
  const live = [...desks.values()].filter(d=>!d.dying); const L = layout(Math.max(live.length, 9), W, H);
  // floor grid
  c.strokeStyle = "#121a25"; c.lineWidth = 1; for (let x=0;x<W;x+=24){c.beginPath();c.moveTo(x,0);c.lineTo(x,H);c.stroke();} for (let y=0;y<H;y+=24){c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}
  // allocator core
  const ax = W/2, ay = 30, pulse = 1+0.08*Math.sin(now/300);
  const g = c.createRadialGradient(ax,ay,2,ax,ay,26*pulse); g.addColorStop(0,"#ff8a3d"); g.addColorStop(1,"rgba(255,42,61,0)"); c.fillStyle=g; c.beginPath(); c.arc(ax,ay,26*pulse,0,7); c.fill();
  c.fillStyle="#fff"; c.font="700 10px system-ui"; c.textAlign="center"; c.fillText("ALLOCATOR", ax, ay+3);
  for (const [id,d] of desks){
    const [x,y] = L.pos(d.slot); d.x=x; d.y=y;
    let a = 1, sc = 1;
    const age = (now-d.born)/600; if (age<1){ sc = 0.4+0.6*easeOut(age); }
    if (d.dying){ const k=(now-d.dying)/900; if (k>1){ desks.delete(id); continue; } a = 1-k; sc = 1-0.4*k; }
    const w = Math.min(L.cw, 150)-12, h = Math.min(L.ch-10, 86);
    c.save(); c.globalAlpha = a; c.translate(x,y); c.scale(sc,sc);
    // desk
    c.fillStyle = "#121a26"; c.strokeStyle = d.pos? (d.pos.dir>0?"#2ee59d":"#ff4d5e") : "#223044"; c.lineWidth = d.pos?2:1;
    rr(c,-w/2,-h/2,w,h,10); c.fill(); c.stroke();
    // weight bar (capital)
    if (d.w){ c.fillStyle="#f5c84c"; c.fillRect(-w/2+6, h/2-6, (w-12)*Math.min(1,d.w/0.25), 3); }
    // avatar
    const f = FAM[d.fam]||["#888","?"]; const r = Math.min(W<520?12:16, h/4);
    c.fillStyle = f[0]; c.beginPath(); c.arc(-w/2+8+r, -h/2+8+r, r, 0, 7); c.fill();
    c.fillStyle = "#07090d"; c.font = `800 ${r}px system-ui`; c.textAlign="center"; c.fillText(f[1], -w/2+8+r, -h/2+8+r*1.36);
    c.textAlign="left"; c.fillStyle="#d9e2ee"; c.font="600 11px system-ui"; c.fillText(`#${id}`, -w/2+14+2*r, -h/2+18);
    c.fillStyle="#7a889b"; c.font="10px system-ui"; c.fillText(d.label||"", -w/2+14+2*r, -h/2+31);
    c.font="700 13px system-ui"; c.fillStyle = d.pnl>0?"#2ee59d":d.pnl<0?"#ff4d5e":"#d9e2ee"; c.fillText(d.pnlTxt||"", -w/2+8, h/2-14);
    if (d.pos){ c.fillStyle = d.pos.dir>0?"#2ee59d":"#ff4d5e"; c.font="700 12px system-ui"; c.textAlign="right"; c.fillText(d.pos.dir>0?"▲ LONG":"▼ SHORT", w/2-8, h/2-14); }
    if (age<1.6 && !d.dying){ c.strokeStyle=`rgba(46,229,157,${1-age/1.6})`; c.lineWidth=3; c.beginPath(); c.arc(0,0,w/2*(0.6+age/2),0,7); c.stroke(); c.fillStyle="#2ee59d"; c.font="700 12px system-ui"; c.textAlign="center"; c.fillText("✦ BORN", 0, -h/2-4); }
    if (d.dying && d.killed){ c.strokeStyle="#ff4d5e"; c.lineWidth=4; c.beginPath(); c.moveTo(-w/3,-h/3); c.lineTo(w/3,h/3); c.moveTo(w/3,-h/3); c.lineTo(-w/3,h/3); c.stroke(); }
    c.restore();
  }
  // capital particles
  for (let i=particles.length-1;i>=0;i--){ const p = particles[i]; p.t += 0.016*p.sp; const d = desks.get(p.to);
    if (!d || p.t>1){ particles.splice(i,1); continue; } if (p.t<0) continue;
    const t = easeOut(p.t), x = ax+(d.x-ax)*t, y = ay+(d.y-ay)*t - Math.sin(t*Math.PI)*30;
    c.fillStyle="#f5c84c"; c.beginPath(); c.arc(x,y,2.6,0,7); c.fill(); }
  requestAnimationFrame(drawFloor);
}
const easeOut = t => 1-Math.pow(1-Math.min(1,Math.max(0,t)),3);
function rr(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}

// ---------------- timeline ----------------
function drawTimeline(){
  if (!REPLAY) return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#timeline"));
  const gens = S.generations; if (!gens.length) return;
  const mx = Math.max(...gens.map(g=>g.gen)), lo = Math.min(...gens.map(g=>g.median)), hi = Math.max(...gens.map(g=>g.best));
  const X = (isl,gen) => 30 + (W-40)*((isl*(mx+1)+gen)/((mx+1)*4)), Y = v => H-18-(H-30)*(v-lo)/(hi-lo||1);
  c.strokeStyle="#1c2532"; c.beginPath(); c.moveTo(30,Y(0)); c.lineTo(W-10,Y(0)); c.stroke(); c.fillStyle="#7a889b"; c.font="10px system-ui"; c.fillText("0", 18, Y(0)+3);
  for (let isl=0;isl<4;isl++){ const gi = gens.filter(g=>g.island===isl); if(!gi.length) continue;
    for (const [k,col] of [["median","#4da3ff"],["best","#ff4d5e"]]){ c.strokeStyle=col; c.lineWidth=1.6; c.beginPath(); gi.forEach((g,i)=>{ const x=X(isl,g.gen), y=Y(g[k]); i?c.lineTo(x,y):c.moveTo(x,y); }); c.stroke(); }
    c.fillStyle="#7a889b"; c.fillText(`island ${isl}`, X(isl,0)+2, 12); }
  if (MODE==="replay"){ const f = S.frames[Math.min(frameI, S.frames.length-1)]; if (f){ c.strokeStyle="#f5c84c"; c.beginPath(); c.moveTo(X(f.island,f.gen),16); c.lineTo(X(f.island,f.gen),H-14); c.stroke(); } }
  c.fillStyle="#ff4d5e"; c.fillText("best", W-60, H-4); c.fillStyle="#4da3ff"; c.fillText("median", W-34, H-4);
}

// ---------------- equity ----------------
function drawEquity(){
  if (!REPLAY) return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#equity"));
  const curves = Object.entries(S.curves); const liveEq = STATE ? (STATE.equity[SYM]||[]) : [];
  if (!curves.length){ c.fillStyle="#7a889b"; c.font="13px system-ui"; c.fillText("No survivors passed the out-of-sample gates for "+SYM, 14, 30); }
  const all = curves.flatMap(([,p])=>p); if (!all.length) return;
  const t0 = Math.min(...all.map(p=>p[0])), t1 = Math.max(...all.map(p=>p[0]), ...liveEq.map(p=>p[0]));
  const vs = all.map(p=>p[1]), lo = Math.min(0,...vs), hi = Math.max(1,...vs);
  const X = t => 34+(W-44)*(t-t0)/(t1-t0||1), Y = v => H-16-(H-28)*(v-lo)/(hi-lo||1);
  c.strokeStyle="#1c2532"; c.beginPath(); c.moveTo(34,Y(0)); c.lineTo(W,Y(0)); c.stroke();
  const col = {t:"#7f8ea3", v:"#4da3ff", h:"#f5c84c"};
  for (const [,pts] of curves){ for (let i=1;i<pts.length;i++){ c.strokeStyle = col[pts[i][2]]||"#888"; c.lineWidth=1.6; c.beginPath(); c.moveTo(X(pts[i-1][0]),Y(pts[i-1][1])); c.lineTo(X(pts[i][0]),Y(pts[i][1])); c.stroke(); } }
  c.fillStyle="#7a889b"; c.font="10px system-ui"; c.fillText(fmt(hi,0)+"R",2,Y(hi)+8); c.fillText(fmt(lo,0)+"R",2,Y(lo));
  for (const y of [2020,2021,2022,2023,2024,2025,2026]){ const t = Date.UTC(y,0,1)/1000; if (t>t0&&t<t1){ c.fillText(y, X(t)-10, H-3); } }
  if (liveEq.length){ c.strokeStyle="#2ee59d"; c.lineWidth=2; c.beginPath(); liveEq.forEach((p,i)=>{ const y=Y((p[1]/10000-1)*100); i?c.lineTo(X(p[0]),y):c.moveTo(X(p[0]),y); }); c.stroke(); }
}

// ---------------- tables / feed ----------------
function renderSurvivors(){
  const S = REPLAY.symbols[SYM]; const m = S.survivor_metrics||[];
  const row = (lab, x) => `<tr><td>${lab}</td><td>${x.n}</td><td class="${x.ret_pct>=0?'pos':'neg'}">${sgn(x.ret_pct)}%</td><td>${fmt(x.max_dd_pct,1)}%</td><td>${fmt(x.pf)}</td><td>${fmt(x.win,0)}%</td><td>${sgn(x.net_r)}</td></tr>`;
  $("#surv").innerHTML = m.length ? m.map(s=>`<div class="scroll"><table><tr><th>#${s.bot_id} · weight ${(s.weight*100).toFixed(0)}%</th><th>trades</th><th>return</th><th>max DD</th><th>PF</th><th>win</th><th>net R</th></tr>
    ${row("Train 2020–23 (in-sample)", s.train)}${row("Valid 2024–Q1'25 (OOS)", s.valid)}${row("Holdout Apr'25–now (sealed)", s.holdout)}
    ${s.adj_holdout? row("Holdout, drift-adjusted", s.adj_holdout):""}<tr><td colspan=7 class="desc">${s.desc}</td></tr></table></div>`).join("")
    : `<div class="mut">No ${SYM} strategy survived the out-of-sample gates.</div>`;
  const wf = S.walk_forward_adj || S.walk_forward;
  $("#gates").innerHTML = `${S.tested.toLocaleString()} genomes tested · gates: plateau ${S.gates.plateau}/${S.gates.pool} → OOS valid ${S.gates.valid} → walk-forward ${S.gates.wf} → decorrelated ${S.gates.decor}.<br>
    Noise floor: ${S.noise.pass_valid_gate}/${S.noise.random_tested} random bots also pass the OOS gate.` + (wf? `<br>Walk-forward of the whole machine (drift-adjusted): ${wf.n} trades, PF ${fmt(wf.pf)}, net ${sgn(wf.net_r)}R, max DD ${fmt(wf.max_dd_pct,1)}%.`:"");
}
function renderFeed(){
  if (!STATE) return; const ev = STATE.events.filter(e=>{ try{ const d=JSON.parse(e.detail||"{}"); return !d.sym || d.sym===SYM; }catch{ return true; } }).slice(0,30);
  $("#feed").innerHTML = ev.length ? ev.map(e=>{ const d = JSON.parse(e.detail||"{}"); let t = "";
    if (e.kind==="open") t = `#${e.bot_id} opened ${d.dir>0?"LONG":"SHORT"} @ ${d.entry} (stop ${d.stop}, target ${d.tgt})`;
    else if (e.kind==="close") t = `#${e.bot_id} closed (${d.reason}) @ ${d.exit} → <b class="${d.r>=0?'pos':'neg'}">${sgn(d.r)}R</b>`;
    else if (e.kind==="birth") t = `<span class="pos">✦ #${e.bot_id} born</span> · ${d.family||""} ${d.origin||""}`;
    else if (e.kind==="kill") t = `<span class="neg">✕ #${e.bot_id} killed</span> · ${d.reason}`;
    else if (e.kind==="rebalance") t = `allocator rebalanced: ` + Object.entries(d).map(([k,v])=>`#${k} ${(v*100).toFixed(0)}%`).join(", ");
    return `<div><span class="t">${e.ts.slice(5,16).replace("T"," ")}</span>${t}</div>`; }).join("") : `<div class="mut">Waiting for the first live paper event…</div>`;
}

// ---------------- modes ----------------
function showFrame(){
  const S = REPLAY.symbols[SYM]; const f = S.frames[frameI]; if (!f) return;
  const next = S.frames[frameI+1];
  const list = f.desks.map(d=>({id:d.id, fam:d.fam, label:`isl ${f.island} · gen ${d.born}`, pnl:d.fit, pnlTxt:`fit ${fmt(d.fit)}`, w:0}));
  for (const d of f.desks){ const e = desks.get(d.id); if (e) e.willDie = d.dies!=null; }
  for (const [id,e] of desks) e.willDie = !list.find(x=>x.id===id) ? true : e.willDie;
  setDesks(list);
  $("#floor-sub").textContent = `${SYM} · backtest replay · island ${f.island} · generation ${f.gen} · top 12 of 160`;
  $("#mode").textContent = `REPLAY gen ${f.gen}`; $("#mode").className = "pill replay";
  drawTimeline();
}
function startReplay(){
  MODE = "replay"; clearInterval(replayTimer); frameI = 0; desks.clear();
  replayTimer = setInterval(()=>{ const S = REPLAY.symbols[SYM]; if (frameI >= S.frames.length){ clearInterval(replayTimer); showSurvivors(); return; } showFrame(); frameI++; }, 420);
}
function showSurvivors(){
  const S = REPLAY.symbols[SYM]; desks.forEach(d=>d.willDie=true);
  setDesks(S.survivors.map(s=>({id:s.id, fam:s.fam, label:"survivor · OOS passed", pnlTxt:`${(s.weight*100).toFixed(0)}% capital`, w:s.weight})));
  $("#floor-sub").textContent = `${SYM} · survivors of the out-of-sample gates receive capital`;
  setTimeout(()=>S.survivors.forEach(s=>flow(s.id, s.weight)), 700);
  setTimeout(goLive, 4500);
}
function goLive(){ MODE = "live"; clearInterval(replayTimer); $("#mode").textContent = "LIVE · PAPER"; $("#mode").className = "pill live"; poll(); }
function renderLive(){
  if (!STATE || MODE!=="live") return;
  const bots = STATE.bots.filter(b=>b.symbol===SYM && (b.status==="live"||b.status==="probation"));
  const killed = new Set(STATE.bots.filter(b=>b.status==="killed").map(b=>b.bot_id)); desks.forEach((e,id)=>e.willDie = killed.has(id));
  setDesks(bots.map(b=>{ const u = b.position&&b.position.unreal_r; return {id:b.bot_id, fam:b.family, w:b.weight,
    label:`${b.tf} · g${b.gen??"–"} · ${b.status==="probation"?"prob.":(b.weight*100).toFixed(0)+"%"}`,
    pnl:(b.live_r||0)+(u||0), pnlTxt:`${sgn(b.live_r||0)}R · ${b.live_n} tr${u!=null?` · open ${sgn(u)}R`:""}`, pos:b.position}; }));
  for (const b of bots){ const prev = lastWeights[b.bot_id]; if (prev==null || Math.abs(prev-b.weight)>0.005) flow(b.bot_id, Math.abs((b.weight||0)-(prev||0))); lastWeights[b.bot_id]=b.weight; }
  const t = STATE.ticks[SYM]; const hb = STATE.last_event;
  $("#floor-sub").textContent = `${SYM} · live paper floor · ${bots.length} bots` + (t?` · last tick ${ago(t.ts)} ago (${t.source})`:"");
}
async function poll(){
  try { STATE = await (await fetch("api/state",{cache:"no-store"})).json();
    for (const s of ["XAUUSD","BTCUSD"]){ const t = STATE.ticks[s]; $("#tk-"+s).textContent = t? `${s} ${s==="XAUUSD"? fmt(t.bid,2)+" / "+fmt(t.ask,2) : fmt(t.bid,1)} · ${ago(t.ts)}` : `${s} —`; }
    renderLive(); renderFeed(); drawEquity();
  } catch(e){ $("#mode").textContent = "OFFLINE"; $("#mode").className="pill"; }
}
setInterval(()=>{ if (MODE==="live") poll(); else fetch("api/state",{cache:"no-store"}).then(r=>r.json()).then(s=>{STATE=s; renderFeed();}).catch(()=>{}); }, 5000);

document.querySelectorAll("#tabs button[data-s]").forEach(b=>b.onclick=()=>{ document.querySelectorAll("#tabs button[data-s]").forEach(x=>x.classList.remove("on")); b.classList.add("on"); SYM=b.dataset.s; desks.clear(); lastWeights={}; renderSurvivors(); drawTimeline(); drawEquity(); MODE==="live"? renderLive() : startReplay(); });
$("#btn-replay").onclick = ()=>startReplay(); $("#btn-live").onclick = ()=>{ desks.clear(); lastWeights={}; goLive(); };
addEventListener("resize", ()=>{ drawTimeline(); drawEquity(); });

(async()=>{ REPLAY = await (await fetch("api/replay")).json();
  if (!REPLAY.run_id){ $("#floor-sub").textContent="No research run yet"; return; }
  renderSurvivors(); drawTimeline(); drawEquity(); requestAnimationFrame(drawFloor); poll().then(()=>{ MODE="replay"; startReplay(); }); })();
