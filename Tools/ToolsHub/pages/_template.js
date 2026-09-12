/* 【模板】工具专属界面 —— 复制本文件为 pages/<tool_id>.js 后按需改写。

框架会在选中该工具、且切到「界面」子标签时调用 render()，
并在收到实时事件（新消息 / agent 回复 / 日志）时调用 onEvent()。

可用的 hub 工具（第三个参数）：
  hub.api(path, opts)      发请求（返回 JSON，非 ok 自动抛错）
  hub.toast(msg, kind)     右下角提示（kind: "" | "ok" | "err"）
  hub.esc(str)             HTML 转义
  hub.toolById(id)         取工具状态
  hub.goTab('config'|'log'|'ui')  切子标签

常用后端接口：
  GET  /api/chat/<tool_id>/messages?limit=300   聊天记录（pending/outgoing/incoming）
  POST /api/chat/<tool_id>/send                 {text} 人工发送给 agent
  POST /api/pending/<pending_id>/approve        {text, prefix} 上报某条待审消息
  POST /api/pending/<pending_id>/delete         删除某条待审消息
  POST /api/tools/<tool_id>/config              {auto_send:true} 等配置项
  POST /api/tools/<tool_id>/start|stop|restart  进程控制
*/
window.ToolPage = {
  render(host, tool, hub) {
    host.innerHTML = `
      <div class="chatwrap">
        <div class="chatbar">
          <span class="name">${hub.esc(tool.name)}</span>
          <span class="tag ${tool.status?.running ? 'on' : ''}">${tool.status?.running ? '运行中' : '已停止'}</span>
        </div>
        <div class="chatlog">
          <div class="empty">这里是 ${hub.esc(tool.name)} 的专属界面（模板）</div>
        </div>
      </div>`;
  },

  onEvent(d, hub) {
    // d.type: "chat" | "log" | "tool_status" | "pending" | "config"
    // d.tool 为工具 id；按需刷新界面
  },
};
