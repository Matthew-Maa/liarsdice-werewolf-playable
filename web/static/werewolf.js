const ROLE = {WOLF:"狼人", VILLAGER:"平民", WITCH:"女巫", SEER:"预言家", HUNTER:"猎人"};
const ROLE_COLOR = {WOLF:"#ff8f88", VILLAGER:"#f6efe0", WITCH:"#c89ee8", SEER:"#86c8f0", HUNTER:"#e8b56a"};
const ROLE_ORDER = ["WOLF","VILLAGER","WITCH","SEER","HUNTER"];
const OBJECTIVE = {
  WOLF:"夜里和同伴选人下手；白天藏好身份，别被投出去。",
  VILLAGER:"没有技能：靠听发言、看投票找出狼人。",
  SEER:"每夜可以验一个人，白天想办法把结果说清楚。",
  WITCH:"一瓶解药、一瓶毒药，同一夜只能用一瓶。",
  HUNTER:"出局时（被毒除外）可以开枪带走一个人。"
};
const CAUSE = {WOLF_KILL:"夜间被袭击", POISON:"夜间中毒", VOTE:"被投票放逐", SHOT:"被猎人带走"};
const PORTRAITS = ["/assets/portrait_1.jpg","/assets/portrait_2.jpg","/assets/portrait_3.jpg",
                   "/assets/portrait_4.jpg","/assets/portrait_5.jpg"];
const POS = [[50,88],[16,70],[9,38],[31,15],[69,15],[91,38],[84,70]];
const PHASE = {night_wolf:"狼人行动",night_witch:"女巫行动",night_seer:"预言家查验",
  day_speech:"白天发言",day_vote:"投票",hunter_shot:"猎人开枪"};

let token=null, state=null, mode="mixed", humans=1;
let handoverFor=null, lastReqKey="", lastActionsSig="";
let speechMode=null, accuseTarget=null;
let speechKey="", speechTimer=null, speakTimer=null;
let paused=false, speed=0.5, pollTimer=null, busy=false, achShown=false, recDlDone=false;
let mobileTab="info";

const $=id=>document.getElementById(id);
const api=async (url,body)=>{
  const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body||{})});
  return await r.json();
};
function toast(msg){
  const t=$("toast"); t.textContent=msg; t.hidden=false;
  clearTimeout(t._t); t._t=setTimeout(()=>t.hidden=true,2200);
}
function roleName(r){return ROLE[r]||r||"";}
function seatName(pid){ return (state && state.names && state.names[pid]) || (pid+"号"); }
function seatLabel(pid){ return pid+"·"+seatName(pid); }
function setText(el,txt){ if(el && el.textContent!==txt) el.textContent=txt; }
function setHTML(el,html,keepScroll){
  if(!el || el._html===html) return;
  const near = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
  el._html=html; el.innerHTML=html;
  if(!keepScroll || near) el.scrollTop=el.scrollHeight;
}

/* ---------- 座位盘 ---------- */
function buildSeats(names){
  const box=$("seats"); box.innerHTML="";
  for(let i=0;i<7;i++){
    const d=document.createElement("div");
    d.className="seat"; d.id="seat"+i;
    d.style.left=POS[i][0]+"%"; d.style.top=POS[i][1]+"%";
    const img = i < humans ? "/assets/portrait_4.jpg" : PORTRAITS[i%5];
    d.innerHTML=`<img src="${img}" alt=""><div class="nm">${i}·${names[i]||""}</div>`;
    box.appendChild(d);
  }
}
function updateSeats(s){
  const alive=new Set(s.alive||[]);
  const hn = (s.humans!=null ? s.humans : humans);
  document.querySelectorAll(".seat").forEach((el,i)=>{
    el.classList.toggle("dead", !alive.has(i));
    el.classList.toggle("me", i<hn);
    let tag=el.querySelector(".dead-tag");
    if(!alive.has(i) && !tag){ tag=document.createElement("div"); tag.className="dead-tag"; tag.textContent="出局"; el.appendChild(tag); }
    if(alive.has(i) && tag) tag.remove();
  });
}
function markSpeaking(pid){
  document.querySelectorAll(".seat.speaking").forEach(el=>el.classList.remove("speaking"));
  const el=$("seat"+pid); if(el) el.classList.add("speaking");
  clearTimeout(speakTimer);
  speakTimer=setTimeout(()=>{ if(el) el.classList.remove("speaking"); },4200);
}

