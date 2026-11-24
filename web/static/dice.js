const OPP = [
  {key:"baseline", name:"老实人", img:"/assets/portrait_1.jpg", diff:"入门", style:"稳健 · 不诈唬",
   stars:1, blurb:"按自己的牌算概率，不记你，也不会故意骗你。"},
  {key:"peak", name:"老江湖", img:"/assets/portrait_3.jpg", diff:"进阶", style:"压迫 · 敢开敢加",
   stars:2, blurb:"气场很足，喜欢把叫价往上顶；你一犹豫，他就加码。"},
  {key:"memory", name:"记忆师", img:"/assets/portrait_5.jpg", diff:"高手", style:"记仇 · 盯你的习惯",
   stars:3, blurb:"你上一局怎么开、怎么加，它都记着；同一个套路别用两次。"},
  {key:"bayes", name:"读心者", img:"/assets/portrait_2.jpg", diff:"专家", style:"读牌 · 越玩越懂你",
   stars:4, blurb:"它把你的手牌当未知数，一局一局更新判断；玩得越久越难骗。"},
];
const PIPS = {1:[5],2:[1,9],3:[1,5,9],4:[1,3,7,9],5:[1,3,5,7,9],6:[1,3,4,6,7,9]};
const POS = {1:[50,50],2:[50,50],3:[50,50],4:[50,50],5:[50,50],6:[50,50],7:[50,50],8:[50,50],9:[50,50]};
POS[1]=[22,22]; POS[2]=[50,22]; POS[3]=[78,22];
POS[4]=[22,50]; POS[5]=[50,50]; POS[6]=[78,50];
POS[7]=[22,78]; POS[8]=[50,78]; POS[9]=[78,78];

let mode = "human";
let researchMode = false;
let sel = ["baseline"];
let token = null;
let state = null;
let qty = 3, face = 2;
let speed = 0.5, paused = false, tickTimer = null;
let pollTimer = null, lastLogText = "";
let busyAct = false;
let achShown = false;
let recDlDone = false;
let turnKey = "";
let resultAnimKey = "";
let toastTimer = null;
const FACE_CN = {1:"①",2:"②",3:"③",4:"④",5:"⑤",6:"⑥"};

const $ = id => document.getElementById(id);
const api = async (url, body) => {
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body || {})});
  return await r.json();
};

function dieHTML(v, back=false){
  if(back) return '<div class="die back"></div>';
  const ps = PIPS[v] || [];
  let s = '<div class="die">';
  ps.forEach(p => {
    const [x,y] = POS[p];
    s += `<span class="pip" style="left:${x}%;top:${y}%"></span>`;
  });
  return s + '</div>';
}
function renderDice(el, vals, back=false){
  el.innerHTML = (vals || []).map(v => dieHTML(v, back)).join("") || "";
}
function setBacks(el,n=5){ el.innerHTML = Array.from({length:n},()=>dieHTML(0,true)).join(""); }

function toast(msg){
  const t=$("toast"); t.textContent=msg; t.hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>t.hidden=true,2200);
}

function renderSetup(){
  const box=$("oppCards"); box.innerHTML="";
  OPP.forEach(o=>{
    const d=document.createElement("div");
    d.className="opp-card"+(sel.includes(o.key)?" sel":"");
    const style = researchMode ? "真实研究权重 · 固定策略" : o.style;
    d.innerHTML=`<img src="${o.img}" alt=""><h3>${o.name}</h3>
      <div class="style">${style}</div><div class="diff">难度 ${o.diff}</div>
      <div class="stars">${"★".repeat(o.stars)}${"☆".repeat(4-o.stars)}</div>`;
    d.onclick=()=>{
      if(mode==="human"){ sel=[o.key]; }
      else {
        if(sel.includes(o.key)) sel=sel.filter(k=>k!==o.key);
        else if(sel.length<2) sel.push(o.key);
      }
      renderSetup();
    };
    box.appendChild(d);
  });
  $("startBtn").disabled = mode==="human" ? sel.length!==1 : sel.length!==2;
  $("aiMode").textContent = mode==="human" ? "AI 内战旁观" : "回到人机";
}

async function startGame(){
  const name=$("playerName").value.trim() || "你";
  const body={mode, name, opponent:sel[0], ai2:sel[1]||"bayes"};
  const r=await api("/api/dice/new", body);
  if(!r.ok){ toast(r.error||"开局失败"); return; }
  token=r.token; state=r.state; achShown=false; recDlDone=false; turnKey=""; resultAnimKey="";
  $("setup").hidden=true; $("game").hidden=false; $("result").hidden=true;
  $("quitModal").hidden=true; $("quitBtn").hidden=false;
  lastLogText="";
  $("modeLabel").textContent = mode==="ai" ? "AI 内战 · 旁观" : "人机对战";
  $("spectator").hidden = mode!=="ai";
  if(mode==="ai"){ stopPoll(); startTick(); } else { stopTick(); startPoll(); }
  render(state);
  if(window.RA_MUSIC) RA_MUSIC.play(); else $("bgm").play().catch(()=>{});
}

