// ULTRON floor view — plain JS + canvas. Main view: plain-English live paper floor. Details: backtest replay & stats.
const FAM = {donchian_breakout:["#ff4d5e","Br","Breakout"], bollinger_reversion:["#4da3ff","Sn","Snapback"],
  ema_trend_cross:["#2ee59d","TR","Trend Rider"], session_orb:["#f5c84c","OR","Opening Range"], rsi_pullback:["#b57bff","DB","Dip Buyer"],
  vwap_reversion:["#38d6e0","FP","Fair-Price Snapback"], momentum_burst:["#ff8a3d","MB","Momentum Burst"], ema_pullback:["#9be15d","TP","Trend Pullback"],
  lvl_retest:["#e06cff","LR","Level Retest"], lvl_sweep:["#ff5fa2","SF","Sweep Fader"], lvl_pullback:["#7ee0ff","LP","Level Pullback"], indicator_combo:["#ffd36e","Cx","Combo"]};
const $ = s => document.querySelector(s);
const ASSET = {XAUUSD:"Gold", BTCUSD:"Bitcoin"};
let SYM = "XAUUSD", VIEW = "floor", REPLAY = null, STATE = null, frameI = 0, replayTimer = null;
function mkFloor(id){ return {id, desks:new Map(), particles:[]}; }
const W_NARROW = () => innerWidth < 520;
const LIVE = mkFloor("floor"), REP = mkFloor("rfloor");
let lastWeights = {}, FLOOR = (location.hash.includes("scalp") ? "scalp" : "main"), RESULTS = null;

function fit(cv){ const r = cv.getBoundingClientRect(), d = devicePixelRatio||1; cv.width = Math.max(1,r.width*d); cv.height = Math.max(1,r.height*d); const c = cv.getContext("2d"); c.setTransform(d,0,0,d,0,0); return [c, r.width, r.height]; }
const fmt = (x, d=2) => x==null||isNaN(x) ? "—" : (+x).toFixed(d);
const sgn = x => (x>0?"+":"") + fmt(x);
const usd = x => (x<0?"−":x>0?"+":"") + "$" + Math.abs(x).toLocaleString(undefined,{minimumFractionDigits:2, maximumFractionDigits:2});
const ago = ts => { const s = Math.max(0, Date.now()/1000 - ts); return s<90? `${s|0}s` : s<5400? `${(s/60)|0}m` : `${(s/3600).toFixed(1)}h`; };
const esc = s => String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

$("#legend").innerHTML = Object.values(FAM).map(f=>`<span><i style="background:${f[0]}"></i>${f[2]}</span>`).join("") +
  `<span>✦ born</span><span style="color:#ff4d5e">✕ deleted</span><span style="color:#f5c84c">• money flow</span>`;

// ---------------- floor canvas (shared by live + replay) ----------------
function layout(n, W, H){ const cols = W < 520 ? 2 : 4, rows = Math.max(2, Math.ceil(n/cols)); const top = 60, cw = W/cols, ch = (H-top-6)/rows;
  return {cols, cw, ch, top, pos: i => [cw*(i%cols) + cw/2, top + ch*Math.floor(i/cols) + ch/2]}; }
function setDesks(F, list){
  const now = performance.now(); const ids = new Set(list.map(d=>d.id));
  for (const [id,d] of F.desks) if (!ids.has(id) && !d.dying) { d.dying = now; d.killed = !!d.willDie; }
  const used = new Set([...F.desks.values()].filter(d=>!d.dying).map(d=>d.slot));
  for (const d of list){ let e = F.desks.get(d.id);
    if (!e || e.dying){ let s=0; while(used.has(s)) s++; used.add(s); e = {slot:s, born:now}; F.desks.set(d.id, e); }
    Object.assign(e, d); }
}
function flow(F, toId, amount){ if (!F.desks.get(toId)) return; for (let i=0;i<Math.min(14, 2+amount*40);i++) F.particles.push({to:toId, t:-i*0.06, sp:0.6+Math.random()*0.5}); }
function drawFloor(F){
  const cv = document.getElementById(F.id); if (!cv.offsetParent) return;
  const [c, W, H] = fit(cv); const now = performance.now();
  const live = [...F.desks.values()].filter(d=>!d.dying); const L = layout(Math.max(live.length, 6), W, H);
  c.strokeStyle = "#121a25"; c.lineWidth = 1; for (let x=0;x<W;x+=24){c.beginPath();c.moveTo(x,0);c.lineTo(x,H);c.stroke();} for (let y=0;y<H;y+=24){c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}
  const ax = W/2, ay = 28, pulse = 1+0.08*Math.sin(now/300);
  const g = c.createRadialGradient(ax,ay,2,ax,ay,26*pulse); g.addColorStop(0,"#ff8a3d"); g.addColorStop(1,"rgba(255,42,61,0)"); c.fillStyle=g; c.beginPath(); c.arc(ax,ay,26*pulse,0,7); c.fill();
  c.fillStyle="#fff"; c.font="800 10px system-ui"; c.textAlign="center"; c.fillText("ULTRON", ax, ay+3);
  for (const [id,d] of F.desks){
    const [x,y] = L.pos(d.slot); d.x=x; d.y=y; let a = 1, sc = 1;
    const age = (now-d.born)/600; if (age<1){ sc = 0.4+0.6*easeOut(age); }
    if (d.dying){ const k=(now-d.dying)/900; if (k>1){ F.desks.delete(id); continue; } a = 1-k; sc = 1-0.4*k; }
    const w = Math.min(L.cw-14, 230), h = Math.min(L.ch-10, 80);
    c.save(); c.globalAlpha = a; c.translate(x,y); c.scale(sc,sc);
    c.fillStyle = "#121a26"; c.strokeStyle = d.pos? (d.pos.dir>0?"#2ee59d":"#ff4d5e") : "#223044"; c.lineWidth = d.pos?2:1;
    rr(c,-w/2,-h/2,w,h,10); c.fill(); c.stroke();
    if (d.w){ c.fillStyle="#f5c84c"; c.fillRect(-w/2+6, h/2-6, (w-12)*Math.min(1,d.w/0.25), 3); }
    const f = FAM[d.fam]||["#888","?"]; const r = Math.min(W<520?11:15, h/4);
    c.fillStyle = f[0]; c.beginPath(); c.arc(-w/2+7+r, -h/2+7+r, r, 0, 7); c.fill();
    c.fillStyle = "#07090d"; c.font = `800 ${r*0.85}px system-ui`; c.textAlign="center"; c.fillText(f[1], -w/2+7+r, -h/2+7+r*1.3);
    c.textAlign="left"; c.fillStyle="#d9e2ee"; c.font=`600 ${W<520?10:11}px system-ui`; clip(c, d.title||`#${id}`, -w/2+12+2*r, -h/2+16, w-2*r-16);
    if (h >= 62){ c.fillStyle="#7a889b"; c.font="10px system-ui"; clip(c, d.label||"", -w/2+12+2*r, -h/2+29, w-2*r-16); }
    c.font=`700 ${W<520?12:13}px system-ui`; c.fillStyle = d.pnl>0?"#2ee59d":d.pnl<0?"#ff4d5e":"#d9e2ee"; c.fillText(d.pnlTxt||"", -w/2+7, h/2-9);
    if (d.pos){ c.fillStyle = d.pos.dir>0?"#2ee59d":"#ff4d5e"; c.font="700 11px system-ui"; c.textAlign="right"; c.fillText(d.pos.dir>0?"▲ BUY":"▼ SELL", w/2-6, h/2-9); }
    if (age<1.6 && !d.dying){ c.strokeStyle=`rgba(46,229,157,${1-age/1.6})`; c.lineWidth=3; const k = 3+age*8; rr(c,-w/2-k,-h/2-k,w+2*k,h+2*k,12); c.stroke(); c.fillStyle="#2ee59d"; c.font="700 11px system-ui"; c.textAlign="center"; c.fillText("✦ BORN", 0, -h/2-3); }
    if (d.dying && d.killed){ c.strokeStyle="#ff4d5e"; c.lineWidth=4; c.beginPath(); c.moveTo(-w/3,-h/3); c.lineTo(w/3,h/3); c.moveTo(w/3,-h/3); c.lineTo(-w/3,h/3); c.stroke(); }
    c.restore();
  }
  for (let i=F.particles.length-1;i>=0;i--){ const p = F.particles[i]; p.t += 0.016*p.sp; const d = F.desks.get(p.to);
    if (!d || p.t>1){ F.particles.splice(i,1); continue; } if (p.t<0) continue;
    const t = easeOut(p.t), x = ax+(d.x-ax)*t, y = ay+(d.y-ay)*t - Math.sin(t*Math.PI)*30;
    c.fillStyle="#f5c84c"; c.beginPath(); c.arc(x,y,2.6,0,7); c.fill(); }
}
function clip(c, s, x, y, mw){ s = String(s); if (c.measureText(s).width <= mw){ c.fillText(s,x,y); return; } while (s.length>1 && c.measureText(s+"…").width>mw) s = s.slice(0,-1); c.fillText(s+"…",x,y); }
function loop(){ drawFloor(LIVE); drawFloor(REP); requestAnimationFrame(loop); }
const easeOut = t => 1-Math.pow(1-Math.min(1,Math.max(0,t)),3);
function rr(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}