/* ---------- 公开信息 ---------- */
function describe(a){
  if(!a) return "";
  if(a.type==="SILENCE") return "沉默";
  if(a.type==="SELF_CLAIM") return "自称"+roleName(a.role);
  if(a.type==="ACCUSE") return "指认 "+seatLabel(a.target)+" 是"+roleName(a.role);
  if(a.type==="VOTE") return "投 "+seatLabel(a.target);
  if(a.type==="ABSTAIN") return "弃票";
  if(a.type==="KILL") return "刀 "+seatLabel(a.target);
  if(a.type==="WITCH") return a.potion==="SAVE"?"用解药":(a.potion==="POISON"?"毒 "+seatLabel(a.target):"不用药");
  if(a.type==="CHECK") return "查验 "+seatLabel(a.target);
  if(a.type==="SHOOT") return "开枪带走 "+seatLabel(a.target);
  if(a.type==="NO_SHOOT") return "不开枪";
  return a.type||"";
}
function publicEvents(pub){
  pub=pub||{};
  const evs=[];
  (pub.deaths||[]).forEach(x=>evs.push({
    day:x.day||1, order:(x.phase==="night"?0:8), cls:"ev-death",
    text:`${seatLabel(x.pid)} 出局${CAUSE[x.cause]?"（"+CAUSE[x.cause]+"）":""}`}));
  (pub.speeches||[]).forEach(x=>{
    const a=x.action||{};
    let txt;
    if(a.type==="SELF_CLAIM") txt=`${seatLabel(x.pid)} 自称${roleName(a.role)}`;
    else if(a.type==="ACCUSE") txt=`${seatLabel(x.pid)} 指认 ${seatLabel(a.target)} 是${roleName(a.role)}`;
    else txt=`${seatLabel(x.pid)}：${describe(a)}`;
    evs.push({day:x.day||1, order:2, cls:"", text:txt});
  });
  (pub.votes||[]).forEach(x=>evs.push({
    day:x.day||1, order:6, cls:"ev-vote",
    text:`${seatLabel(x.voter)} → ${x.target==null?"弃票":seatLabel(x.target)}`}));
  evs.sort((a,b)=> (a.day-b.day) || (a.order-b.order));
  return evs;
}
function publicHTML(pub){
  const evs=publicEvents(pub);
  if(!evs.length) return '<div class="ev-empty">还没有公开信息</div>';
  let html=[], cur=null;
  evs.forEach(e=>{
    if(e.day!==cur){ cur=e.day; html.push(`<div class="ev-day">第 ${cur} 天</div>`); }
    html.push(`<div class="${e.cls||""}">${e.text}</div>`);
  });
  return html.join("");
}

/* ---------- 开局 ---------- */
function setup(){
  const box=$("counts"); box.innerHTML="";
  for(let i=1;i<=6;i++){
    const b=document.createElement("button");
    b.className="btn "+(i===humans?"primary":"ghost");
    b.textContent=i; b.onclick=()=>{humans=i; setup();};
    box.appendChild(b);
  }
}
async function startGame(h){
  humans=h;
  const name=$("playerName").value.trim()||"你";
  const names=[name];
  for(let i=1;i<h;i++) names.push("玩家"+(i+1));
  mode = h===0 ? "ai" : (h===1 ? "human" : "mixed");
  const multi = !!($("multiSpeech") && $("multiSpeech").checked);
  const r=await api("/api/werewolf/new",{humans:h,names,mode,
    speech_mode: multi ? "multi" : "single"});
  if(!r.ok){ toast(r.error||"开局失败"); return; }
  token=r.token; state=r.state; achShown=false; recDlDone=false;
  handoverFor=null; lastReqKey=""; lastActionsSig="";
  speechMode=null; accuseTarget=null; speechKey="";
  $("setup").hidden=true; $("game").hidden=false;
  $("result").hidden=true; $("handover").hidden=true; $("private").hidden=true;
  $("quitModal").hidden=true; $("quitBtn").hidden=false;
  $("modeLabel").textContent = h===0 ? "纯 AI 旁观" +
    (multi ? " · 多发言实验" : "") : "同机多人 · 身份隐藏" +
    (multi ? " · 多发言实验" : "");
  $("spectator").hidden = h!==0;
  buildSeats(state.names || names);
  startPoll();
  render(state);
  if(window.RA_MUSIC) RA_MUSIC.play(); else $("bgm").play().catch(()=>{});
}