function render(s){
  state=s;
  const p0=s.players[0], p1=s.players[1];
  const top = mode==="ai" ? p0 : p1;
  const bottom = mode==="ai" ? p1 : p0;
  $("oppImg").src = top.img || (mode==="ai" ? "/assets/portrait_3.jpg" : "/assets/portrait_3.jpg");
  // 服务端 players 不含 portrait；按名字映射
  const topInfo = OPP.find(o=>o.name===top.name);
  const botInfo = OPP.find(o=>o.name===bottom.name);
  $("oppImg").src = topInfo ? topInfo.img : "/assets/portrait_3.jpg";
  $("oppName").textContent = top.name;
  $("oppStyle").textContent = top.level || "";
  $("meName").textContent = bottom.name;
  $("bidText").textContent = s.current_bid || "还没有人叫牌";
  $("thinking").hidden = !(mode==="human" && !s.over && (s.thinking || s.turn===1));
  renderDice($("oppDice"), Array(5).fill(0), true);
  if(mode==="human" && s.own_dice) renderDice($("myDice"), s.own_dice);
  else setBacks($("myDice"),5);
  const logs=(s.log||[]).slice(-12);
  $("log").innerHTML = logs.map(x=>`<div>${x}</div>`).join("");
  const lastLine=logs.length ? logs[logs.length-1] : "";
  if(lastLine && lastLine!==lastLogText){
    const el=$("log").lastElementChild;
    if(el) el.classList.add("flash");
  }
  lastLogText=lastLine;
  $("quitBtn").hidden = !!s.over;
  const show = mode==="human" && s.waiting && !s.over;
  $("controls").hidden = !show;
  if(show){
    const tk = s.turn + "|" + s.history.length + "|" + s.waiting;
    if(tk !== turnKey){
      turnKey = tk;
      qty = Math.max(3, (s.history.length ? s.history[s.history.length-1].qty+1 : 3));
      face = s.history.length ? s.history[s.history.length-1].face : 2;
      qty = Math.min(10, qty);
    }
    updateControls();
  } else {
    turnKey = "";
  }
  $("spectator").hidden = mode!=="ai";
  if(mode==="ai"){ $("pauseBtn").textContent = paused ? "继续" : "暂停";
    $("speedBtn").textContent = "速度 " + speed + "x"; }
  if(s.over && s.result) showResult(s.result);
}

function updateControls(){
  $("qtyText").textContent = qty + " 个";
  document.querySelectorAll(".face").forEach(el=>{
    el.classList.toggle("sel", Number(el.dataset.f)===face);
  });
  $("openBtn").disabled = !(state && state.history.length);
}
function buildFaces(){
  const box=$("faces"); box.innerHTML="";
  for(let f=1;f<=6;f++){
    const d=document.createElement("div");
    d.className="face"+(f===face?" sel":"");
    d.dataset.f=f; d.textContent=f===1?"1":f;
    d.onclick=()=>{ face=f; updateControls(); };
    box.appendChild(d);
  }
}
async function act(body){
  if(!token || busyAct) return;
  busyAct = true;
  $("controls").classList.add("busy");
  try{
    const r = await api("/api/dice/action",{token,action:body});
    if(!r.ok){ toast(r.error||"动作不合法"); return; }
    render(r.state);
  }catch(e){
    toast("网络错误");
  }finally{
    busyAct = false;
    $("controls").classList.remove("busy");
  }
}
async function tick(){
  if(!token || mode!=="ai" || paused) return;
  const r=await api("/api/dice/tick",{token});
  if(r.ok) render(r.state);
}
function startTick(){
  stopTick();
  const ms = Math.max(100, 700/speed);
  tickTimer=setInterval(tick, ms);
}
function stopTick(){ if(tickTimer){clearInterval(tickTimer);tickTimer=null;} }

async function pollHuman(){
  if(!token || mode!=="human" || busyAct) return;
  try{
    const r=await fetch("/api/dice/state?token="+encodeURIComponent(token));
    const j=await r.json();
    if(j.ok) render(j.state);
  }catch(e){}
}
function startPoll(){ stopPoll(); pollTimer=setInterval(pollHuman, 350); }
function stopPoll(){ if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }

