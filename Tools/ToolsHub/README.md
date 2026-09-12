# ToolsHub —— 工具聚合与 MSA 中间对接

把 `Tools/` 下的独立工具聚合到一个网页控制台，并在**不修改 My_Agent_MSA 项目**的前提下，
利用它对外暴露的接口做中间对接。

## 快速开始

```bat
start.bat            :: 或 python hub_server.py（agent conda 环境）
```

- 本机：http://127.0.0.1:8077
- 手机：http://<本机局域网IP>:8077 （同一网络即可）

## 界面

配色对齐 My_Agent_MSA 前端（Catppuccin）：**深色侧栏 `#1e1e2e` + 浅灰内容底 `#f5f5f5`（非纯白）**，
卡片半透明让底纹透出。左侧栏为工具列表（无图标，带状态点与待处理徽标）：

- **概览**：MSA 连接状态、各工具卡片（启停）
- **设置**：Hub 地址/端口/工具根目录、MSA 网关与默认 user_id / agent_id
- **工具**：每个工具有自己的页面，顶部子标签为
  - **界面** —— 该工具的**专属适配界面**（由 `pages/<tool_id>.js` 实现）
  - **配置** —— 目录/入口/conda 环境/端口/扣留开关等（持久化）
  - **日志** —— 运行日志实时流

### 幻灯片壁纸（同步 MSA）

与 MSA 前端 `chat.html` 的 `.bg-rotator` 同机制：每 **5 分钟**换一张、`cover` 铺满、低不透明度、
切换前预加载下一张。壁纸**不复制文件**，而是由 Hub 反向代理 MSA 前端的 `/backgrounds/`：

```
浏览器  →  Hub /backgrounds/          →  MSA 前端 /backgrounds/（目录清单 JSON）
        →  Hub /backgrounds/<图片>    →  MSA 前端 /backgrounds/<图片>
```

MSA 那边往 backgrounds 目录加图，Hub 侧自动跟随。代理目标地址取
`msa.frontend_url`，未配置时回落到 `msa.gateway_url`（同一 8080 入口）。

### 每个工具的界面都是独立适配页

专属界面**不是统一的结构化表单**，而是针对该工具交互方式单独写的页面：

```
pages/
  llbot_monitor.js     # LLBot群聊监控：页面级聊天框
  _template.js         # 模板（复制改名即可）
```

没有专属页面的工具会自动回退到「配置 / 日志」两个标签。

## LLBot群聊监控 的界面（页面级聊天框）

```
┌ 小栏（头部信息）────────────────────────────────┐
│ LLBot群聊监控 · 运行中 · user=h268 · agent=main │
│                        手动审核/自动发送 [开关] │
├ 聊天区（占满一页，可滚动）──────────────────────┤
│ ┌ 收到（全文显示）────────────────────┐          │
│ │ 群 851648664 · 蝶殇、❀ (2195239690) │          │
│ │ [附加信息（可选，加在正文前）]        │          │
│ │ [正文，可直接编辑]                   │          │
│ │            [发送]  [删除]            │          │
│ └──────────────────────────────────────┘          │
│                              我发出的消息 ─┐      │
│ ┌─ 智能体回复                              │      │
├ 输入框 ────────────────────────────── [发送] ────┤
└─────────────────────────────────────────────────┘
```

- **收到的消息**：全文显示，带「发送」「删除」；可在正文前附加自定义信息、也可直接编辑正文
- **发送的信息 / 智能体回复**：左右气泡呈现，自动滚动
- **自动发送开关**（小栏里）：
  - 关闭（默认）= 扣留待审，收到后由你决定发送或删除
  - 开启 = 收到即上报，不扣留
- **携带信息**（小栏按钮，配置项 `auto_prefix`）：设置**自动上报时**附带的文字/模板，支持占位符：

  | 占位符 | 含义 |
  | --- | --- |
  | `{group}` `{sender}` `{sender_id}` `{account}` | 从消息原文解析出的群号 / 发送者 / 发送者号 / 账号 |
  | `{content}` | 消息正文（解析结果） |
  | `{raw}` | 完整原文（含元信息头） |

  例：模板 `以下是来自 QQ 群 {group} 的消息，{sender} 说：{content}`
  → 自动上报内容为 `以下是来自 QQ 群 851648664 的消息，蝶殇、❀ 说：12点10提醒我去领快递`

  不写 `{content}` / `{raw}` 时，原文自动接在模板之后；留空则原样上报。

## 核心能力

### 1. 工具聚合（路径全部可配置，不硬编码）

每个工具的**目录 / 入口 / 参数 / conda 环境 / 端口**都保存在 `hub_config.json`，
可在网页「设置」「工具配置」里随时修改并持久化。目录必须在「工具根目录」（默认 `Tools/`）内。

- 启动：按适配器生成命令（`.bat` → `cmd /c`；`.py` → 对应 conda 环境的 python）
- 停止：`taskkill /T /F`（连子进程一起结束，避免残留 python）
- 日志：落盘 `data/logs/<tool>.log` 并实时 SSE 推送到网页