// ---------------- main (plain) view ----------------
function nextLearn(){ const n = new Date(); const t = new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate(), 22)); if (t <= n) t.setUTCDate(t.getUTCDate()+1);
  return t.toLocaleTimeString("en-AU",{timeZone:"Australia/Sydney", hour:"numeric", minute:"2-digit"}).replace(":00","").replace(" ",""); }
function indRow(b){ return `<div class="inds">${b.indicators?`<span class="ind">📐 ${esc(b.indicators)}</span>`:""}${b.rr!=null?`<span class="ind rr${b.rr<2?" off":""}">R:R 1:${fmt(b.rr,1)}${b.rr<2?" (older bot, tested before the 1:2 rule)":""}</span>`:""}</div>`; }
function renderMain(){
  if (!STATE) return; const S = STATE.summary, bots = STATE.bots.filter(b=>b.status==="live"||b.status==="probation");
  const fresh = Object.values(STATE.ticks).some(t=>Date.now()/1000 - t.ts < 300);
  const br = S.breeder; const N = x => (x||0).toLocaleString();
  const top = br && br.top_reject ? ` · top reject reason: ${esc(br.top_reject)}${br.top_reject_share!=null?` (${br.top_reject_share}%)`:""}` : "";
  const breed = br ? (br.running ? `<b>Breeding:</b> tested ${N(br.tested_last_hour)} in the last hour${top}<br><span class="mut">${N(br.tested_today)} tested today · ${N(br.promoted_today)} promoted · ${N(br.cut_today)} cut</span>` : `Breeder restarting…`) : `Breeder starting…`;
  const stale = STATE.stale; const hbm = STATE.heartbeat_age_s==null ? "never" : `${Math.round(STATE.heartbeat_age_s/60)} min ago`;
  const live = stale ? `<b style="color:#e5a33b">⚠ Floor data is stale — the live trader's last heartbeat was ${hbm}</b>` : (fresh?"Paper trading live":"Paper floor running (markets quiet)");
  $("#status").innerHTML = `<span class="dot" style="${fresh&&!stale?"":"background:#7a889b;box-shadow:none"}"></span>${breed}<br><span class="mut">${live} · ${S.bots_alive} bots · ${S.open_trades} in a trade now</span>`;
  const p = S.paper_profit_usd; $("#n-profit").textContent = usd(p); $("#n-profit").className = "val " + (p>0?"pos":p<0?"neg":"");
  $("#n-bots").textContent = S.bots_alive; $("#n-bots-sub").textContent = br ? `${N(br.promoted_today)} new · ${N(br.cut_today)} cut today` : "";
  $("#n-trades").textContent = S.trades_today;
  const tph = S.trades_per_hour_expected;
  $("#n-tph").textContent = tph==null ? "–" : (tph < 10 ? tph.toFixed(1) : Math.round(tph));
  $("#n-tph-sub").textContent = `expected from history · actual ${fmt(S.trades_per_hour_actual,1)}/h (last 24h)`;
  // floor-wide risk
  const R = S.risk;
  if (R){ const used = R.open_risk_pct, cap = R.max_total_open_risk_pct;
    $("#risk").innerHTML = `<div class="riskrow"><b>Risk in use: ${fmt(used,1)}% of ${fmt(cap,0)}%</b><span class="mut">${R.open_trades} of ${R.max_open_trades} trades open</span></div>
      <div class="bar risk"><i style="width:${Math.min(100, used/cap*100)}%"></i></div>
      <div class="mut small">Today's floor result ${R.day_pnl_pct>=0?"+":""}${fmt(R.day_pnl_pct,2)}% (new trades stop for the day at −${fmt(R.daily_floor_loss_limit_pct,0)}%) · max ${R.max_same_direction_per_symbol} trades the same way per market · max ${R.max_bots_on_floor} bots${R.blocked_today?` · ${R.blocked_today} signals blocked by floor limit today`:""}</div>
      ${R.halted?`<div class="halt">⛔ Floor loss limit hit — no new trades until tomorrow (Sydney time)</div>`:""}`; }
  // floor canvas
  const killed = new Set(STATE.bots.filter(b=>b.status==="killed").map(b=>b.bot_id)); LIVE.desks.forEach((e,id)=>e.willDie = killed.has(id));
  setDesks(LIVE, bots.map(b=>{ const tot = b.profit_usd + (b.unreal_usd||0); return {id:b.bot_id, fam:b.family, w:b.weight, title: W_NARROW() ? b.name.replace(/^(Gold|Bitcoin) /, (m,a)=>a==="Gold"?"🥇":"₿ ") : b.name,
    label: `${b.tf} · ${b.status==="probation"?"on trial":(b.weight*100).toFixed(0)+"% of money"}`,
    pnl: tot, pnlTxt: usd(tot), pos:b.position}; }));
  for (const b of bots){ const prev = lastWeights[b.bot_id]; if (prev==null || Math.abs(prev-b.weight)>0.005) flow(LIVE, b.bot_id, Math.abs((b.weight||0)-(prev||0))); lastWeights[b.bot_id]=b.weight; }
  // leaderboard (by paper profit)
  const B = S.leaderboard || [];
  $("#board").innerHTML = B.length ? B.map((x,i)=>`<div class="row"><span class="rk">${i+1}</span><span class="rn">${esc(x.name)}${x.status==="probation"?'<span class="badge new">TRIAL</span>':''}<br><span class="mut small">${x.trades} paper trade${x.trades===1?"":"s"} so far</span></span><span class="rp ${x.profit_usd>0?"pos":x.profit_usd<0?"neg":""}">${usd(x.profit_usd)}</span></div>`).join("")
    : `<div class="mut">No bots yet.</div>`;
  // bot cards
  $("#cards").innerHTML = bots.map(b=>{ const f = FAM[b.family]||["#888","?"]; const tot = b.profit_usd + (b.unreal_usd||0);
    const doing = b.position ? `<div class="doing ${b.position.dir>0?"buy":"sell"}">${esc(b.doing)}${b.unreal_usd?` · open ${usd(b.unreal_usd)}`:""}</div>` : `<div class="doing wait">Waiting</div>`;
    const newb = b.status==="probation" ? '<span class="badge new">NEW · on trial</span>' : "";
    const e = b.expected_trades_per_day;
    const freq = e==null ? "" : `<div class="mut small">Usually about ${e < 1 ? (e*7).toFixed(1)+" trades a week" : e.toFixed(1)+" trades a day"} · ${b.live_n} so far</div>`;
    return `<div class="bot" style="--c:${f[0]}"><div class="top"><div class="av">${f[1]}</div><div><div class="nm">${esc(b.name)}${newb}</div><div class="mut small">${b.tf} · generation ${b.gen??"–"} · gets ${(b.weight*100).toFixed(0)}% of the money</div></div>
      <div class="pf ${tot>0?"pos":tot<0?"neg":""}">${usd(tot)}</div></div>${doing}${indRow(b)}<div class="rule">${esc(b.rule)}</div>${freq}</div>`; }).join("");
  const xb = $("#scalpinfo");
  if (S.experimental){ xb.hidden = false;
    xb.innerHTML = `<b>⚗ Scalp Pit · EXPERIMENTAL</b> · separate $10,000 paper account · 1-minute bots trading around 1h, 4h, daily, session and round-number levels, holding 6–45 minutes. Same strict test as the main floor (profitable in training and on the unseen 6 months after costs) plus beating 75% of random bots.
      <br>Trading costs paid (spread, commission, slippage): <b class="neg">${usd(-S.costs_paid_today_usd)}</b> today · <b class="neg">${usd(-S.costs_paid_usd)}</b> total · profit before costs ${usd(S.gross_before_costs_usd)}`; }
  else xb.hidden = true;
  renderFeed();
}
function renderFeed(){
  if (!STATE) return; const ev = STATE.events.slice(0,25);
  const nm = e => esc(e.name || ("bot #"+e.bot_id));
  $("#feed").innerHTML = ev.length ? ev.map(e=>{ const d = JSON.parse(e.detail||"{}"); let t = "";
    const a = d.sym ? ASSET[d.sym].toLowerCase() : "";
    if (e.kind==="open") t = `${nm(e)} ${d.dir>0?"bought":"sold"} ${a} at ${d.entry}`;
    else if (e.kind==="close") t = `${nm(e)} closed its ${a} trade (${({stop:"hit its safety stop",target:"hit its profit target",time:"time limit",flip:"signal reversed","challenge breach":"challenge limit hit","bot cut":"bot was cut"})[d.reason]||d.reason}) <b class="${d.r>=0?'pos':'neg'}">${d.r>=0?"win":"loss"}</b>`;
    else if (e.kind==="birth") { const h = d.holdout; t = `<span class="pos">✦ ${nm(e)} was promoted</span>${d.origin==="backtest survivor"?"":h?` · why: on the last 6 months it never trained on it made <b>${h.net_r>=0?"+":""}${fmt(h.net_r,1)}R</b> over ${h.n} trades (profit factor ${fmt(h.pf,2)}), after costs; also passed the history, stability and not-a-copy tests · starts on trial`:" · passed the recent-history test, starting its trial"}`; }
    else if (e.kind==="kill") t = `<span class="neg">✕ ${nm(e)} was deleted</span> · ${esc(d.reason)}`;
    else if (e.kind==="rebalance") t = `Ultron re-shared the money between the bots`;
    else if (e.kind==="graduated") t = `<span class="pos">🎓 ${nm(e)} finished its trial and now gets full money</span>`;
    else if (e.kind==="blocked") t = `<span class="mut">${nm(e)} wanted to ${d.dir>0?"buy":"sell"} ${a} — blocked by floor limit (${esc(d.reason)})</span>`;
    else if (e.kind==="floor_halt") t = `<span class="neg">⛔ Floor lost ${fmt(-d.day_pnl_pct,1)}% today — new trades paused until tomorrow</span>`;
    else if (e.kind==="challenge_failed") t = `<span class="neg">${nm(e)} failed its practice challenge</span> · ${esc(d.reason)}`;
    else if (e.kind==="challenge_phase1_passed") t = `<span class="pos">${nm(e)} passed challenge phase 1!</span>`;
    else if (e.kind==="challenge_passed") t = `<span class="pos">🏆 ${nm(e)} passed the full challenge!</span>`;
    else if (e.kind==="challenge_restart") t = `${nm(e)} started a new practice challenge (attempt ${d.attempt})`;
    else t = esc(e.kind);
    const when = new Date(e.ts.replace(/(\d\d)(\d\d)$/,"$1:$2")).toLocaleString("en-AU",{timeZone:"Australia/Sydney", weekday:"short", hour:"numeric", minute:"2-digit"});
    return `<div><span class="t">${when}</span>${t}</div>`; }).join("") : `<div class="mut">Nothing yet. Bots are waiting for their setups.</div>`;
}