function startPoll(){ stopPoll(); pollTimer=setInterval(poll, 400); }
function stopPoll(){ if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }
async function poll(){
  if(!token || busy) return;
  try{
    const r=await fetch("/api/werewolf/state?token="+encodeURIComponent(token));
    const j=await r.json();
    if(j.ok && !busy) render(j.state);
  }catch(e){}
}

/* ---------- 主渲染 ---------- */
function render(s){
  state=s;
  updateSeats(s);
  const ph=s.phase||"day_speech";
  const thinking = !s.over && !s.waiting;
  setText($("phase"),"第 "+(s.day||1)+" 天 · "+(PHASE[ph]||ph));
  $("phase").classList.toggle("acting", thinking);
  const speeches=(s.public&&s.public.speeches)||[];
  if(speeches.length){
    const last=speeches[speeches.length-1];
    const key=last.day+"-"+last.pid+"-"+JSON.stringify(last.action);
    if(key!==speechKey){
      speechKey=key;
      $("speech").hidden=false;
      $("speech").textContent=seatLabel(last.pid)+"："+describe(last.action);
      clearTimeout(speechTimer);
      speechTimer=setTimeout(()=>{$("speech").hidden=true;},4500);
      markSpeaking(last.pid);
    }
  }
  setHTML($("log"), publicHTML(s.public));
  $("quitBtn").hidden = !!s.over;
  $("spectator").hidden = (s.humans!=null?s.humans:humans)!==0;
  $("pauseBtn").textContent = s.paused ? "继续" : "暂停";
  $("speedBtn").textContent = "速度 " + (s.speed||0.5) + "x";
  if(s.over && s.result){ hideOverlays(); showResult(s.result); stopPoll(); return; }
  if(s.waiting && s.view){ showRequest(s,false); }
  else { $("handover").hidden=true; $("private").hidden=true; }
}
function hideOverlays(){ $("handover").hidden=true; $("private").hidden=true; }

