(function(){
  const TRACKS = [
    {name:"Luck Be a Lady", src:"/assets/bgm_dice.mp3"},
    {name:"Casino Royale", src:"/assets/bgm_casino_royale.mp3"},
    {name:"Poker Face", src:"/assets/bgm_poker_face.mp3"},
    {name:"Viva Las Vegas", src:"/assets/bgm_viva_las_vegas.mp3"},
    {name:"狼人杀", src:"/assets/bgm_werewolf.wav"},
  ];
  const KEY = "ra_music_v1";
  const DEF = {on:true, volume:0.65, track:0, mode:"list"};
  let st = Object.assign({}, DEF);
  try { Object.assign(st, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch(e) {}
  if (st.track < 0 || st.track >= TRACKS.length) st.track = 0;
  let audio = null;
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch(e) {} };
  const updateBtn = () => {
    const b = document.getElementById("musicBtn");
    if (b) b.textContent = st.on ? "音乐 开" : "音乐 关";
  };
  const play = () => {
    if (!audio) bind();
    if (!audio) return;
    if (!st.on) return;
    audio.play().catch(() => {});
    updateBtn();
  };
  const toggle = () => {
    if (!audio) bind();
    if (!audio) return;
    st.on = !st.on; save();
    if (st.on) audio.play().catch(() => {}); else audio.pause();
    updateBtn();
  };
  const setTrack = (i, autoplay) => {
    i = Number(i);
    if (!(i >= 0 && i < TRACKS.length)) return;
    st.track = i; save();
    if (!audio) bind();
    if (!audio) return;
    audio.src = TRACKS[i].src;
    audio.load();
    if (st.on && autoplay !== false) audio.play().catch(() => {});
    updateBtn();
  };
  const setMode = (m) => { if (m === "single" || m === "list") { st.mode = m; save(); } };
  const setVolume = (v) => {
    st.volume = Math.max(0, Math.min(1, Number(v)));
    save();
    if (audio) audio.volume = st.volume;
  };
  function bind() {
    audio = document.getElementById("bgm");
    if (!audio) return;
    audio.volume = st.volume;
    audio.loop = false;
    audio.addEventListener("ended", () => {
      if (st.mode === "single") {
        audio.currentTime = 0;
        if (st.on) audio.play().catch(() => {});
      } else {
        setTrack((st.track + 1) % TRACKS.length, true);
      }
    });
    audio.src = TRACKS[st.track].src;
    updateBtn();
    if (st.on) audio.play().catch(() => {});
  }
  window.RA_MUSIC = {
    tracks: TRACKS,
    get state() { return st; },
    toggle, play, setTrack, setMode, setVolume,
    pause: () => { if (audio) audio.pause(); },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();