// ---------------- details: replay, timeline, equity, survivors ----------------
function drawTimeline(){
  if (!REPLAY || VIEW!=="details") return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#timeline"));
  const gens = S.generations; if (!gens.length) return;
  const mx = Math.max(...gens.map(g=>g.gen)), lo = Math.min(...gens.map(g=>g.median)), hi = Math.max(...gens.map(g=>g.best));
  const X = (isl,gen) => 30 + (W-40)*((isl*(mx+1)+gen)/((mx+1)*4)), Y = v => H-18-(H-30)*(v-lo)/(hi-lo||1);
  c.strokeStyle="#1c2532"; c.beginPath(); c.moveTo(30,Y(0)); c.lineTo(W-10,Y(0)); c.stroke(); c.fillStyle="#7a889b"; c.font="10px system-ui"; c.fillText("0", 18, Y(0)+3);
  for (let isl=0;isl<4;isl++){ const gi = gens.filter(g=>g.island===isl); if(!gi.length) continue;
    for (const [k,col] of [["median","#4da3ff"],["best","#ff4d5e"]]){ c.strokeStyle=col; c.lineWidth=1.6; c.beginPath(); gi.forEach((g,i)=>{ const x=X(isl,g.gen), y=Y(g[k]); i?c.lineTo(x,y):c.moveTo(x,y); }); c.stroke(); }
    c.fillStyle="#7a889b"; c.fillText(`island ${isl}`, X(isl,0)+2, 12); }
  const f = S.frames[Math.min(frameI, S.frames.length-1)]; if (f && replayTimer){ c.strokeStyle="#f5c84c"; c.beginPath(); c.moveTo(X(f.island,f.gen),16); c.lineTo(X(f.island,f.gen),H-14); c.stroke(); }
  c.fillStyle="#ff4d5e"; c.fillText("best", W-60, H-4); c.fillStyle="#4da3ff"; c.fillText("median", W-34, H-4);
}
function drawEquity(){
  if (!REPLAY || VIEW!=="details") return; const S = REPLAY.symbols[SYM]; const [c,W,H] = fit($("#equity"));
  const curves = Object.entries(S.curves);
  if (!curves.length){ c.fillStyle="#7a889b"; c.font="13px system-ui"; c.fillText("No "+ASSET[SYM].toLowerCase()+" strategy passed the out-of-sample tests", 14, 30); return; }
  const all = curves.flatMap(([,p])=>p); if (!all.length) return;
  const t0 = Math.min(...all.map(p=>p[0])), t1 = Math.max(...all.map(p=>p[0]));
  const vs = all.map(p=>p[1]), lo = Math.min(0,...vs), hi = Math.max(1,...vs);
  const X = t => 34+(W-44)*(t-t0)/(t1-t0||1), Y = v => H-16-(H-28)*(v-lo)/(hi-lo||1);
  c.strokeStyle="#1c2532"; c.beginPath(); c.moveTo(34,Y(0)); c.lineTo(W,Y(0)); c.stroke();
  const col = {t:"#7f8ea3", v:"#4da3ff", h:"#f5c84c"};
  for (const [,pts] of curves){ for (let i=1;i<pts.length;i++){ c.strokeStyle = col[pts[i][2]]||"#888"; c.lineWidth=1.6; c.beginPath(); c.moveTo(X(pts[i-1][0]),Y(pts[i-1][1])); c.lineTo(X(pts[i][0]),Y(pts[i][1])); c.stroke(); } }
  c.fillStyle="#7a889b"; c.font="10px system-ui"; c.fillText(fmt(hi,0)+"R",2,Y(hi)+8); c.fillText(fmt(lo,0)+"R",2,Y(lo));
  for (const y of [2024,2025,2026]){ const t = Date.UTC(y,0,1)/1000; if (t>t0&&t<t1){ c.fillText(y, X(t)-10, H-3); } }
}
function renderSurvivors(){
  const S = REPLAY.symbols[SYM]; const m = S.survivor_metrics||[]; const W = REPLAY.windows;
  const row = (lab, x) => `<tr><td>${lab}</td><td>${x.n}</td><td class="${x.ret_pct>=0?'pos':'neg'}">${sgn(x.ret_pct)}%</td><td>${fmt(x.max_dd_pct,1)}%</td><td>${fmt(x.pf)}</td><td>${fmt(x.win,0)}%</td><td>${sgn(x.net_r)}</td></tr>`;
  const ch = s => s.challenge ? `<tr><td colspan=7 class="desc">FTMO practice challenges pass / breach: ${["train","valid","holdout"].map(w=>{const c=s.challenge.FTMO[w]; return `${w} ${(c.pass_rate*100).toFixed(0)}% / ${(c.breach_rate*100).toFixed(0)}%`;}).join(" · ")}</td></tr>` : "";
  $("#surv").innerHTML = m.length ? m.map(s=>`<div class="scroll"><table><tr><th>#${s.bot_id} · weight ${(s.weight*100).toFixed(0)}%</th><th>trades</th><th>return</th><th>max DD</th><th>PF</th><th>win</th><th>net R</th></tr>
    ${row(`Train ${W.train[0].slice(0,7)}…${W.train[1].slice(0,7)}`, s.train)}${row("Validation (unseen)", s.valid)}${row("Held-back (sealed)", s.holdout)}
    ${s.adj_holdout? row("Held-back, drift-adjusted", s.adj_holdout):""}${ch(s)}<tr><td colspan=7 class="desc">${esc(s.desc)}</td></tr></table></div>`).join("")
    : `<div class="mut">No ${ASSET[SYM].toLowerCase()} strategy survived the out-of-sample tests.</div>`;
  const wf = S.walk_forward_adj || S.walk_forward;
  $("#gates").innerHTML = `${S.tested.toLocaleString()} strategies tested · passed: stability ${S.gates.plateau}/${S.gates.pool} → validation ${S.gates.valid} → walk-forward ${S.gates.wf} → not-duplicates ${S.gates.decor}.<br>
    Random-bot baseline: ${S.noise.pass_valid_gate}/${S.noise.random_tested} random bots also pass validation.` + (wf? `<br>Walk-forward of the whole machine (drift-adjusted): ${wf.n} trades, PF ${fmt(wf.pf)}, net ${sgn(wf.net_r)}R, max DD ${fmt(wf.max_dd_pct,1)}%.`:"");
}
function showFrame(){
  const S = REPLAY.symbols[SYM]; const f = S.frames[frameI]; if (!f) return;
  const list = f.desks.map(d=>({id:d.id, fam:d.fam, title:(FAM[d.fam]||[,,d.fam])[2], label:`island ${f.island} · born gen ${d.born}`, pnl:d.fit, pnlTxt:`score ${fmt(d.fit)}`, w:0}));
  for (const d of f.desks){ const e = REP.desks.get(d.id); if (e) e.willDie = d.dies!=null; }
  for (const [id,e] of REP.desks) e.willDie = !list.find(x=>x.id===id) ? true : e.willDie;
  setDesks(REP, list);
  $("#rfloor-sub").textContent = `${ASSET[SYM]} · island ${f.island} · generation ${f.gen} · top 12`;
  drawTimeline();
}
function startReplay(){
  clearInterval(replayTimer); frameI = 0; REP.desks.clear();
  replayTimer = setInterval(()=>{ const S = REPLAY.symbols[SYM]; if (frameI >= S.frames.length){ clearInterval(replayTimer); replayTimer=null; showSurvivors(); return; } showFrame(); frameI++; }, 300);
}
function showSurvivors(){
  const S = REPLAY.symbols[SYM]; REP.desks.forEach(d=>d.willDie=true);
  setDesks(REP, S.survivors.map(s=>({id:s.id, fam:s.fam, title:(FAM[s.fam]||[,,s.fam])[2], label:"survivor · passed tests", pnlTxt:`${(s.weight*100).toFixed(0)}% of money`, w:s.weight})));
  $("#rfloor-sub").textContent = `${ASSET[SYM]} · ${S.survivors.length} survivors of the out-of-sample tests receive money`;
  setTimeout(()=>S.survivors.forEach(s=>flow(REP, s.id, s.weight)), 700); drawTimeline();
}
// robust polling: setTimeout chain (not setInterval), 8 s request timeout, backoff retry on errors / server restarts,
// immediate re-poll when the tab becomes visible again (iOS Safari freezes timers in background tabs).
let lastOk = 0, failCount = 0, inflight = null, pollTimer = null;
const POLL_MS = 4000;
function schedule(ms){ clearTimeout(pollTimer); pollTimer = setTimeout(poll, ms); }
function wake(){ if (document.visibilityState !== "hidden"){ if (inflight){ inflight.abort(); inflight = null; } poll(); } }
document.addEventListener("visibilitychange", wake); addEventListener("pageshow", wake); addEventListener("focus", wake); addEventListener("online", wake);
function liveInd(){ const el = $("#liveind"); if (!el) return; const age = lastOk ? (Date.now()-lastOk)/1000 : null;
  if (age==null){ el.className = "liveind amber"; el.textContent = failCount ? "Reconnecting…" : "Connecting…"; return; }
  el.className = "liveind " + (age > 30 ? "amber" : "ok");
  el.textContent = (age > 30 ? (failCount ? "Reconnecting · last update " : "Stale · updated ") : "Live · updated ") + (age < 1.5 ? "just now" : `${Math.round(age)}s ago`); }
