# LLBot 群聊监控

独立工具（依附 My_Agent_MSA 的 LLBot / Satori 网关），双击 `start.bat` 使用。
支持 **多个 QQ 账号同时运行**：账号数量由变量控制，端口自动递增，所有账号共用一个
`llbot.exe` 和 `qq.exe`。

## 重要：与 MSA 项目的账号互不影响

- MSA 项目里已经有一个 LLBot 实例在跑（固定端口 `13000` / `3080` / `5600`），
  **这个实例的端口、data 目录、登录态都不要动**。
- 本工具使用**自己独立的 LLBot 副本**（把 `LLBot-CLI-win-x64` 整个复制一份，如
  `D:\DuanKou\tools\LLBot-工具`），所有工具账号共用这一份，与 MSA 实例完全隔离。
- 工具账号的端口从 `13001` / `3081` / `5601` 起自动递增，与 MSA 实例错开。

## 功能

1. **启动 LLBot（多账号）**：按 `account_count` 生成账号列表，逐个启动
   `llbot.exe --qq-path <QQ.exe> --pmhq-port=<端口>`。路径存储于 `config.json`，
   首次启动要求输入并保存；之后启动显示已存路径，可输入新路径修改，直接回车使用存储值。
2. **群聊监控**：每个账号分别连接各自的 Satori 网关，仅处理群聊消息。触发条件为"或"：
   监控列表 `monitored`（群号 → [q号, ...]）里的用户 **不 @ 也会处理**；
   非监控用户则需要**明确 @ 机器人**才处理（@全体 不触发）。
   命中后不把消息发给模型，而是通过**收到消息的那个账号**私聊转发给 `target_qq`。

## 多账号同时运行（LLBot 部分）

1. 复制整个 `LLBot-CLI-win-x64` 文件夹为工具专用副本，例如 `D:\DuanKou\tools\LLBot-工具`
   （工具账号的 data 与 MSA 实例分离）。
2. 在 `config.json` 中设置 `account_count`（账号数量）、`llbot_path`（指向副本的
   `llbot.exe`）、`qq_path`。端口自动生成：账号N 使用 `base_* + (N-1)`。
3. 在每个账号的 `data\config_<QQ号>.json` 中把端口改成对应值，并开启 Satori：
   - 账号1：`webui.port=3081`，`satori.enable=true`，`satori.port=5601`
   - 账号2：`webui.port=3082`，`satori.enable=true`，`satori.port=5602`
   - 依此类推。PMHQ 端口由脚本自动拼接 `--pmhq-port=`，无需手动改。
4. 首次运行时每个 LLBot 窗口会各自弹出二维码，分别扫码登录各自的 QQ 号。
5. 新版本支持 `pmhq_config.json` 的 `quick_login_qq`，可指定 QQ 号免扫码快速登录。

> 若 QQ 客户端不允许同时开两个窗口：给对应账号开启 `"headless": true`，
> 脚本会拼接 `--headless` 参数以无头模式启动 QQ，两个账号即可共存。

## config.json 字段

- `llbot_path`：工具专用 LLBot 副本的 llbot.exe 完整路径（所有账号共用，首次启动会询问）
- `qq_path`：QQ.exe 完整路径（所有账号共用）
- `account_count`：账号数量（端口从 base 端口依次递增）
- `base_pmhq_port`：账号1 的 PMHQ 端口，账号N = base + (N-1)（默认 `13001`）
- `base_webui_port`：账号1 的 WebUI 端口（默认 `3081`）
- `base_satori_port`：账号1 的 Satori 端口（默认 `5601`）
- `satori_token`：Satori 鉴权 token，本地一般留空
- `headless`：是否无头模式启动 QQ（多开 QQ 窗口冲突时开启）
- `target_qq`：命中消息转发私聊给的 QQ 号（可不填，留空则只记录不转发）
- `monitored`：被监控用户表，格式 `{"群号": ["q号1", "q号2"], ...}`

## 依赖

`websockets`、`requests`（与 `Tools/agents'chat` 相同环境）。