### 2. MSA 中间对接（同构代理，工具零改动）

Hub 复刻网关的同形接口，工具只需把地址指过来：

| 工具请求 | Hub 行为 |
| --- | --- |
| `POST /t/<tool>/api/login` | 透传给 MSA 网关 |
| `POST /t/<tool>/api/messages` | `intercept=true` → **扣留待审**；否则直接上报 |
| `GET  /t/<tool>/api/events` | 扣留模式返回空闲流；直通模式透传事件流 |

工具侧只改一个地址（也可用面板上的「一键写入工具配置」自动改）：

```
gateway_url: http://127.0.0.1:8080        →  http://127.0.0.1:8077/t/<tool_id>
```

Hub 侧统一实现：登录会话缓存、SSE 保活、`event_id` 去重、**同一 task 只保留首条用户可见回复**。

### 3. 扣留待审（人工决定上报 or 删除）

工具的 `intercept` 打开后，它上报的消息**不会直接进 agent**，而是停在「待审队列」：

- 可**编辑正文**，并填写**附加信息**（自动加在正文之前）
- 「上报给 agent」→ 此时才真正 POST 给 MSA
- 「删除」→ 丢弃，不产生任何上报
- 所有决定记入「收发流水」

## 新增一个工具的对接（任意扩展）

对接实现分两部分，**每个工具一个文件**：

**① 后端适配器** `adapters/<tool>.py`（启动、探测、消息解析、一键对接）

```
adapters/
  base.py              # 契约（ToolAdapter 基类）
  _template.py         # 模板：复制它改名即可
  llbot_monitor.py     # LLBot群聊监控
  chat_profile.py      # 聊天记录转档案
  agents_chat.py       # agents‘chat（默认未启用）
```

**② 前端专属界面** `pages/<tool>.js`（该工具自己的交互页面，可选）

```js
window.ToolPage = {
  render(host, tool, hub) { /* 画界面 */ },
  onEvent(d, hub)         { /* 接收实时事件（新消息/回复/日志）*/ },
};
```

三步接入：

1. 复制 `adapters/_template.py` → `adapters/<你的工具>.py`，填 `id / name / default_dirname / default_entry`；
   接 MSA 设 `bridge_mode="proxy"`，要人工审核设 `default_intercept=True`
2. （可选）复制 `pages/_template.js` → `pages/<你的工具>.js` 写专属界面；不写则用通用「配置/日志」
3. 网页点「重扫适配器」（或重启 Hub）→ 边栏出现新工具 → 在面板里配置目录

**无需修改 `hub_server.py`。**

## 目录结构

```
ToolsHub/
  hub_server.py        # 主服务：进程管理 + 管理 API + 桥代理 + SSE
  index.html           # 页面骨架
  static/app.css       # 明色调主题
  static/app.js        # 前端框架（侧栏 / 工具页 / 子标签 / SSE）
  pages/               # ★ 每个工具的专属界面（分文件，按需加载）
    llbot_monitor.js
  hub_config.json      # 持久化配置（Hub / MSA / 各工具）
  adapters/            # ★ 每个工具的对接实现（分文件，自动发现）
  msa/
    client.py          # MSA 外部接口客户端（不修改 MSA）
    bridge.py          # 桥：同构代理 + 扣留队列 + 流水
    chat.py            # 聊天记录 + agent 回复订阅
  data/
    pending.json       # 待审队列
    chat_<tool>.json   # 各工具聊天记录
    logs/<tool>.log    # 各工具日志
```

## 配置项（hub_config.json）

```jsonc
{
  "hub": { "host": "0.0.0.0", "port": 8077, "tools_root": "E:\\...\\Tools" },
  "msa": { "gateway_url": "http://127.0.0.1:8080",
           "default_user_id": "h268", "default_agent_id": "main" },
  "tools": {
    "llbot_monitor": {
      "dir": "...\\Tools\\LLBot群聊监控",
      "entry": "start.bat",
      "env_name": "agent",
      "stdin_lines": 2,          // 启动时自动回车次数（该工具会问两次路径）
      "intercept": true,          // ★ 扣留待审
      "msa_user_id": "h268",      // 上报用的 user_id（留空用全局默认）
      "msa_agent_id": "main",
      "enabled": true
    }
  }
}
```

## 依赖

`requests`（agent conda 环境已具备）。前端零依赖（原生 HTML/JS）。

## 注意

- **单实例**：Hub 启动时会检测端口占用并拒绝启动 —— Windows 上 `SO_REUSEADDR` 允许
  多个进程绑定同一端口，多实例会让请求被随机分配、界面行为错乱。请勿重复启动。
- 同一工具不要同时被 Hub 和手工启动（端口冲突会被拦截并提示）
- 扣留模式下工具侧无需订阅事件流（`web_keepalive: false`），回复仍在 agent 侧正常生成
- Hub 不改动 My_Agent_MSA 任何文件，只用其 HTTP 外部接口