setInterval(liveInd, 1000);
async function poll(){
  clearTimeout(pollTimer);
  if (inflight) return;
  const ctl = new AbortController(); inflight = ctl; const to = setTimeout(()=>ctl.abort(), 8000);
  try { const f = FLOOR; const res = await fetch(`api/state?floor=${f}&_=${Date.now()}`,{cache:"no-store", signal: ctl.signal});
    if (!res.ok) throw new Error("HTTP " + res.status);
    const st = await res.json(); if (f !== FLOOR) return; STATE = st; lastOk = Date.now(); failCount = 0;
    for (const s of ["XAUUSD","BTCUSD"]){ const t = STATE.ticks[s]; $("#tk-"+s).textContent = t? `${ASSET[s]} ${s==="XAUUSD"? fmt(t.bid,2)+" / "+fmt(t.ask,2) : fmt(t.bid,1)} · ${ago(t.ts)} ago` : `${s} —`; }
    renderMain(); if (VIEW==="results") loadResults(); if (VIEW==="trades") renderTrades(); if (VIEW==="learned") loadLearned();
  } catch(e){ failCount++; if (!STATE) $("#status").textContent = "Can't reach the floor right now — retrying…"; }
  finally { clearTimeout(to); if (inflight === ctl) inflight = null; liveInd();
    schedule(failCount ? Math.min(15000, 1500 * failCount) : (document.visibilityState === "hidden" ? 15000 : POLL_MS)); }
}

