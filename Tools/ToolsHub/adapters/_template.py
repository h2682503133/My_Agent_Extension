# -*- coding: utf-8 -*-
"""【模板】新工具对接适配器 —— 复制本文件为 <你的工具>.py 后填写即可。

步骤：
  1) 复制 _template.py → 例如 my_tool.py（文件名随意，别以 _ 开头）
  2) 改 id / name / default_dirname / default_entry / default_port
  3) 若该工具要接 My_Agent_MSA：bridge_mode = "proxy"，并告知 Hub 该工具的
     gateway_url 应填 http://<hub>/t/<id>（前端会自动显示提示）
  4) 重启 Hub（或前端「重新扫描」），边栏出现新工具，再在前端设置它的目录
"""

from .base import ToolAdapter


class MyToolAdapter(ToolAdapter):
    id = "my_tool"                    # 唯一标识
    name = "我的工具"                  # 边栏显示名
    description = "示例：新工具的对接适配器"
    icon = "🧩"

    default_dirname = "我的工具"        # Tools/ 下的目录名（建议值）
    default_entry = "start.bat"        # 入口文件
    default_args = []
    default_env_name = "agent"
    default_port = None                # 有 Web 界面就填端口
    default_stdin_lines = 0            # 启动需要敲几次回车

    bridge_mode = "none"               # "proxy" = 接入 MSA；"none" = 纯本地
    default_intercept = False          # 是否需要人工审核上报

    ui_type = "none"                   # "web" = 工具有自己的网页

    # 可选：覆盖探测逻辑
    # def probe(self) -> dict:
    #     info = super().probe()
    #     ...

    # 可选：覆盖启动命令（特殊启动方式）
    # def command(self) -> list:
    #     ...
