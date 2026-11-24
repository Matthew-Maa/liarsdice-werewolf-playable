(function(){
  let timer = null;
  window.showAchPopup = function(names){
    if(!names || !names.length) return;
    let el = document.getElementById('achPopup');
    if(!el){
      el = document.createElement('div');
      el.id = 'achPopup';
      el.className = 'ach-popup';
      el.innerHTML = '<div class="ach-pop-kicker">成就解锁</div>' +
        '<div class="ach-pop-names" id="achPopNames"></div>';
      document.body.appendChild(el);
    }
    document.getElementById('achPopNames').innerHTML =
      names.map(n => `<div>${n}</div>`).join('');
    el.classList.remove('show');
    void el.offsetWidth;
    el.classList.add('show');
    clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove('show'), 3000);
  };
})();