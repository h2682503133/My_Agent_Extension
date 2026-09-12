/* LLBot群聊监控 · 专属适配界面
   页面级聊天框：小栏（头部信息 + 自动发送开关）/ 聊天区（收到·发出·回复）/ 底部输入
   收到的群消息全文显示，各带「发送」「删除」两个按钮，可在正文前附加信息。
*/
(function () {
  let ITEMS = [], TOOL = null, HUB = null, AUTO = false, LOG = null;

  const esc = s => (s ?? '').toString().replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const hhmm = ts => new Date((ts || 0) * 1000).toLocaleTimeString('zh-CN', { hour12: false });

  async function loadMessages() {
    const d = await HUB.api(`/api/chat/${TOOL.id}/messages?limit=300`);
    ITEMS = d.items || [];
    AUTO = !!d.auto_send;
    LOG = { user_id: (d.defaults || {}).user_id || '', agent_id: (d.defaults || {}).agent_id || 'main' };
    render();
  }

  function bubble(it) {
    if (it.kind === 'incoming') {
      return `<div class="bubble b-agent"><div class="who"><b>${esc(it.agent_id || LOG?.agent_id || 'agent')}</b><span>${esc(it.time || '')}</span></div><div class="txt">${esc(it.text || '')}</div></div>`;
    }
    if (it.kind === 'outgoing') {
      const src = { manual: '我发送', approve: '已上报', auto: '自动发送', direct: '直通上报' }[it.source] || '已发送';
      return `<div class="bubble b-out"><div class="who"><span>${esc(src)}</span><span>${esc(it.time || '')}</span></div><div class="txt">${esc(it.text || '')}</div></div>`;
    }
    // pending：收到的群消息，全文显示 + 发送/删除
    const p = it.parsed || {};
    const done = it.status !== 'pending';
    const doneText = { sent: '已发送', deleted: '已删除' }[it.status] || it.status;
    const head = `<div class="hd"><b>收到</b><span>群 ${esc(p.group || '-')}</span><span>${esc(p.sender || '-')}${p.sender_id ? ' (' + esc(p.sender_id) + ')' : ''}</span><span>${esc(it.time || '')}</span>${done ? `<span class="tag">${esc(doneText)}</span>` : ''}</div>`;
    if (done) {
      return `<div class="bubble b-pending done" id="b_${it.id}">${head}<div class="txt">${esc(it.final_text || it.text || '')}</div></div>`;
    }
    return `<div class="bubble b-pending" id="b_${it.id}">${head}` +
      `<input type="text" id="px_${it.id}" placeholder="（可选）附加信息，将加在正文之前">` +
      `<textarea id="tx_${it.id}" style="margin-top:6px">${esc(it.text || '')}</textarea>` +
      `<div class="acts">` +
      `<button class="ok sm" onclick="LLBOT_PAGE.send('${it.id}','${it.pending_id || ''}')">发送</button>` +
      `<button class="sm danger" onclick="LLBOT_PAGE.del('${it.id}','${it.pending_id || ''}')">删除</button>` +
      `</div></div>`;
  }

  function render() {
    if (!document.getElementById('chatlog')) return;
    const box = document.getElementById('chatlog');
    box.innerHTML = ITEMS.length
      ? ITEMS.map(bubble).join('')
      : `<div class="empty">还没有消息。群聊命中的消息会显示在这里，等你决定发送或删除。</div>`;
    const sw = document.getElementById('sw');
    if (sw) sw.className = 'switch' + (AUTO ? ' on' : '');
    const st = document.getElementById('autotxt');
    if (st) st.textContent = AUTO ? '自动发送（收到即上报）' : '手动审核（收到后等你决定）';
    box.scrollTop = box.scrollHeight;
  }

  async function toggleAuto() {
    AUTO = !AUTO;
    try {
      await HUB.api(`/api/tools/${TOOL.id}/config`, { method: 'POST', body: JSON.stringify({ auto_send: AUTO }) });
      HUB.toast(AUTO ? '已开启自动发送：新消息将直接上报' : '已关闭自动发送：新消息将被扣留待审', 'ok');
      render();
      HUB.HUB() && HUB.HUB();
    } catch (e) { AUTO = !AUTO; HUB.toast('切换失败：' + e.message, 'err'); render(); }
  }

  function updateCarryBtn() {
    const btn = document.getElementById('carrybtn');
    if (!btn) return;
    const has = !!(TOOL && TOOL.config && (TOOL.config.auto_prefix || '').trim());
    btn.textContent = has ? '携带信息：已设置' : '携带信息：未设置';
    btn.classList.toggle('primary', has);
  }

  function carryHtml() {
    const v = (TOOL && TOOL.config && TOOL.config.auto_prefix) || '';
    return `<div class="carrybar" id="carrybar" hidden>` +
      `<div class="muted" style="font-size:12px">自动上报时携带的信息。占位符：` +
      `<code>{group}</code> <code>{sender}</code> <code>{sender_id}</code> <code>{account}</code> ` +
      `<code>{content}</code>（消息正文） <code>{raw}</code>（完整原文）；` +
      `不写 <code>{content}</code>/<code>{raw}</code> 时，原文自动接在模板之后</div>` +
      `<textarea id="carrytext" placeholder="例如：以下是来自 QQ 群 {group} 的消息，{sender} 说：{content}">${esc(v)}</textarea>` +
      `<div class="row" style="margin-top:6px">` +
      `<button class="primary sm" onclick="LLBOT_PAGE.saveCarry()">保存</button>` +
      `<button class="sm" onclick="LLBOT_PAGE.clearCarry()">清空</button>` +
      `<span class="muted" id="carrymsg" style="font-size:12px"></span></div>` +
      `</div>`;
  }

  window.ToolPage = {
    render(host, tool, hub) {
      TOOL = tool; HUB = hub;
      host.innerHTML = `
        <div class="chatwrap">
          <div class="chatbar">
            <span class="name">${esc(tool.name)}</span>
            <span class="tag ${tool.status?.running ? 'on' : ''}">${tool.status?.running ? '运行中' : '已停止'}</span>
            <span class="muted" style="font-size:12px">上报 user=${esc(tool.config.msa_user_id || '默认')} · agent=${esc(tool.config.msa_agent_id || 'main')}</span>
            <span style="flex:1"></span>
            <button class="sm" id="carrybtn" onclick="LLBOT_PAGE.toggleCarry()">携带信息</button>
            <div class="autoswitch" title="开启后：收到的群消息直接上报（并带上携带信息）；关闭：扣留待审">
              <span id="autotxt" class="muted" style="font-size:12px"></span>
              <div class="switch" id="sw" onclick="LLBOT_PAGE.toggleAuto()"><i></i></div>
            </div>
            <button class="sm" onclick="LLBOT_PAGE.reload()">刷新</button>
          </div>
          ${carryHtml()}
          <div class="chatlog" id="chatlog"></div>
          <div class="chatinput">
            <textarea id="chatin" placeholder="输入要发给智能体的消息（Enter 发送，Shift+Enter 换行）"></textarea>
            <button class="primary" id="chatsend" onclick="LLBOT_PAGE.manualSend()">发送</button>
          </div>
        </div>`;
      const ta = document.getElementById('chatin');
      ta.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.ToolPage.manualSend(); }
      });
      updateCarryBtn();
      loadMessages().catch(e => HUB.toast('加载聊天记录失败：' + e.message, 'err'));
    },

    toggleCarry() {
      const bar = document.getElementById('carrybar');
      if (bar) bar.hidden = !bar.hidden;
    },

    async saveCarry() {
      const t = document.getElementById('carrytext');
      const v = t ? t.value : '';
      try {
        await HUB.api(`/api/tools/${TOOL.id}/config`, { method: 'POST', body: JSON.stringify({ auto_prefix: v }) });
        TOOL.config.auto_prefix = v;
        HUB.toast(v.trim() ? '已保存自动上报携带信息' : '已清空携带信息', 'ok');
        const m = document.getElementById('carrymsg');
        if (m) m.textContent = '已保存';
        updateCarryBtn();
      } catch (e) { HUB.toast('保存失败：' + e.message, 'err'); }
    },

    async clearCarry() {
      const t = document.getElementById('carrytext');
      if (t) t.value = '';
      await window.ToolPage.saveCarry();
    },

    async reload() { try { await loadMessages(); } catch (e) { HUB.toast(e.message, 'err'); } },

    async send(chatId, pendingId) {
      const px = document.getElementById('px_' + chatId);
      const tx = document.getElementById('tx_' + chatId);
      const text = tx ? tx.value : '';
      const prefix = px ? px.value : '';
      const btn = document.querySelector(`#b_${chatId} button.ok`);
      if (btn) { btn.disabled = true; btn.textContent = '发送中…'; }
      try {
        await HUB.api(`/api/pending/${pendingId}/approve`, {
          method: 'POST', body: JSON.stringify({ prefix, text })
        });
        HUB.toast('已发送给智能体', 'ok');
        await loadMessages();
      } catch (e) {
        HUB.toast('发送失败：' + e.message, 'err');
        if (btn) { btn.disabled = false; btn.textContent = '发送'; }
      }
    },

    async del(chatId, pendingId) {
      if (!confirm('确定删除这条消息（不上报给智能体）？')) return;
      try {
        await HUB.api(`/api/pending/${pendingId}/delete`, { method: 'POST' });
        HUB.toast('已删除');
        await loadMessages();
      } catch (e) { HUB.toast('删除失败：' + e.message, 'err'); }
    },

    async manualSend() {
      const ta = document.getElementById('chatin');
      const text = (ta?.value || '').trim();
      if (!text) return;
      const btn = document.getElementById('chatsend');
      if (btn) { btn.disabled = true; btn.textContent = '发送中…'; }
      try {
        await HUB.api(`/api/chat/${TOOL.id}/send`, { method: 'POST', body: JSON.stringify({ text }) });
        ta.value = '';
        await loadMessages();
      } catch (e) {
        HUB.toast('发送失败：' + e.message, 'err');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '发送'; }
      }
    },

    toggleAuto,

    /* 实时事件：新收到的群消息 / 已发送 / agent 回复 */
    onEvent(d) {
      if (!d || d.tool !== (TOOL && TOOL.id)) return;
      if (d.type === 'chat') {
        if (d.item) {
          const i = ITEMS.findIndex(x => x.id === d.item.id);
          if (i >= 0) ITEMS[i] = d.item; else ITEMS.push(d.item);
          ITEMS.sort((a, b) => (a.ts || 0) - (b.ts || 0));
          if (d.item.kind === 'pending' && d.item.status === 'pending') {
            HUB.toast('收到一条待审消息：' + (d.item.summary || '').slice(0, 40));
          }
        } else {
          ITEMS = [];
        }
        render();
      }
    }
  };

  window.LLBOT_PAGE = window.ToolPage;
})();
