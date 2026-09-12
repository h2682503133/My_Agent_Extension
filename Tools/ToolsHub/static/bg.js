/* 幻灯片壁纸 —— 同步 My_Agent_MSA 前端（chat.html 的 .bg-rotator 机制）
   - 后台由 Hub 反向代理 MSA 前端的 /backgrounds/（含目录清单与图片）
   - 每 5 分钟换一张，低不透明度铺底，切换前预加载下一张
*/
(function () {
  var ROOT = '/backgrounds/';
  var INTERVAL = 5 * 60 * 1000;    // 与 MSA 一致的 5 分钟
  var list = [], index = 0, el = null;

  function target() {
    if (!el) el = document.getElementById('bg-rotator');
    return el;
  }
  function setBg(url) {
    var e = target();
    if (e) e.style.backgroundImage = 'url("' + url + '")';
  }
  function preload(url) { var img = new Image(); img.src = url; }
  function next() {
    if (!list.length) return;
    setBg(ROOT + encodeURIComponent(list[index % list.length]));
    index += 1;
    preload(ROOT + encodeURIComponent(list[index % list.length]));
  }
  function nameOf(f) {
    var n = f && f.name ? f.name : String(f || '');
    try { n = decodeURIComponent(n); } catch (e) {}
    return n;
  }

  fetch(ROOT)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      list = (data || []).map(nameOf).filter(function (n) { return /\.(jpe?g|png|webp|gif)$/i.test(n); });
      if (!list.length) return;
      next();
      setInterval(next, INTERVAL);
    })
    .catch(function () { /* 未同步到壁纸时静默使用纯色底 */ });
})();