function setFloor(f){ if (f===FLOOR) return; FLOOR = f; STATE = null; RESULTS = null; LIVE.desks.clear(); LIVE.particles.length = 0; lastWeights = {};
  document.querySelectorAll("#floortabs button").forEach(b=>b.classList.toggle("on", b.dataset.f===f));
  document.body.classList.toggle("scalp", f==="scalp"); history.replaceState(null, "", f==="scalp" ? "#scalp" : "#");
  $("#board").innerHTML = $("#cards").innerHTML = $("#feed").innerHTML = $("#risk").innerHTML = ""; $("#status").textContent = "Loading…";
  $("#tr-open").innerHTML = $("#tr-closed").innerHTML = ""; LEARNED = null;
  if (inflight){ inflight.abort(); inflight = null; } poll(); if (VIEW==="results") loadResults(); if (VIEW==="learned") loadLearned(true); }
document.querySelectorAll("#floortabs button").forEach(b=>b.onclick=()=>setFloor(b.dataset.f));
if (FLOOR==="scalp"){ document.body.classList.add("scalp"); document.querySelectorAll("#floortabs button").forEach(b=>b.classList.toggle("on", b.dataset.f==="scalp")); }

// ---------------- results (why each bot was promoted, live since, recent kills) ----------------
const syd = ts => new Date(ts).toLocaleString("en-AU",{timeZone:"Australia/Sydney", day:"numeric", month:"short", hour:"numeric", minute:"2-digit"}) + " Sydney";
const rr2 = x => (x>0?"+":"") + fmt(x,1) + "R";
function testLine(lab, m){ return `<tr><td>${lab}</td><td>${m.n}</td><td class="${m.net_r>0?"pos":m.net_r<0?"neg":""}">${rr2(m.net_r)}<br><span class="mut small">${m.ret_pct>=0?"+":""}${fmt(m.ret_pct,1)}% at 1% risk</span></td><td>${fmt(m.win,0)}%<br><span class="mut small">${m.wins} won / ${m.losses} lost</span></td><td>${fmt(m.pf,2)}</td><td class="neg">${fmt(m.max_dd_pct,1)}%</td></tr>`; }
function whyCard(b){
  const e = b.evidence, L = b.live, f = FAM[b.family]||["#888","?"];
  const head = `<div class="top"><div class="av" style="background:${f[0]}">${f[1]}</div><div><div class="nm">${esc(b.name)}${b.status==="probation"?'<span class="badge new">on trial</span>':''}${FLOOR==="scalp"?'<span class="badge exp">experimental</span>':''}</div>
    <div class="mut small">${b.tf} · promoted ${b.added_ts?syd(b.added_ts):"–"}${b.origin==="breeder"?" by the nonstop breeder":" from the research run"}</div></div></div>${indRow(b)}<div class="rule">${esc(b.rule)}</div>`;
  if (!e) return `<div class="bot why" style="--c:${f[0]}">${head}<div class="mut">Test numbers are being recorded…</div></div>`;
  const g = e.gate, H = e.holdout, T = e.train, R = e.random;
  const why = `<p><b>Why it got in:</b> on ${e.train_window[0].slice(0,4)}–${e.train_window[1].slice(0,7)} history it made <b>${rr2(T.net_r)}</b> over ${T.n} trades (won ${T.wins}, lost ${T.losses}), after trading costs.
    It stayed profitable when its settings were nudged. On the <b>last 6 months it never trained on</b> it made <b>${rr2(H.net_r)}</b> over ${H.n} trades (${fmt(H.win,0)}% wins, worst drop ${fmt(H.max_dd_pct,1)}%),
    which beat <b>${fmt(R.beat_pct,0)}% of ${R.n} random bots</b> on the same months (the typical random bot made ${rr2(R.median_net_r)}).
    The bar on this floor: at least ${g.n} recent trades, profit factor ≥ ${g.pf}, net ${g.net_r>=0?"> "+g.net_r+"R":"≥ "+g.net_r+"R"}, worst drop no worse than ${g.dd_pct}%${g.beat_random_pct?`, and beat at least ${g.beat_random_pct}% of random bots`:""}${FLOOR==="scalp"?" — a deliberately <b>lenient</b> bar for this experimental pit":""}; and it is not a copy of a bot already on the floor.${e.backfilled?' <span class="mut">(numbers re-checked after it joined)</span>':''}</p>`;
  const tbl = `<div class="scroll"><table><tr><th>Test</th><th>trades</th><th>profit</th><th>win rate</th><th>profit factor</th><th>worst drop</th></tr>${testLine("History (training)", T)}${testLine("Last 6 months (holdout)", H)}</table></div>`;
  const live = `<div class="livebox"><b>Live paper results since promotion:</b> ${L.trades ? `${L.trades} trades · ${L.wins} won / ${L.losses} lost (${fmt(L.win_pct,0)}%) · <b class="${L.profit_usd>0?"pos":L.profit_usd<0?"neg":""}">${usd(L.profit_usd)}</b> (${rr2(L.net_r)}) · costs paid ${usd(-L.costs_usd)} · worst drop ${fmt(L.worst_drop_r,1)}R` : "no finished trades yet"}</div>`;
  return `<div class="bot why" style="--c:${f[0]}">${head}${why}${tbl}${live}</div>`;
}
async function loadResults(){
  try { const f = FLOOR; const r = await (await fetch(`api/results?floor=${f}`,{cache:"no-store"})).json(); if (f !== FLOOR) return; RESULTS = r; } catch(e){ return; }
  const R = RESULTS;
  $("#res-title").textContent = FLOOR==="scalp" ? "Scalp Pit · experimental" : "Main Floor";
  $("#res-promoted").innerHTML = R.promoted.length ? R.promoted.map(whyCard).join("") : `<div class="card mut">No bots promoted yet — the breeder is still testing.</div>`;
  $("#res-killed").innerHTML = R.killed.length ? R.killed.map(b=>`<div class="row"><span class="rn">✕ ${esc(b.name)} <span class="mut small">${b.tf} · cut ${b.removed_ts?syd(b.removed_ts):""}</span><br><span class="mut small">${esc(b.reason||"")}${b.live.trades?` · live: ${b.live.trades} trades, ${usd(b.live.profit_usd)}`:""}</span></span></div>`).join("") : `<div class="mut">No bots cut yet.</div>`;
}


