(function(){
  const $s = id => document.getElementById(id);
  let isHost = false;
  const AUTO_KEY = "ra_auto_download";

  function refreshMusic() {
    if (!window.RA_MUSIC) return;
    const st = RA_MUSIC.state;
    $s("setMusicOn").checked = !!st.on;
    $s("setVolume").value = Math.round((st.volume == null ? 0.65 : st.volume) * 100);
    document.querySelectorAll('input[name="setMode"]').forEach(r => {
      r.checked = r.value === st.mode;
    });
    renderTracks();
  }
  function renderTracks() {
    if (!window.RA_MUSIC) return;
    const st = RA_MUSIC.state;
    $s("setTracks").innerHTML = RA_MUSIC.tracks.map((t, i) =>
      `<button class="track ${i === st.track ? "on" : ""}" data-i="${i}">${t.name}</button>`
    ).join("");
    document.querySelectorAll("#setTracks .track").forEach(b => {
      b.onclick = () => { RA_MUSIC.setTrack(Number(b.dataset.i), true); refreshMusic(); };
    });
  }
  function openSettings() {
    const am = $s("accountModal");
    if (am) am.hidden = true;
    $s("settingsModal").hidden = false;
    $s("setAutoDl").checked = localStorage.getItem(AUTO_KEY) !== "0";
    refreshMusic();
    fetch("/api/account").then(r => r.json()).then(j => {
      isHost = !!j.host;
      $s("clearSection").hidden = !isHost;
    }).catch(() => { $s("clearSection").hidden = true; });
  }
  function closeSettings() { $s("settingsModal").hidden = true; }

  $s("settingsBtn").onclick = openSettings;
  $s("settingsClose").onclick = closeSettings;
  $s("settingsModal").addEventListener("click", e => {
    if (e.target === $s("settingsModal")) closeSettings();
  });
  $s("setMusicOn").onchange = () => {
    if (!window.RA_MUSIC) return;
    const st = RA_MUSIC.state;
    if ($s("setMusicOn").checked && !st.on) RA_MUSIC.toggle();
    if (!$s("setMusicOn").checked && st.on) RA_MUSIC.toggle();
    refreshMusic();
  };
  $s("setVolume").oninput = () => {
    if (window.RA_MUSIC) RA_MUSIC.setVolume(Number($s("setVolume").value) / 100);
  };
  document.querySelectorAll('input[name="setMode"]').forEach(r => {
    r.onchange = () => {
      if (window.RA_MUSIC) RA_MUSIC.setMode(r.value);
      refreshMusic();
    };
  });
  $s("setAutoDl").onchange = () => {
    localStorage.setItem(AUTO_KEY, $s("setAutoDl").checked ? "1" : "0");
  };

  $s("clearRecords").onclick = () => {
    if (!isHost) return;
    $s("clearMsg").textContent = "";
    $s("clearModal").hidden = false;
  };
  $s("clearNo").onclick = () => { $s("clearModal").hidden = true; };
  async function doClear(mode) {
    $s("clearMsg").textContent = "处理中…";
    const btns = [$s("clearNo"), $s("clearDirect"), $s("clearBackup")];
    btns.forEach(b => b.disabled = true);
    try {
      const r = await fetch("/api/records/clear", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mode})
      });
      const j = await r.json();
      if (!j.ok) {
        $s("clearMsg").textContent = j.error || "清空失败";
      } else {
        $s("clearMsg").textContent = mode === "backup"
          ? "已备份到本机备份文件夹，并清空。"
          : "已直接清空。";
        if (typeof loadAccount === "function") loadAccount();
      }
    } catch (e) {
      $s("clearMsg").textContent = "网络错误";
    } finally {
      btns.forEach(b => b.disabled = false);
    }
  }
  $s("clearDirect").onclick = () => doClear("delete");
  $s("clearBackup").onclick = () => doClear("backup");

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      $s("setAutoDl").checked = localStorage.getItem(AUTO_KEY) !== "0";
    });
  } else {
    $s("setAutoDl").checked = localStorage.getItem(AUTO_KEY) !== "0";
  }
})();