/* ---------- 决策面板 ---------- */
function reqSig(s){
  const v=s.view||{};
  return (s.pending_pid)+"|"+(v.day)+"|"+(v.phase)+"|"+JSON.stringify(s.legal||[]);
}
function showRequest(s,force){
  const pid=s.pending_pid, v=s.view;
  if(!v) return;
  const sig=reqSig(s);
  const isNew=sig!==lastReqKey;
  if(isNew){ lastReqKey=sig; speechMode=null; accuseTarget=null; mobileTab="info"; applyTab(); }
  if(handoverFor!==pid){
    $("handover").hidden=false; $("private").hidden=true;
    setText($("handoverName"), (s.names&&s.names[pid])||("玩家"+(pid+1)));
    return;
  }
  $("handover").hidden=true; $("private").hidden=false;
  renderPrivate(s,v,isNew);
  // 只在"新的请求"或本地状态变化时重建按钮；轮询本身绝不重建
  if(isNew || force || sig!==lastActionsSig){
    lastActionsSig=sig;
    renderActions(s,v,s.legal||[]);
  }
}
function applyTab(){
  const card=document.querySelector('#private .modal-card');
  if(!card) return;
  card.classList.toggle('tab-public', mobileTab==='public');
  document.querySelectorAll('#ptabs .tab').forEach(b=>{
    b.classList.toggle('on', b.dataset.tab===mobileTab);
  });
}
function renderPrivate(s,v,isNew){
  setText($("roleName"), roleName(v.my_role)+"（"+ (v.my_faction==="wolf"?"狼人阵营":"好人阵营") +"）");
  $("roleName").style.color=ROLE_COLOR[v.my_role]||"#f6efe0";
  const extra=[];
  extra.push(`<div>${OBJECTIVE[v.my_role]||""}</div>`);
  if(v.teammates&&v.teammates.length){
    extra.push("狼同伴："+v.teammates.map(seatLabel).join("、"));
  }
  if(v.my_role==="SEER" && v.seer_results){
    const arr=Object.keys(v.seer_results).map(t=>t+"号="+(v.seer_results[t]?"狼":"好人"));
    if(arr.length) extra.push("验人："+arr.join("；"));
  }
  if(v.my_role==="WITCH" && v.witch){
    extra.push("药：解药"+(v.witch.has_antidote?"有":"无")+" / 毒药"+(v.witch.has_poison?"有":"无")+
      "；今夜被刀："+(v.witch.night_victim==null?"无人":v.witch.night_victim+"号"));
  }
  setHTML($("roleExtra"), extra.map(x=>`<div>${x}</div>`).join(""), true);
  setHTML($("roster"), rosterHTML(s,v), true);
  let req = `第 ${v.day} 天 · ${PHASE[v.phase]||v.phase} · 轮到你（${seatLabel(s.pending_pid)}）`;
  if(v.phase==="day_speech" && s.speech_mode==="multi"){
    const n=(s.public&&s.public.speeches||[]).filter(x=>x.day===v.day && x.pid===v.pid).length;
    req += ` · 本日第 ${n+1} 句（最多 3）`;
  }
  setText($("reqPhase"), req);
  const evn=publicEvents(s.public).length;
  setText($("tabPublic"), "公开记录"+(evn?" · "+evn:""));
  setHTML($("publicList"), publicHTML(s.public), true);
  if(isNew) $("publicList").scrollTop=$("publicList").scrollHeight;
}
function rosterHTML(s,v){
  const alive=new Set(s.alive||[]);
  const claims={}, acc={};
  ((s.public&&s.public.speeches)||[]).forEach(x=>{
    if(!x.action) return;
    if(x.action.type==="SELF_CLAIM") claims[x.pid]={role:x.action.role,day:x.day};
    if(x.action.type==="ACCUSE"){
      const t=x.action.target;
      (acc[t]=acc[t]||[]).push({role:x.action.role, by:x.pid});
    }
  });
  const mates=new Set(v.teammates||[]);
  const hn=(s.humans!=null?s.humans:humans);
  const rows=[];
  for(let i=0;i<7;i++){
    const tags=[`<span class="tag">${i}·${seatName(i)}</span>`];
    if(hn>1 && i<hn) tags.push('<span class="tag">真人</span>');
    if(mates.has(i)) tags.push('<span class="claim">狼同伴</span>');
    if(!alive.has(i)) tags.push('<span class="dead">出局</span>');
    let line=tags.join(" ");
    const c=claims[i];
    if(c) line+=` · 自称${roleName(c.role)}`;
    if(acc[i]&&acc[i].length){
      const cnt={};
      acc[i].forEach(a=>cnt[a.role]=(cnt[a.role]||0)+1);
      const txt=Object.keys(cnt).map(r=>roleName(r)+(cnt[r]>1?"×"+cnt[r]:"")).join("、");
      const bys=[...new Set(acc[i].map(a=>a.by))];
      line+=` · <span class="acc">被指认：${txt}（${bys.map(seatLabel).join("、")}）</span>`;
    }
    const cls="row"+(alive.has(i)?"":" dead")+(i===v.pid?" you":"");
    rows.push(`<div class="${cls}">${i===v.pid?"▶ ":""}${line}</div>`);
  }
  return rows.join("");
}