// ---------------- live trades ----------------
const px = (sym, x) => x==null ? "—" : (+x).toLocaleString(undefined, {minimumFractionDigits: sym==="XAUUSD"?2:1, maximumFractionDigits: sym==="XAUUSD"?2:1});
const sydT = ts => new Date(ts*1000).toLocaleString("en-AU",{timeZone:"Australia/Sydney", day:"numeric", month:"short", hour:"numeric", minute:"2-digit", second:"2-digit"});
const dur2 = s => { s = Math.max(0, s|0); const h = (s/3600)|0, m = ((s%3600)/60)|0; return h ? `${h}h ${m}m` : `${m}m ${s%60}s`; };
function renderTrades(){
  if (!STATE) return; const now = Date.now()/1000;
  const open = STATE.bots.filter(b=>b.position && (b.status==="live"||b.status==="probation"));
  $("#tr-title").textContent = FLOOR==="scalp" ? "Scalp Pit · experimental" : "Main Floor";
  $("#tr-open").innerHTML = open.length ? `<div class="scroll"><table class="trades"><tr><th>Bot</th><th>Market</th><th>Side</th><th>Opened (Sydney)</th><th>Entry</th><th>Now</th><th>Stop loss</th><th>Take profit</th><th>R:R</th><th>Size</th><th>Risk</th><th>Live P&amp;L</th><th>Open for</th></tr>` +
    open.map(b=>{ const p = b.position, s = b.symbol, u = b.unreal_usd||0;
      return `<tr><td>${esc(b.name)}</td><td>${ASSET[s]}</td><td class="${p.dir>0?"pos":"neg"}"><b>${p.dir>0?"BUY":"SELL"}</b></td><td>${sydT(p.since)}</td><td>${px(s,p.entry)}</td><td>${px(s,p.current)}</td><td>${px(s,p.stop)}</td><td>${px(s,p.tgt)}</td><td>${p.rr!=null?"1:"+fmt(p.rr,1):"—"}</td>
        <td>${p.size!=null?fmt(p.size, s==="XAUUSD"?2:4)+" "+p.unit:"—"}</td><td>${p.risk_usd!=null?usd(p.risk_usd).replace("+",""):"—"}</td><td class="${u>0?"pos":u<0?"neg":""}"><b>${usd(u)}</b></td><td>${dur2(now-p.since)}</td></tr>`; }).join("") + `</table></div>`
    : `<div class="empty">No trades open right now.</div>`;
  const R = {stop:"stop loss", target:"take profit", time:"time limit", flip:"signal reversed", "bot cut":"bot was cut"};
  const closed = (STATE.trades||[]).slice(-60).reverse();
  $("#tr-closed").innerHTML = closed.length ? `<div class="scroll"><table class="trades"><tr><th>Bot</th><th>Market</th><th>Side</th><th>Opened</th><th>Entry</th><th>Closed</th><th>Exit</th><th>R:R</th><th>Result</th><th>Why it closed</th></tr>` +
    closed.map(t=>`<tr><td>${esc(t.name)}</td><td>${ASSET[t.symbol]}</td><td class="${t.dir>0?"pos":"neg"}">${t.dir>0?"BUY":"SELL"}</td><td>${sydT(t.entry_ts)}</td><td>${px(t.symbol,t.entry)}</td><td>${sydT(t.exit_ts)}</td><td>${px(t.symbol,t.exit)}</td><td>${t.rr!=null?"1:"+fmt(t.rr,1):"—"}</td>
      <td class="${t.usd>0?"pos":t.usd<0?"neg":""}"><b>${usd(t.usd)}</b></td><td>${esc(R[t.reason]||t.reason)}</td></tr>`).join("") + `</table></div>`
    : `<div class="empty">No closed trades yet on this floor.</div>`;
}

