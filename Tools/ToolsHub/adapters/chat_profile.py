# -*- coding: utf-8 -*-
"""聊天记录转档案 对接适配器（纯本地工具，不接 MSA）。

该工具读取聊天记录 JSON/TXT，调用 LLM 生成 IDENTITY.md / USER.md，
不需要与 My_Agent_MSA 对接，仅由 Hub 负责启动与日志查看。
"""

from .base import ToolAdapter


class ChatProfileAdapter(ToolAdapter):
    id = "chat_profile"
    name = "聊天记录转档案"
    description = "从聊天记录迭代生成 IDENTITY.md / USER.md 档案"
    icon = "📄"

    default_dirname = "聊天记录转档案"
    default_entry = "start.bat"
    default_args = []
    default_env_name = "agent"
    default_port = None
    default_stdin_lines = 1          # 结束后 pause，回车关闭

    bridge_mode = "none"
    default_intercept = False

    ui_type = "none"
    supports_log = True

    # 该工具的 start.bat 会 pause 等待按键，Hub 停止时直接杀进程树即可
