let acct = null, acctDefs = [], acctTotal = 0, acctTab = "login";

const $a = id => document.getElementById(id);
const api = async (url, body) => {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {})});
  return await r.json();
};
const GAME_CN = {liarsdice: "吹牛骰子", werewolf: "狼人杀"};
const RES_CN = {win: "胜", loss: "负", aborted: "未完成", spectate: "旁观"};
const ROLE_CN = {human: "真人", spectator: "旁观", WOLF: "狼人", VILLAGER: "平民",
  WITCH: "女巫", SEER: "预言家", HUNTER: "猎人"};

function setMsg(t) { $a("acctMsg").textContent = t || ""; }

function paintAccount(data) {
  acct = data && data.user ? data.user : null;
  acctDefs = (data && data.defs) || acctDefs || [];
  acctTotal = (data && data.ach_total) || acctDefs.length;
  $a("accountBtn").textContent = acct ? (acct.nick || acct.username).slice(0, 6) : "登录";
  $a("acctGuest").hidden = !!acct;
  $a("acctPanel").hidden = !acct;
  if (!acct) return;
  $a("acctHello").textContent = (acct.nick || acct.username) + " · 我的记录";
  const st = data.stats || {};
  const wr = st.win_rate == null ? "—" : Math.round(st.win_rate * 100) + "%";
  $a("acctStats").innerHTML =
    `<span>对局 <b>${st.games || 0}</b></span>` +
    `<span>胜 <b>${st.wins || 0}</b></span>` +
    `<span>负 <b>${st.losses || 0}</b></span>` +
    `<span>胜率 <b>${wr}</b></span>`;
  const games = data.games || [];
  $a("acctHistory").innerHTML = games.length ? games.map(g => {
    const res = RES_CN[g.result] || g.result;
    const cls = ["win", "loss", "aborted"].includes(g.result) ? g.result : "";
    const role = g.role ? ` · ${ROLE_CN[g.role] || g.role}` : "";
    const sum = g.summary ? ` · ${g.summary}` : "";
    const ts = (g.ts || "").replace("T", " ").slice(0, 16);
    return `<div class="row"><span class="res ${cls}">${res}</span>` +
      `<span>${GAME_CN[g.game] || g.game}${role}${sum}</span>` +
      `<span class="meta">${ts}</span></div>`;
  }).join("") : '<div class="acct-note">还没有对局记录。</div>';
  const got = new Map((data.achievements || []).map(x => [x.code, x.ts || ""]));
  let ach = acctDefs.map(d =>
    `<div class="a on"><b>★ ${d.name}</b>${(got.get(d.code) || "").slice(0, 10)}</div>`
  ).join("");
  const locked = Math.max(0, acctTotal - acctDefs.length);
  for (let i = 0; i < locked; i++) ach += '<div class="a"><b>???</b>未解锁</div>';
  $a("acctAch").innerHTML = ach;
}

async function loadAccount() {
  try {
    const r = await fetch("/api/account");
    const j = await r.json();
    if (j.ok) paintAccount(j);
  } catch (e) {}
}

function showAccount() {
  const sm = document.getElementById("settingsModal");
  if (sm) sm.hidden = true;
  $a("accountModal").hidden = false;
  setMsg("");
  loadAccount();
}

function setTab(t) {
  acctTab = t;
  document.querySelectorAll(".acct-tab").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === t));
  $a("acctNick").hidden = t !== "reg";
  $a("acctSubmit").textContent = t === "reg" ? "注 册" : "登 录";
  $a("acctPass").autocomplete = t === "reg" ? "new-password" : "current-password";
  setMsg("");
}

$a("accountBtn").onclick = showAccount;
$a("acctClose").onclick = () => { $a("accountModal").hidden = true; };
$a("accountModal").addEventListener("click", e => {
  if (e.target === $a("accountModal")) $a("accountModal").hidden = true;
});
document.querySelectorAll(".acct-tab").forEach(b =>
  b.onclick = () => setTab(b.dataset.tab));

$a("acctSubmit").onclick = async () => {
  const username = $a("acctUser").value.trim();
  const password = $a("acctPass").value;
  const nick = $a("acctNick").value.trim();
  setMsg("");
  if (!username || !password) { setMsg("用户名和密码都要填"); return; }
  const url = acctTab === "reg" ? "/api/register" : "/api/login";
  const r = await api(url, {username, password, nick});
  if (!r.ok) { setMsg(r.error || "失败"); return; }
  $a("acctPass").value = "";
  await loadAccount();
};
$a("acctLogout").onclick = async () => {
  await api("/api/logout", {});
  await loadAccount();
  setTab("login");
};

loadAccount();
setTab("login");
