# -*- coding: utf-8 -*-
"""工具对接适配器契约（ToolAdapter 基类）。

每个接入 ToolsHub 的工具，在 adapters/ 下拥有【一个独立文件】，
继承 ToolAdapter 并实现所需方法即可被自动发现（见 adapters/__init__.py）。

设计原则：
  - Hub 不关心工具内部实现，只按契约启动/停止/探测/取日志；
  - 启动路径一律来自配置（前端可改、持久化），适配器只给 default_* 建议值；
  - 与 My_Agent_MSA 的对接方式由 bridge 声明，Hub 统一实现，工具侧尽量零改动。
"""

from pathlib import Path


class ToolAdapter:
    # ── 身份（子类必须覆盖）────────────────────────────────
    id = "template"                 # 唯一标识（用于配置键、URL 前缀 /t/<id>/）
    name = "模板工具"                # 边栏显示名
    description = ""                # 一句话说明
    icon = "🔧"                     # 边栏图标（emoji）

    # ── 默认值（仅作建议，实际以配置为准，前端可改）──────────
    default_dirname = ""            # 默认目录名（位于 hub.tools_root 下）
    default_entry = ""              # 默认入口（start.bat / python xxx.py）
    default_args = []               # 默认参数
    default_env_name = "agent"      # 建议 conda 环境
    default_port = None             # 工具自身 Web 端口（无则 None）
    default_stdin_lines = 0         # 启动后需要回车应答的次数（交互式脚本用）

    # ── 与 MSA 的对接声明 ──────────────────────────────────
    #   mode="proxy"  : 工具把 gateway_url 指向 Hub，Hub 做同构代理（推荐，零改动）
    #   mode="none"   : 该工具不接 MSA（纯本地工具）
    bridge_mode = "none"
    # 是否默认拦截工具上报的消息（true=进待审队列，人工决定上报/删除）
    default_intercept = False
    # 是否默认「自动发送」（true=收到即上报，不扣留）
    default_auto_send = False

    # ── 能力声明（前端据此显示按钮）────────────────────────
    ui_type = "none"                # "web" = 工具有自己的网页界面；"none" = 控制台/脚本
    supports_log = True             # 是否支持日志查看
    default_enabled = True          # 默认是否在前端启用（暂不接入的工具设 False）

    def __init__(self, cfg: dict):
        """cfg = 该工具的持久化配置（dir/entry/args/env_name/port/intercept/...）。"""
        self.cfg = cfg or {}

    # ── 取值helper：配置优先，回落到默认 ───────────────────
    def dir_path(self) -> Path:
        d = str(self.cfg.get("dir") or "").strip()
        return Path(d) if d else Path(".")

    def entry(self) -> str:
        return str(self.cfg.get("entry") or self.default_entry or "").strip()

    def args(self) -> list:
        a = self.cfg.get("args")
        return list(a) if isinstance(a, list) else list(self.default_args)

    def env_name(self) -> str:
        return str(self.cfg.get("env_name") or self.default_env_name or "").strip()

    def port(self):
        p = self.cfg.get("port", self.default_port)
        try:
            return int(p) if p else None
        except (TypeError, ValueError):
            return None

    def intercept(self) -> bool:
        v = self.cfg.get("intercept", self.default_intercept)
        return bool(v)

    def bridge_gateway_url(self, hub_base: str) -> str:
        """工具应填写的 gateway_url（Hub 的同构代理地址，带工具前缀）。"""
        return f"{hub_base.rstrip('/')}/t/{self.id}"

    # ── 契约方法（子类按需覆盖）────────────────────────────
    def probe(self) -> dict:
        """探测运行环境，返回给前端展示的诊断信息。

        返回示例：
          {"ok": True, "dir_exists": True, "entry_exists": True,
           "port_free": True, "issues": ["..."]}
        """
        d = self.dir_path()
        entry = self.entry()
        info = {
            "dir": str(d),
            "dir_exists": d.is_dir(),
            "entry": entry,
            "entry_exists": bool(entry) and (d / entry).exists(),
            "port": self.port(),
            "issues": [],
        }
        if not info["dir_exists"]:
            info["issues"].append(f"目录不存在：{d}")
        elif entry and not info["entry_exists"]:
            info["issues"].append(f"入口文件不存在：{d / entry}")
        if not entry:
            info["issues"].append("未设置启动入口（entry）")
        info["ok"] = not info["issues"]
        return info

    def command(self) -> list:
        """生成启动命令列表（由配置派生，不硬编码路径）。

        默认实现：
          - .bat/.cmd  → cmd.exe /c <entry> [args]
          - .py        → python <entry> [args]
          - 其他       → 直接执行 <entry> [args]
        子类可覆盖以支持特殊启动方式。
        """
        d = self.dir_path()
        entry = self.entry()
        args = [str(a) for a in self.args()]
        if not entry:
            return []
        suffix = Path(entry).suffix.lower()
        if suffix in (".bat", ".cmd"):
            return ["cmd.exe", "/c", entry] + args
        if suffix == ".py":
            return ["python", entry] + args
        return [entry] + args

    def cwd(self) -> str:
        """工作目录（默认工具目录）。"""
        return str(self.dir_path())

    def stdin_lines(self) -> int:
        """启动后自动回车应答的行数（交互式脚本用）。"""
        try:
            return int(self.cfg.get("stdin_lines", self.default_stdin_lines) or 0)
        except (TypeError, ValueError):
            return 0

    def ready(self, running: bool, port_open: bool) -> bool:
        """就绪判定：默认「进程存活」即视为运行；有端口的工具要求端口打开。"""
        if not running:
            return False
        if self.port():
            return bool(port_open)
        return True

    def open_url(self) -> str:
        """前端「打开工具」的地址（ui_type=web 时有效）。"""
        if self.ui_type != "web":
            return ""
        p = self.port()
        return f"http://127.0.0.1:{p}/" if p else ""

    def qrcode_path(self):
        """返回该工具的二维码图片路径（无则 None）。

        实现后，前端「配置」页会自动出现「扫码登录」区块：
        显示二维码 + 更新时间 + 是否可能过期，并自动跟随文件刷新。
        这是【文件兜底】方式；若工具提供了 WebUI API（见 qrcode_api），优先用 API。
        """
        return None

    def qrcode_api(self):
        """返回该工具 WebUI 的二维码 API 信息（无则 None）。

        约定返回：{"url": "http://127.0.0.1:3081", "token": "<webui token>"}
        Hub 会调用 <url>/api/login-qrcode（header x-webui-token）取二维码，
        自带精确的过期时间，且按实例端口区分，不会与其他实例互相覆盖。
        """
        return None

    # ── 桥接钩子（可选覆盖）────────────────────────────────
    def on_held_message(self, payload: dict) -> dict:
        """拦截到的消息进入待审队列前的加工钩子。

        payload = {"raw": 原始文本, "parsed": {...}, ...}
        返回可修改的 payload（如自动解析群号/发送者）。
        """
        return payload

    def on_approved_message(self, text: str) -> str:
        """消息被人工批准上报前的最终加工（可加前缀等）。"""
        return text

    def bridge_apply(self, hub_base: str) -> dict:
        """把工具自身的配置切换到「经 Hub 代理」（可选实现）。

        用途：一键完成对接，不用手工改工具 config.json。
        返回 {"ok": bool, "message": str, "changed": {...}}
        未实现时前端不显示该按钮。
        """
        return {"ok": False, "message": "该工具不支持自动对接配置"}

    # ── 序列化 ────────────────────────────────────────────
    def meta(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "ui_type": self.ui_type,
            "supports_log": self.supports_log,
            "bridge_mode": self.bridge_mode,
            "defaults": {
                "dirname": self.default_dirname,
                "entry": self.default_entry,
                "args": list(self.default_args),
                "env_name": self.default_env_name,
                "port": self.default_port,
                "intercept": self.default_intercept,
                "stdin_lines": self.default_stdin_lines,
                "enabled": self.default_enabled,
            },
        }