// ---------------- what Ultron learned ----------------
let LEARNED = null, learnedAt = 0;
async function loadLearned(force){
  if (!force && LEARNED && LEARNED.floor===FLOOR && Date.now()-learnedAt < 60000) return renderLearned();
  try { const f = FLOOR; const r = await (await fetch(`api/learned?floor=${f}`,{cache:"no-store"})).json(); if (f!==FLOOR) return; LEARNED = r; learnedAt = Date.now(); } catch(e){ return; }
  renderLearned();
}
function tbl(head, rows){ return `<div class="scroll"><table><tr>${head.map(h=>`<th>${h}</th>`).join("")}</tr>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</table></div>`; }
function lineChart(id, series, labels, colors, unit){
  const cv = document.getElementById(id); if (!cv) return; const [c, W, H] = fit(cv); c.clearRect(0,0,W,H);
  const all = series.flat().filter(v=>v!=null); if (!all.length || series[0].length < 2){ c.fillStyle="#7a889b"; c.font="12px system-ui"; c.fillText("Not enough hours of data yet for a chart", 10, 20); return; }
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi-lo)*0.1 || 1, y0 = lo-pad, y1 = hi+pad, n = series[0].length;
  const X = i => 40 + (W-50)*i/(n-1), Y = v => H-22 - (H-34)*(v-y0)/(y1-y0);
  c.strokeStyle="#1a2433"; c.fillStyle="#7a889b"; c.font="10px system-ui";
  for (let k=0;k<=3;k++){ const v = y0+(y1-y0)*k/3; c.beginPath(); c.moveTo(40,Y(v)); c.lineTo(W-10,Y(v)); c.stroke(); c.fillText(fmt(v,1)+unit, 2, Y(v)+3); }
  c.fillText(labels[0], 40, H-6); c.textAlign="right"; c.fillText(labels[n-1], W-10, H-6); c.textAlign="left";
  series.forEach((sr,j)=>{ c.strokeStyle = colors[j]; c.lineWidth = 2; c.beginPath(); let st=false; sr.forEach((v,i)=>{ if (v==null) return; st ? c.lineTo(X(i),Y(v)) : c.moveTo(X(i),Y(v)); st = true; }); c.stroke(); });
}
function renderLearned(){
  const L = LEARNED; if (!L) return;
  $("#ln-title").textContent = FLOOR==="scalp" ? "Scalp Pit · experimental" : "Main Floor";
  $("#ln-basis").innerHTML = `Based on <b>${N(L.sample)}</b> bots Ultron tested in the last ${L.hours} hours (a 35% random sample of everything tested is logged), every gate run, and every bot on this floor. Claims about traits only use the randomly created bots, so Ultron's own favourites can't flatter themselves. A claim needs at least ${L.markets.XAUUSD?.min_sample||1000} random bots per market.`;
  const KIND = {winning:"✅ Winning", dying:"🪦 Dying out", setting:"⚙️ Settings", "kill pattern":"✕ Why bots die", trend:"📈 Shifting"};
  $("#ln-lessons").innerHTML = L.lessons.length ? L.lessons.map(l=>`<div class="lesson"><span class="mut small">${new Date(l.ts).toLocaleString("en-AU",{timeZone:"Australia/Sydney", day:"numeric", month:"short", hour:"numeric", minute:"2-digit"})}</span> ${esc(l.text)} ${l.still_true?'<span class="badge pass">still true</span>':'<span class="badge">no longer seen</span>'}</div>`).join("")
    : `<div class="empty">No lessons yet — Ultron writes one down the first time the data clearly shows something (checked every 30 minutes).</div>`;
  $("#ln-now").innerHTML = L.claims.length ? L.claims.map(c=>`<div class="lesson"><b>${KIND[c.kind]||c.kind}</b> · ${esc(c.text)}</div>`).join("") : `<div class="empty">Nothing clear enough to claim yet.</div>`;
  const N1 = x => (+x).toLocaleString();
  $("#ln-markets").innerHTML = ["XAUUSD","BTCUSD"].map(sym=>{ const m = L.markets[sym]||{}; const lift = (rows)=> tbl(["", "random bots tested","% of tested","% of best 5%","× more likely"], (rows||[]).map(r=>[esc(r.value), N1(r.tested), r.share_all+"%", r.share_best+"%", `<b class="${r.lift>=1.5?"pos":r.lift<=0.4?"neg":""}">${fmt(r.lift,1)}×</b>`]));
    return `<section class="card"><div class="ct"><b>${ASSET[sym]}</b><span class="mut">${N1(m.tested_sample||0)} logged · ${N1(m.random_sample||0)} random${m.enough?"":` (claims start at ${N1(m.min_sample)})`}${m.share_positive_fit!=null?` · ${m.share_positive_fit}% scored above zero on history`:""}${m.gate?` · ${m.gate.runs} reached the gate, ${m.gate.passed_holdout} passed the 6-month test, ${m.gate.promoted} promoted`:""}</span></div>
      <h4>Strategy families</h4>${lift(m.families)}<h4>Timeframes</h4>${lift(m.timeframes)}<h4>Trading hours</h4>${lift(m.sessions)}
      <h4>Settings: best 5% vs all</h4>${tbl(["setting","all random bots (median)","best 5% (median)"], (m.settings||[]).map(r=>[r.setting, fmt(r.all,2)+r.unit, fmt(r.best,2)+r.unit]))}
      <h4>Trading costs by timeframe</h4>${tbl(["timeframe","bots (≥30 trades)","profitable before costs","turned into losers by costs","typical cost per trade"], (m.costs||[]).map(r=>[r.tf, N1(r.tested), N1(r.gross_profitable), `${N1(r.turned_losers_by_costs)} (${r.gross_profitable?Math.round(100*r.turned_losers_by_costs/r.gross_profitable):0}%)`, fmt(r.cost_r_per_trade,2)+"R"]))}
      <h4>On this floor</h4>${tbl(["family","alive","cut","live paper profit"], (m.floor_families||[]).map(r=>[esc(r.family), r.alive, r.cut, `<span class="${r.live_profit_usd>0?"pos":r.live_profit_usd<0?"neg":""}">${usd(r.live_profit_usd)}</span>`]))}
      <h4>How the best 10% of new bots changed, hour by hour</h4><div class="mut small"><i class="k" style="background:#f5c84c"></i> stop (× ATR) <i class="k" style="background:#2ee59d"></i> target (× ATR) <i class="k" style="background:#4da3ff"></i> max hold (hours)</div><canvas class="lchart" id="lc-${sym}"></canvas>
      <div class="mut small">Family share of the best 10%:</div><canvas class="lchart" id="lf-${sym}"></canvas><div class="legend" id="lfl-${sym}"></div></section>`; }).join("");
  const K = L.kills; $("#ln-kills").innerHTML = K.total ? tbl(["why cut","bots"], Object.entries(K.by_reason).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[esc(k), v])) + `<div class="mut small">${K.total} bots cut on this floor · ${K.live_trades_of_cut_bots} live paper trades between them · ${usd(-K.costs_paid_usd)} trading costs paid</div>` : `<div class="empty">No bots cut yet.</div>`;
  for (const sym of ["XAUUSD","BTCUSD"]){ const S = (L.shifts||{})[sym]||[]; const lab = S.map(x=>new Date(x.t*1000).toLocaleTimeString("en-AU",{timeZone:"Australia/Sydney", hour:"numeric"}));
    lineChart("lc-"+sym, [S.map(x=>x.sl), S.map(x=>x.tp), S.map(x=>x.hold_h)], lab, ["#f5c84c","#2ee59d","#4da3ff"], "");
    const fams = [...new Set(S.flatMap(x=>Object.keys(x.fam)))]; const col = n => (Object.values(FAM).find(f=>f[2]===n)||["#888"])[0];
    lineChart("lf-"+sym, fams.map(f=>S.map(x=>x.fam[f]||0)), lab, fams.map(col), "%");
    const lg = document.getElementById("lfl-"+sym); if (lg) lg.innerHTML = fams.map(f=>`<span><i style="background:${col(f)}"></i>${esc(f)}</span>`).join(""); }
}

