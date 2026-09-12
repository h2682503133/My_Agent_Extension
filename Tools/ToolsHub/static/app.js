/* ToolsHub 前端框架
   - 侧栏：概览 / 设置 / 工具列表（无图标）
   - 工具页：子标签「界面（工具专属页面） | 配置 | 日志」
     工具专属页面实现放在 pages/<tool_id>.js，按需加载；缺失则回退通用配置页。
*/
const $ = s => document.querySelector(s);
let STATE = null;
let VIEW = { kind: 'overview' };
let LOGS = {}, ES = null, PAGE_SCRIPTS = {};
window.__hub = { api, toast, esc, HUB: () => STATE, toolById, goTab, onToolEvent };

/* ── 基础 ── */
function toast(msg, kind = "") {
  const d = document.createElement('div');
  d.className = 'toast ' + kind;
  d.textContent = msg;
  $('#toasts').appendChild(d);
  setTimeout(() => d.remove(), 4200);
}
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok || data.ok === false) throw new Error(data.message || data.error || ('HTTP ' + r.status));
  return data;
}
function esc(s) {
  return (s ?? '').toString().replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function toolById(id) { return (STATE?.tools || []).find(t => t.id === id); }
function statusTag(t) {
  const s = t.status || {};
  if (!t.config.enabled) return '<span class="tag">未启用</span>';
  if (s.running && s.ready) return '<span class="tag on">运行中</span>';
  if (s.running) return '<span class="tag">启动中</span>';
  if (s.last_error) return '<span class="tag" style="color:var(--err)">异常</span>';
  return '<span class="tag">已停止</span>';
}

/* ── 状态 ── */
async function loadState() {
  try { STATE = await api('/api/hub/state'); }
  catch (e) { toast('读取状态失败：' + e.message, 'err'); return; }
  const h = STATE.msa_health || {};
  $('#msadot').className = 'dot ' + (h.ok ? 'ok' : 'err');
  $('#msatext').textContent = h.ok ? `MSA 在线 · ${STATE.msa.default_user_id || '未设 user'}` : 'MSA 不可达';
  $('#hubinfo').textContent = STATE.hub_base || '';
  renderNav();
  renderMain();
}
function renderNav() {
  const st = STATE.pending_stats || {};
  const pend = st.pending || 0;
  let html = `<div class="navgroup">总览</div>
    <div class="nav ${VIEW.kind === 'overview' ? 'active' : ''}" onclick="go({kind:'overview'})">
      <span class="nm">概览</span></div>
    <div class="nav ${VIEW.kind === 'settings' ? 'active' : ''}" onclick="go({kind:'settings'})">
      <span class="nm">设置</span></div>
    <div class="navgroup">工具</div>`;
  for (const t of STATE.tools) {
    const s = t.status || {};
    const dot = s.running ? (s.ready ? 'dot ok' : 'dot warn') : 'dot';
    const badge = (t.id === 'llbot_monitor' && pend) ? `<span class="badge">${pend}</span>` : '';
    html += `<div class="nav ${VIEW.kind === 'tool' && VIEW.id === t.id ? 'active' : ''}" onclick="go({kind:'tool',id:'${t.id}'})">
      <span class="nm">${esc(t.name)}</span>${badge}<span class="${dot}"></span></div>`;
  }
  $('#navlist').innerHTML = html;
}
function go(v) {
  VIEW = v;
  document.getElementById('side').classList.remove('open');
  renderNav();
  renderMain();
}
function goTab(tab) { VIEW.tab = tab; renderMain(); }

function renderMain() {
  const c = $('#content');
  c.className = '';
  c.innerHTML = '';
  if (VIEW.kind === 'overview') return renderOverview(c);
  if (VIEW.kind === 'settings') return renderSettings(c);
  if (VIEW.kind === 'tool') return renderToolPage(c, VIEW.id);
}

/* ── 概览 ── */
function renderOverview(c) {
  $('#title').textContent = '概览';
  const st = STATE.pending_stats || {};
  const errs = STATE.adapter_errors || {};
  let html = `<div class="card"><h2>平台对接</h2>
    <div class="kv"><span class="k">MSA 网关</span><span>${esc(STATE.msa.gateway_url)}</span></div>
    <div class="kv"><span class="k">默认 user_id</span><span>${esc(STATE.msa.default_user_id || '（未设置）')}</span></div>
    <div class="kv"><span class="k">默认 agent</span><span>${esc(STATE.msa.default_agent_id || 'main')}</span></div>
    <div class="kv"><span class="k">Hub 地址</span><span>${esc(STATE.hub_base)}</span></div>
    <div class="kv"><span class="k">待处理</span><span>${st.pending || 0} 条 · 已上报 ${st.approved || 0} · 已删除 ${st.deleted || 0}</span></div>
  </div>`;
  if (Object.keys(errs).length) {
    html += `<div class="card"><h2 style="color:var(--err)">适配器加载失败</h2>` +
      Object.entries(errs).map(([m, e]) =>
        `<pre class="log">${esc(m)}: ${esc((e || '').split('\n').slice(-2).join('\n'))}</pre>`).join('') + `</div>`;
  }
  html += `<div class="card"><h2>工具（${STATE.tools.length}）</h2><div class="grid">`;
  for (const t of STATE.tools) {
    html += `<div class="card" style="margin:0">
      <div class="row" style="justify-content:space-between"><b>${esc(t.name)}</b>${statusTag(t)}</div>
      <div class="muted" style="font-size:12px;margin:6px 0 10px">${esc(t.description || '')}</div>
      <div class="row">
        <button class="sm" onclick="go({kind:'tool',id:'${t.id}'})">打开</button>
        ${t.status.running
          ? `<button class="sm danger" onclick="toolAction('${t.id}','stop')">停止</button>`
          : `<button class="sm primary" onclick="toolAction('${t.id}','start')">启动</button>`}
      </div></div>`;
  }
  c.innerHTML = html + `</div></div>`;
}

/* ── 工具页：子标签 ── */
function renderToolPage(c, id) {
  const t = toolById(id);
  if (!t) { c.innerHTML = '<div class="card">工具不存在</div>'; return; }
  stopQrWatch();
  $('#title').textContent = t.name;
  const tab = VIEW.tab || (t.has_page ? 'ui' : 'config');

  c.innerHTML = `<div class="tabs">
      ${t.has_page ? `<div class="tab ${tab === 'ui' ? 'active' : ''}" onclick="goTab('ui')">界面</div>` : ''}
      <div class="tab ${tab === 'config' ? 'active' : ''}" onclick="goTab('config')">配置</div>
      <div class="tab ${tab === 'log' ? 'active' : ''}" onclick="goTab('log')">日志</div>
    </div>
    <div id="toolbody" style="flex:1;min-height:0;display:flex;flex-direction:column"></div>`;

  if (tab === 'ui') { c.className = 'flush'; return mountToolUi(t); }
  if (tab === 'config') {
    $('#toolbody').innerHTML = toolConfigHtml(t);
    if (t.has_qrcode) startQrWatch(t.id);
    return;
  }
  $('#toolbody').innerHTML = toolLogHtml(t);
  refreshLog(id);
}

/* ── 二维码（扫码登录）：显示二维码 + 更新时间，文件变化自动刷新 ── */
let QR_TIMER = null, QR_TS = 0, QR_TOOL = '';

function qrCardHtml(t) {
  const cfg = t.config || {};
  return `<div class="card"><h2>扫码登录</h2>
    <div class="qrbox">
      <img id="qrimg" src="/api/tools/${t.id}/qrcode" alt="登录二维码">
      <div class="qrmeta">
        <div class="kv"><span class="k">取源</span><span id="qrsource">—</span></div>
        <div class="kv"><span class="k">更新时间</span><b id="qrtime">读取中…</b></div>
        <div class="kv"><span class="k">状态</span><span id="qrage">—</span></div>
        <div class="kv"><span class="k">文件</span><span id="qrpath" class="muted" style="font-size:11px;word-break:break-all">—</span></div>
        <div class="row" style="margin-top:10px">
          <button class="sm" onclick="qrReload()">立即刷新</button>
          <span class="muted" style="font-size:12px">每 5 秒自动检测更新</span>
        </div>
      </div>
    </div>
    <details style="margin-top:12px">
      <summary class="muted" style="cursor:pointer;font-size:12px">WebUI API 取码设置（多实例防覆盖，需 token）</summary>
      <div class="grid" style="margin-top:8px">
        <div><label class="f">WebUI 地址</label>
          <input type="text" id="qr_url" value="${esc(cfg.qrcode_api_url || '')}" placeholder="http://127.0.0.1:3081"></div>
        <div><label class="f">WebUI token（cookie webui_token）</label>
          <input type="text" id="qr_token" value="${esc(cfg.qrcode_api_token || '')}" placeholder="留空则只用文件"></div>
      </div>
      <div class="row" style="margin-top:8px">
        <button class="sm primary" onclick="qrSaveApi('${t.id}')">保存</button>
        <span class="muted" style="font-size:12px">API 可用时优先用 API（自带精确过期时间）；不可用自动回落二维码文件</span>
      </div>
    </details>
  </div>`;
}

async function qrSaveApi(toolId) {
  const patch = {
    qrcode_api_url: ($('#qr_url')?.value || '').trim(),
    qrcode_api_token: ($('#qr_token')?.value || '').trim(),
  };
  try {
    await api(`/api/tools/${toolId}/config`, { method: 'POST', body: JSON.stringify(patch) });
    toast('已保存二维码取源设置', 'ok');
    QR_TS = 0;
    qrTick();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

function qrReload() {
  const img = $('#qrimg');
  if (img) img.src = `/api/tools/${QR_TOOL}/qrcode?t=${Date.now()}`;
  qrTick();
}

async function qrTick() {
  if (!QR_TOOL) return;
  let d;
  try { d = await api(`/api/tools/${QR_TOOL}/qrcode/info`); } catch (e) { return; }
  const img = $('#qrimg'), timeEl = $('#qrtime'), ageEl = $('#qrage'),
        pathEl = $('#qrpath'), srcEl = $('#qrsource');
  const srcLabel = { api: 'WebUI API', file: '二维码文件（兜底）', none: '无' }[d.source] || d.source;

  if (d.source === 'none') {
    if (srcEl) srcEl.textContent = '无可用来源';
    if (timeEl) timeEl.textContent = '—';
    if (ageEl) ageEl.textContent = (d.api && d.api.error) || '未配置 WebUI API，且未找到二维码文件';
    if (img) img.style.display = 'none';
    if (pathEl) pathEl.textContent = '—';
    return;
  }
  if (img) img.style.display = '';

  if (d.source === 'api') {
    const apiInfo = d.api || {};
    if (srcEl) srcEl.textContent = 'WebUI API · ' + (apiInfo.url || '');
    if (timeEl) timeEl.textContent = apiInfo.fetched_str || '—';
    const left = apiInfo.expire_in;
    if (ageEl) {
      if (left === null || left === undefined) { ageEl.textContent = '有效'; ageEl.style.color = 'var(--ok)'; }
      else if (left > 0) {
        const m = Math.floor(left / 60), s = left % 60;
        ageEl.textContent = `有效 · 剩余 ${m > 0 ? m + ' 分 ' : ''}${s} 秒`;
        ageEl.style.color = left > 30 ? 'var(--ok)' : 'var(--warn)';
      } else { ageEl.textContent = '已过期，正在获取新码…'; ageEl.style.color = 'var(--warn)'; }
    }
    const f = d.file || {};
    if (pathEl) pathEl.textContent = f.exists ? `${f.path}（${(f.size / 1024).toFixed(1)} KB）` : '—';
    if (img && Date.now() - QR_TS > 4000) { QR_TS = Date.now(); img.src = `/api/tools/${QR_TOOL}/qrcode?t=${QR_TS}`; }
    return;
  }

  // 文件来源
  const f = d.file || {};
  if (srcEl) srcEl.textContent = '二维码文件（兜底）' + ((d.api && d.api.error) ? ` · API 不可用：${d.api.error}` : '');
  if (timeEl) timeEl.textContent = f.updated_str || '—';
  if (ageEl) {
    const s = f.age_sec || 0;
    const human = s < 60 ? `${s} 秒前` : (s < 3600 ? `${Math.floor(s / 60)} 分钟前` : `${Math.floor(s / 3600)} 小时前`);
    ageEl.textContent = (s < 120 ? '有效 · ' : '可能已过期 · ') + human;
    ageEl.style.color = s < 120 ? 'var(--ok)' : 'var(--warn)';
  }
  if (pathEl) pathEl.textContent = `${f.path}（${(f.size / 1024).toFixed(1)} KB）`;
  if (f.updated_at !== QR_TS) {
    QR_TS = f.updated_at;
    if (img) img.src = `/api/tools/${QR_TOOL}/qrcode?t=${Math.floor(f.updated_at)}`;
  }
}

function startQrWatch(toolId) {
  stopQrWatch();
  QR_TOOL = toolId;
  QR_TS = 0;
  qrTick();
  QR_TIMER = setInterval(qrTick, 5000);
}

function stopQrWatch() {
  if (QR_TIMER) { clearInterval(QR_TIMER); QR_TIMER = null; }
  QR_TOOL = '';
}

/* ── 工具专属页面加载（pages/<id>.js）── */
function mountToolUi(t) {
  const host = $('#toolbody');
  host.innerHTML = `<div class="empty">正在加载界面…</div>`;
  const mount = () => {
    host.innerHTML = '';
    try {
      window.ToolPage.render(host, t, window.__hub);
    } catch (e) {
      host.innerHTML = `<div class="card">界面加载失败：${esc(e.message)}</div>`;
    }
  };
  if (PAGE_SCRIPTS[t.id]) { window.ToolPage = PAGE_SCRIPTS[t.id]; return mount(); }
  const s = document.createElement('script');
  s.src = `/pages/${t.id}.js`;
  s.onload = () => { PAGE_SCRIPTS[t.id] = window.ToolPage; mount(); };
  s.onerror = () => { host.innerHTML = `<div class="card">该工具暂无专属界面。</div>`; };
  document.head.appendChild(s);
}

/* ── 通用：配置页 ── */
function toolConfigHtml(t) {
  const cfg = t.config || {}, s = t.status || {}, probe = t.probe || {};
  let html = `<div style="padding:18px;overflow:auto">
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div class="row"><b>${esc(t.name)}</b>${statusTag(t)}</div>
        <div class="row">
          ${s.running ? `<button class="sm danger" onclick="toolAction('${t.id}','stop')">停止</button>
                         <button class="sm" onclick="toolAction('${t.id}','restart')">重启</button>`
                      : `<button class="sm primary" onclick="toolAction('${t.id}','start')">启动</button>`}
          ${t.ui_type === 'web' && t.open_url ? `<button class="sm" onclick="openToolNew('${t.id}')">新标签打开工具</button>` : ''}
          <button class="sm" onclick="toolAction('${t.id}','probe')">环境探测</button>
        </div>
      </div>
      <div class="grid" style="margin-top:12px">
        <div>
          <div class="kv"><span class="k">状态</span><span>${s.running ? '运行中 PID ' + s.pid : '已停止'}</span></div>
          <div class="kv"><span class="k">端口</span><span>${s.port ? s.port + (s.port_open ? ' 已监听' : ' 未监听') : '—'}</span></div>
          <div class="kv"><span class="k">启动时间</span><span>${s.started_at ? new Date(s.started_at * 1000).toLocaleString() : '—'}</span></div>
        </div>
        <div>
          <div class="kv"><span class="k">桥接模式</span><span>${esc(t.bridge_mode)}</span></div>
          <div class="kv"><span class="k">扣留待审</span><span>${cfg.intercept ? '开启' : '关闭'}</span></div>
          <div class="kv"><span class="k">自动发送</span><span>${t.auto_send ? '开启' : '关闭'}</span></div>
        </div>
      </div>
      ${s.last_error ? `<div class="card" style="margin:12px 0 0;border-color:#f0c9c9">最近错误：${esc(s.last_error)}</div>` : ''}
      ${(probe.issues && probe.issues.length)
        ? `<div class="card" style="margin:12px 0 0"><h2 style="color:var(--warn)">环境诊断</h2>
             <ul style="margin:0;padding-left:20px">${probe.issues.map(i => `<li>${esc(i)}</li>`).join('')}</ul></div>` : ''}
    </div>`;

  if (t.has_qrcode) html += qrCardHtml(t);

  if (t.bridge_mode === 'proxy') {
    html += `<div class="card"><h2>MSA 对接</h2>
      <div class="kv"><span class="k">代理地址</span><span><code>${esc(t.gateway_url)}</code></span></div>
      <div class="muted" style="font-size:12px;margin-top:6px">
        把该工具的消息地址指向上面的代理地址即可经 Hub 中转。当前模式：${cfg.intercept ? (t.auto_send ? '扣留+自动发送（收到即上报）' : '扣留待审（人工决定）') : '直通'}。
      </div>
      <div class="row" style="margin-top:10px">
        <button class="sm primary" onclick="toolAction('${t.id}','bridge-apply')">一键写入工具配置</button>
        <button class="sm" onclick="toolAction('${t.id}','bridge-revert')">还原直连 8080</button>
      </div></div>`;
  }

  html += `<div class="card"><h2>配置（保存在 hub_config.json）</h2>
    <label class="f">工具目录（必须位于工具根目录内）</label>
    <input type="text" id="c_dir" value="${esc(cfg.dir || '')}">
    <div class="grid">
      <div><label class="f">启动入口</label><input type="text" id="c_entry" value="${esc(cfg.entry || '')}"></div>
      <div><label class="f">conda 环境</label><input type="text" id="c_env" value="${esc(cfg.env_name || '')}"></div>
      <div><label class="f">Web 端口（无则留空）</label><input type="text" id="c_port" value="${esc(cfg.port ?? '')}"></div>
      <div><label class="f">启动需要回车次数</label><input type="number" id="c_stdin" value="${esc(cfg.stdin_lines ?? 0)}"></div>
      <div><label class="f">上报 user_id</label><input type="text" id="c_uid" value="${esc(cfg.msa_user_id || '')}" placeholder="留空用默认"></div>
      <div><label class="f">上报 agent_id</label><input type="text" id="c_aid" value="${esc(cfg.msa_agent_id || '')}" placeholder="留空用默认"></div>
    </div>
    <label class="f">启动参数（空格分隔）</label>
    <input type="text" id="c_args" value="${esc((cfg.args || []).join(' '))}">
    <div class="row" style="margin-top:12px">
      <label class="row" style="gap:6px"><input type="checkbox" id="c_int" ${cfg.intercept ? 'checked' : ''}> 扣留待审</label>
      <label class="row" style="gap:6px"><input type="checkbox" id="c_en" ${cfg.enabled ? 'checked' : ''}> 启用该工具</label>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="primary" onclick="saveToolCfg('${t.id}')">保存配置</button>
      <span class="muted" style="font-size:12px">命令预览：<code>${esc((t.command || []).join(' ') || '（未配置入口）')}</code></span>
    </div>
  </div></div>`;
  return html;
}

/* ── 通用：日志页 ── */
function toolLogHtml(t) {
  return `<div style="padding:18px;display:flex;flex-direction:column;flex:1;min-height:0">
    <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <h2 style="margin:0">运行日志</h2>
        <div class="row"><button class="sm" onclick="refreshLog('${t.id}')">刷新</button>
          <span class="muted" style="font-size:12px">实时推送中</span></div>
      </div>
      <pre class="log" id="logbox" style="flex:1;max-height:none">${esc((LOGS[t.id] || []).join('\n') || '（暂无日志）')}</pre>
    </div></div>`;
}

/* ── 设置页 ── */
function renderSettings(c) {
  $('#title').textContent = '设置';
  c.innerHTML = `<div style="max-width:720px">
    <div class="card"><h2>Hub</h2>
      <label class="f">监听地址（0.0.0.0 允许手机访问）</label>
      <input type="text" id="s_host" value="${esc(STATE.hub.host || '0.0.0.0')}">
      <label class="f">端口（改动后需重启 Hub）</label>
      <input type="number" id="s_port" value="${esc(STATE.hub.port || 8077)}">
      <label class="f">工具根目录（所有工具必须位于此目录内）</label>
      <input type="text" id="s_root" value="${esc(STATE.hub.tools_root || '')}">
    </div>
    <div class="card"><h2>My_Agent_MSA 对接</h2>
      <label class="f">网关地址</label>
      <input type="text" id="s_gw" value="${esc(STATE.msa.gateway_url || '')}">
      <label class="f">默认 user_id</label>
      <input type="text" id="s_uid" value="${esc(STATE.msa.default_user_id || '')}">
      <label class="f">默认 agent_id</label>
      <input type="text" id="s_aid" value="${esc(STATE.msa.default_agent_id || 'main')}">
      <div class="row" style="margin-top:12px"><button class="primary" onclick="saveHubConfig()">保存</button></div>
    </div></div>`;
}
async function saveHubConfig() {
  const patch = {
    hub: { host: $('#s_host').value.trim(), port: parseInt($('#s_port').value || '8077', 10),
           tools_root: $('#s_root').value.trim() },
    msa: { gateway_url: $('#s_gw').value.trim(), default_user_id: $('#s_uid').value.trim(),
           default_agent_id: $('#s_aid').value.trim() }
  };
  try { await api('/api/hub/config', { method: 'POST', body: JSON.stringify(patch) });
    toast('已保存（端口改动需重启 Hub）', 'ok'); loadState();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

/* ── 动作 ── */
async function toolAction(id, action) {
  try {
    const d = await api(`/api/tools/${id}/${action}`, { method: 'POST' });
    toast(d.message || ('已执行 ' + action), 'ok');
    loadState();
  } catch (e) { toast('失败：' + e.message, 'err'); }
}
async function saveToolCfg(id) {
  const patch = {
    dir: $('#c_dir').value.trim(), entry: $('#c_entry').value.trim(),
    env_name: $('#c_env').value.trim(),
    port: $('#c_port').value.trim() ? parseInt($('#c_port').value.trim(), 10) : null,
    stdin_lines: parseInt($('#c_stdin').value || '0', 10) || 0,
    args: ($('#c_args').value || '').trim() ? $('#c_args').value.trim().split(/\s+/) : [],
    msa_user_id: $('#c_uid').value.trim(), msa_agent_id: $('#c_aid').value.trim(),
    intercept: $('#c_int').checked, enabled: $('#c_en').checked,
  };
  try { await api(`/api/tools/${id}/config`, { method: 'POST', body: JSON.stringify(patch) });
    toast('配置已保存', 'ok'); loadState();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}
async function refreshLog(id) {
  try {
    const d = await api(`/api/tools/${id}/log?tail=500`);
    LOGS[id] = d.lines || [];
    const b = $('#logbox');
    if (b) { b.textContent = LOGS[id].join('\n') || '（暂无日志）'; b.scrollTop = b.scrollHeight; }
  } catch (e) { toast('读取日志失败：' + e.message, 'err'); }
}
function openToolNew(id) {
  const t = toolById(id);
  if (t && t.open_url) window.open(t.open_url, '_blank');
}
async function reloadAdapters() {
  try { const d = await api('/api/hub/reload-adapters', { method: 'POST' });
    toast(d.message, 'ok'); loadState();
  } catch (e) { toast('重扫失败：' + e.message, 'err'); }
}

/* ── 事件（SSE）── */
function onToolEvent(d) {
  if (VIEW.kind === 'tool' && window.ToolPage && window.ToolPage.onEvent) {
    try { window.ToolPage.onEvent(d, window.__hub); } catch (e) {}
  }
}
function connectSSE() {
  if (ES) ES.close();
  ES = new EventSource('/api/hub/events');
  ES.onmessage = ev => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.type === 'log') {
      (LOGS[d.tool] = LOGS[d.tool] || []).push(d.line);
      if (LOGS[d.tool].length > 800) LOGS[d.tool].splice(0, LOGS[d.tool].length - 800);
      const b = $('#logbox');
      if (b && VIEW.kind === 'tool' && VIEW.id === d.tool) {
        b.textContent += (b.textContent ? '\n' : '') + d.line;
        b.scrollTop = b.scrollHeight;
      }
    } else if (d.type === 'tool_status') {
      loadState();
    } else if (d.type === 'pending' && d.action === 'add') {
      if (STATE?.pending_stats) STATE.pending_stats.pending = (STATE.pending_stats.pending || 0) + 1;
      renderNav();
    } else if (d.type === 'config') {
      loadState();
    }
    onToolEvent(d);
  };
  ES.onerror = () => { setTimeout(connectSSE, 4000); };
}

loadState();
connectSSE();