/* ---------- 动作按钮 ---------- */
function rerenderActions(){
  if(!state||!state.view) return;
  renderActions(state,state.view,state.legal||[]);
  lastActionsSig=reqSig(state);
}
function renderActions(s,v,legal){
  const box=$("actions"); box.innerHTML="";
  const add=(label,action,cls="ghost")=>{
    const b=document.createElement("button"); b.className="btn "+cls; b.textContent=label;
    if(action===null){ b.onclick=()=>{ speechMode=null; accuseTarget=null; rerenderActions(); }; }
    else if(action.__mode){ b.onclick=()=>{ speechMode=action.__mode; accuseTarget=null; rerenderActions(); }; }
    else if(action.__target!==undefined){ b.onclick=()=>{ accuseTarget=action.__target; rerenderActions(); }; }
    else if(!legal.some(a=>JSON.stringify(a)===JSON.stringify(action))) { return; }
    else { b.onclick=()=>act(action); }
    box.appendChild(b);
  };
  const ph=v.phase;
  if(ph==="day_speech"){
    const multi = s.speech_mode==="multi";
    const nToday = ((s.public&&s.public.speeches)||[])
      .filter(x=>x.day===v.day && x.pid===v.pid).length;
    if(speechMode==="claim"){
      const roles=[...new Set(legal.filter(a=>a.type==="SELF_CLAIM").map(a=>a.role))];
      // 自己的真实身份排第一，狼人放最后：避免"我是狼人"成为误点的第一个按钮。
      roles.sort((a,b)=>{
        const rank=r=>(r===v.my_role?-1:(r==="WOLF"?99:0));
        return rank(a)-rank(b);
      });
      roles.forEach(r=>add("我是"+roleName(r),{type:"SELF_CLAIM",role:r}));
      add("返回",null,"ghost");
    } else if(speechMode==="accuse"){
      if(accuseTarget===null){
        const ts=[...new Set(legal.filter(a=>a.type==="ACCUSE").map(a=>a.target))];
        ts.forEach(t=>add("指认 "+seatLabel(t)+"…",{__target:t},"ghost"));
      } else {
        const roles=[...new Set(legal.filter(a=>a.type==="ACCUSE"&&a.target===accuseTarget)
          .map(a=>a.role))];
        roles.sort((a,b)=>(a==="WOLF"?-1:(b==="WOLF"?1:0)));
        roles.forEach(r=>add(seatLabel(accuseTarget)+" 是"+roleName(r),
          {type:"ACCUSE",target:accuseTarget,role:r}));
      }
      add("返回",null,"ghost");
    } else {
      add(multi && nToday>0 ? "结束发言" : "沉默",{type:"SILENCE"});
      if(legal.some(a=>a.type==="SELF_CLAIM")) add("我是…",{__mode:"claim"},"primary");
      if(legal.some(a=>a.type==="ACCUSE")) add("我认为…",{__mode:"accuse"},"primary");
    }
  } else if(ph==="day_vote"){
    const targets=[...new Set(legal.filter(a=>a.type==="VOTE").map(a=>a.target))];
    targets.forEach(t=>add("投 "+seatLabel(t),{type:"VOTE",target:t},"primary"));
    add("弃票",{type:"ABSTAIN"});
  } else if(ph==="night_wolf"){
    const targets=[...new Set(legal.filter(a=>a.type==="KILL").map(a=>a.target))];
    targets.forEach(t=>add("刀 "+seatLabel(t),{type:"KILL",target:t},"danger"));
  } else if(ph==="night_witch"){
    add("不用药",{type:"WITCH",potion:"NONE",target:-1});
    add("用解药救人",{type:"WITCH",potion:"SAVE",target:-1},"primary");
    const ts=[...new Set(legal.filter(a=>a.type==="WITCH"&&a.potion==="POISON").map(a=>a.target))];
    ts.forEach(t=>add("毒 "+seatLabel(t),{type:"WITCH",potion:"POISON",target:t},"danger"));
  } else if(ph==="night_seer"){
    const targets=[...new Set(legal.filter(a=>a.type==="CHECK").map(a=>a.target))];
    targets.forEach(t=>add("验 "+seatLabel(t),{type:"CHECK",target:t},"primary"));
  } else if(ph==="hunter_shot"){
    const targets=[...new Set(legal.filter(a=>a.type==="SHOOT").map(a=>a.target))];
    targets.forEach(t=>add("开枪带走 "+seatLabel(t),{type:"SHOOT",target:t},"danger"));
    add("不开枪",{type:"NO_SHOOT"});
  } else {
    legal.forEach((a,i)=>add(describe(a),a,i===0?"primary":"ghost"));
  }
}

function act(action){
  if(busy || !state) return;
  const pid=state.pending_pid;
  busy=true;
  const box=$("actions");
  if(box) box.classList.add("busy");
  api("/api/werewolf/action",{token,pid,action}).then(r=>{
    busy=false;
    if(box) box.classList.remove("busy");
    if(!r.ok){ toast(r.error||"动作不合法"); rerenderActions(); return; }
    handoverFor=pid;
    $("handover").hidden=true; $("private").hidden=true;
    speechMode=null; accuseTarget=null;
    lastReqKey=""; lastActionsSig="";
    render(r.state);
  }).catch(()=>{busy=false; if(box) box.classList.remove("busy"); toast("网络错误");});
}

