# -*- coding: utf-8 -*-
"""agents‘chat 对接适配器 ——【暂时不接入】。

保留此文件的用途：
  - 记录该工具的对接要素（自带网页界面 8090、双智能体调度、chat_history.json）；
  - 将来需要接入时，把 default_enabled 改为 True 即可在前端启用；
  - 届时可把它的 GATEWAY_HOST 指向 Hub 代理，由 Hub 统一做会话/去重/首条规则。

当前状态：default_enabled = False（前端默认隐藏/不启用）。
"""

from .base import ToolAdapter


class AgentsChatAdapter(ToolAdapter):
    id = "agents_chat"
    name = "agents‘chat"
    description = "双智能体群聊调度器（手机比例网页前端，自带 8090 界面）"
    icon = "🗨️"

    default_dirname = "agents\u2018chat"      # 目录名含弯引号，用转义避免源码编码问题
    default_entry = "start_web.bat"
    default_args = []
    default_env_name = "agent"
    default_port = 8090
    default_stdin_lines = 0

    bridge_mode = "proxy"                     # 接入时把 GATEWAY_HOST/PORT 指向 Hub
    default_intercept = False

    ui_type = "web"                           # 有自带网页界面 → 前端可 iframe 内嵌 / 新标签打开
    supports_log = True
    default_enabled = False                   # ★ 暂不接入