function showResult(r){
  const key = [r.rounds, r.revealed_total, r.final_bid && r.final_bid.text,
    (r.hands.p0||[]).join(""), (r.hands.p1||[]).join("")].join("|");
  $("result").hidden=false;
  $("quitBtn").hidden=true;
  if(key === resultAnimKey) return;
  resultAnimKey = key;
  stopTick(); stopPoll();

  const dl=$("downloadRecord");
  if(dl){ dl.hidden=true; dl.href="/api/record?token="+encodeURIComponent(token||""); }
  $("againBtn").disabled=true; $("changeBtn").disabled=true;
  $("resultTitle").textContent="开 盅";
  $("resultReason").textContent="";
  $("resultBid").textContent="最后一手：" + r.final_bid.text;
  $("resultCount").textContent="";

  const rows=[["resMine", r.hands.p0], ["resOpp", r.hands.p1]];
  rows.forEach(([id,vals])=>{
    const el=$(id); el.innerHTML="";
    (vals||[]).forEach(()=>{
      const d=document.createElement("div");
      d.className="die back rolling";
      el.appendChild(d);
    });
  });

  const target=r.final_bid.face;
  const wild = !!r.special_rule && target !== 1;
  const wildNote = $("wildNote");
  if(wildNote){ wildNote.hidden = !wild; }
  const seq=[];
  for(let i=0;i<5;i++){
    seq.push({id:"resMine", pos:i, v:r.hands.p0[i]});
    seq.push({id:"resOpp", pos:i, v:r.hands.p1[i]});
  }
  let idx=0, count=0;
  const finish=()=>{
    const win = mode==="human" && r.winner_seat===0;
    $("resultTitle").textContent = mode==="human" ? (win?"你赢了":"对手赢了") : (r.winner_name+" 胜");
    $("resultReason").textContent = r.reason;
    $("resultCount").textContent = "开盅实际：" + r.revealed_total + " 个";
    $("againBtn").disabled=false; $("changeBtn").disabled=false;
    if(dl) dl.hidden=false;
    if(token && !recDlDone && localStorage.getItem("ra_auto_download") !== "0"){
      recDlDone=true;
      setTimeout(()=>{ try{ if(dl) dl.click(); }catch(e){} }, 300);
    }
    if(!achShown && state && state.new_achievements && state.new_achievements.length){
      achShown=true;
      showAchPopup(state.new_achievements.map(a=>a.name));
    }
  };
  const step=()=>{
    if(idx>=seq.length){ setTimeout(finish, 420); return; }
    const it=seq[idx++];
    const row=$(it.id);
    const old=row.children[it.pos];
    const tmp=document.createElement("div");
    tmp.innerHTML=dieHTML(it.v);
    const die=tmp.firstElementChild;
    die.classList.add("reveal");
    if(wild && it.v===1) die.classList.add("wild");
    if(it.v===target){ die.classList.add("hit"); count++; }
    else if(wild && it.v===1){ count++; }
    row.replaceChild(die, old);
    $("resultCount").textContent = "开盅中：" + count + " 个 " + (FACE_CN[target]||"");
    setTimeout(step, 320);
  };
  setTimeout(step, 380);
}

function backToSetup(){
  stopTick(); stopPoll();
  $("result").hidden=true; $("quitModal").hidden=true; $("quitBtn").hidden=true;
  $("game").hidden=true; $("setup").hidden=false;
  token=null; state=null; lastLogText="";
  renderSetup();
}

$("rulesBtn").onclick=()=>{ $("rules").hidden=false; };
$("rulesClose").onclick=()=>{ $("rules").hidden=true; };
$("backHome").onclick=()=>location.href="/";
$("aiMode").onclick=()=>{ mode = mode==="human" ? "ai" : "human"; sel=[]; renderSetup(); };
$("startBtn").onclick=startGame;
$("againBtn").onclick=()=>{ $("result").hidden=true; startGame(); };
$("changeBtn").onclick=backToSetup;
$("quitBtn").onclick=()=>{ if(state && !state.over) $("quitModal").hidden=false; };
$("quitCancel").onclick=()=>{ $("quitModal").hidden=true; };
$("quitConfirm").onclick=async()=>{
  if(!token) return;
  const b=$("quitConfirm"); b.disabled=true;
  try{
    const r=await api("/api/dice/quit",{token});
    $("quitModal").hidden=true;
    if(!r.ok){ toast(r.error||"退出失败"); return; }
    backToSetup();
    toast("本局已保存为未完成");
  }catch(e){
    toast("网络错误");
  }finally{
    b.disabled=false;
  }
};
$("qMinus").onclick=()=>{ qty=Math.max(1,qty-1); updateControls(); };
$("qPlus").onclick=()=>{ qty=Math.min(10,qty+1); updateControls(); };
$("callBtn").onclick=()=>act({type:"call",qty,face});
$("openBtn").onclick=()=>act({type:"open"});
$("pauseBtn").onclick=()=>{ paused=!paused; paused?stopTick():startTick(); render(state); };
$("speedBtn").onclick=()=>{ speed={0.25:0.5,0.5:1,1:2,2:4,4:8,8:0.25}[speed]||0.5;
  paused=false; startTick(); render(state); };

async function loadConfig(){
  try{
    const r=await fetch("/api/health"); const h=await r.json();
    researchMode=!!h.dice_model;
  }catch(e){}
  renderSetup();
}
buildFaces(); loadConfig();

window.addEventListener("pagehide",()=>{
  if(token && state && !state.over){
    try{ navigator.sendBeacon("/api/dice/quit", JSON.stringify({token})); }catch(e){}
  }
});

// 无人值守预览：/dice/?demo=1（不会出现在界面）
if (new URLSearchParams(location.search).get("demo")) {
  setTimeout(()=>{ mode="human"; sel=["peak"]; startGame(); }, 200);
}