/* ---------- 结果 ---------- */
function showResult(r){
  $("result").hidden=false;
  $("quitBtn").hidden=true;
  $("phase").classList.remove("acting");
  const dl=$("downloadRecord"); if(dl) dl.href="/api/record?token="+encodeURIComponent(token||"");
  if(token && !recDlDone && localStorage.getItem("ra_auto_download") !== "0"){
    recDlDone=true;
    setTimeout(()=>{ try{ if(dl) dl.click(); }catch(e){} }, 250);
  }
  if(!achShown && state && state.new_achievements && state.new_achievements.length){
    achShown=true;
    showAchPopup(state.new_achievements.map(a=>a.name));
  }
  $("resultTitle").textContent=r.winner_cn+" 胜";
  $("resultReason").textContent=r.reason;
  const roles=r.reveal_after_game&&r.reveal_after_game.roles||{};
  $("roleList").innerHTML=Object.keys(roles).sort((a,b)=>a-b).map(i=>{
    const nm=(state&&state.names&&state.names[i])||(i+"号");
    return `<div style="color:${ROLE_COLOR[roles[i]]||"#f6efe0"}">${i}·${nm}：${roleName(roles[i])}</div>`;
  }).join("");
  stopPoll();
}

/* ---------- 控制 ---------- */
async function control(body){
  if(!token) return;
  const r=await api("/api/werewolf/control",Object.assign({token},body));
  if(r.ok) render(r.state);
}

$("rulesBtn").onclick=()=>$("rules").hidden=false;
$("rulesClose").onclick=()=>$("rules").hidden=true;
$("backHome").onclick=()=>location.href="/";
$("aiOnly").onclick=()=>startGame(0);
$("startBtn").onclick=()=>startGame(humans);
$("readyBtn").onclick=()=>{
  if(!state) return;
  handoverFor=state.pending_pid;
  $("handover").hidden=true;
  showRequest(state,true);
};
$("pauseBtn").onclick=()=>{ paused=!paused; control({paused}); };
$("speedBtn").onclick=()=>{ speed={0.25:0.5,0.5:1,1:2,2:4,4:8,8:0.25}[speed]||0.5;
  paused=false; control({paused:false,speed}); };
function backToSetup(){
  stopPoll(); token=null; state=null;
  handoverFor=null; lastReqKey=""; lastActionsSig=""; speechKey="";
  $("result").hidden=true; $("quitModal").hidden=true; $("quitBtn").hidden=true;
  $("game").hidden=true; $("setup").hidden=false;
  $("phase").classList.remove("acting");
  setup();
}
$("againBtn").onclick=backToSetup;
$("quitBtn").onclick=()=>{ if(state && !state.over) $("quitModal").hidden=false; };
$("quitCancel").onclick=()=>{ $("quitModal").hidden=true; };
$("quitConfirm").onclick=async()=>{
  if(!token) return;
  const b=$("quitConfirm");
  b.disabled=true; b.textContent="正在保存…";
  try{
    const r=await api("/api/werewolf/quit",{token});
    $("quitModal").hidden=true;
    if(!r.ok){ toast(r.error||"退出失败"); return; }
    backToSetup();
    toast("本局已保存为未完成");
  }catch(e){
    toast("网络错误");
  }finally{
    b.disabled=false; b.textContent="退出并保存";
  }
};
$("menuBtn").onclick=()=>location.href="/";

window.addEventListener("pagehide",()=>{
  if(token && state && !state.over){
    try{ navigator.sendBeacon("/api/werewolf/quit", JSON.stringify({token})); }catch(e){}
  }
});

document.querySelectorAll('#ptabs .tab').forEach(b=>{
  b.onclick=()=>{ mobileTab=b.dataset.tab; applyTab(); };
});

setup();

// 无人值守预览：/werewolf/?demo=ai|1|ready
if (new URLSearchParams(location.search).get("demo")) {
  const d = new URLSearchParams(location.search).get("demo");
  setTimeout(()=>{ startGame(d==="ai" ? 0 : 1); }, 200);
  if(d==="ready"){
    const t=setInterval(()=>{
      if(state && state.pending_pid!=null && state.view){
        clearInterval(t);
        handoverFor=state.pending_pid;
        showRequest(state,true);
      }
    }, 200);
    setTimeout(()=>clearInterval(t), 15000);
  }
}