function setView(v){ VIEW = v; for (const x of ["floor","trades","results","learned","how","details"]) $("#v-"+x).hidden = x!==v; if (v==="results") loadResults(); if (v==="trades") renderTrades(); if (v==="learned") loadLearned(true);
  document.querySelectorAll("#maintabs button").forEach(b=>b.classList.toggle("on", b.dataset.v===v));
  if (v==="details" && REPLAY && REPLAY.run_id){ renderSurvivors(); drawTimeline(); drawEquity(); if (!replayTimer && !REP.desks.size) startReplay(); } }
document.querySelectorAll("#maintabs button").forEach(b=>b.onclick=()=>setView(b.dataset.v));
document.querySelectorAll("#tabs button[data-s]").forEach(b=>b.onclick=()=>{ document.querySelectorAll("#tabs button[data-s]").forEach(x=>x.classList.remove("on")); b.classList.add("on"); SYM=b.dataset.s; renderSurvivors(); drawTimeline(); drawEquity(); startReplay(); });
$("#btn-replay").onclick = ()=>startReplay();
addEventListener("resize", ()=>{ drawTimeline(); drawEquity(); });

requestAnimationFrame(loop); poll();
fetch("api/replay").then(r=>r.json()).then(j=>{ REPLAY = j; if (location.hash==="#details") setView("details"); }).catch(()=>{});
