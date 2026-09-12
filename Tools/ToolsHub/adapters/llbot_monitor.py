# -*- coding: utf-8 -*-
"""LLBot群聊监控 对接适配器。

对接要点：
  - 该工具原先把命中消息【直接】上报给 agent（forward_mode=web，POST 网关 /api/messages）；
  - 接入 ToolsHub 后，把它的 gateway_url 指向 Hub 的同构代理地址
        http://<hub>/t/llbot_monitor
    Hub 即可把消息【扣留】进待审队列，由人决定「上报」还是「删除」，
    并可在上报前自行添加其他信息（前缀/正文编辑）。
  - 工具代码无需改动，仅其 config.json 的地址字段变化（可用 bridge_apply 一键切换）。
"""

import json
import re
from pathlib import Path

from .base import ToolAdapter

# 工具上报的原始格式：
#   【群聊监控转发 · 账号1】
#   群号：851648664
#   发送者：蝶殇、❀ (2195239690)
#   内容：12点10提醒我去领快递
RE_GROUP = re.compile(r"群号[:：]\s*(\S+)")
RE_SENDER = re.compile(r"发送者[:：]\s*(.+?)\s*\((\d+)\)")
RE_CONTENT = re.compile(r"内容[:：]\s*([\s\S]*)$")
RE_BANNER = re.compile(r"【群聊监控转发\s*·\s*([^】]+)】")


class LLBotMonitorAdapter(ToolAdapter):
    id = "llbot_monitor"
    name = "LLBot群聊监控"
    description = "监控 QQ 群聊，命中消息可扣留待审后上报给 agent"
    icon = "💬"

    default_dirname = "LLBot群聊监控"
    default_entry = "start.bat"
    default_args = []
    default_env_name = "agent"
    default_port = None
    default_stdin_lines = 2          # 启动时会依次询问 llbot.exe / QQ.exe 路径，回车沿用已存值

    bridge_mode = "proxy"
    default_intercept = True         # ★ 默认扣留：人工决定上报/删除

    ui_type = "none"
    supports_log = True

    # ── 待审消息加工：解析出群号 / 发送者 / 正文，便于前端友好展示与编辑 ──
    def on_held_message(self, payload: dict) -> dict:
        raw = payload.get("raw") or ""
        parsed = {"group": "", "sender": "", "sender_id": "", "content": raw, "account": ""}
        m = RE_BANNER.search(raw)
        if m:
            parsed["account"] = m.group(1).strip()
        m = RE_GROUP.search(raw)
        if m:
            parsed["group"] = m.group(1).strip()
        m = RE_SENDER.search(raw)
        if m:
            parsed["sender"] = m.group(1).strip()
            parsed["sender_id"] = m.group(2).strip()
        m = RE_CONTENT.search(raw)
        if m:
            parsed["content"] = m.group(1).strip()
        payload["parsed"] = parsed
        payload["summary"] = (
            f"群 {parsed['group'] or '?'} · {parsed['sender'] or '?'}：{parsed['content'][:60]}"
        )
        return payload

    # ── 二维码 WebUI API（优先）：按实例端口取码，自带过期时间 ──
    def qrcode_api(self):
        """返回 llbot WebUI 的二维码 API 源。

        配置来源（优先工具自身 config.json，其次 Hub 里的工具配置）：
          webui_url / webui_port —— WebUI 地址（默认取 base_webui_port）
          webui_token            —— WebUI 的 x-webui-token（浏览器 cookie webui_token 的值）
        """
        url = str(self.cfg.get("qrcode_api_url") or "").strip()
        token = str(self.cfg.get("qrcode_api_token") or "").strip()
        port = self.cfg.get("qrcode_api_port")

        cfg_path = self.dir_path() / "config.json"
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if not url:
                    u = str(data.get("webui_url") or "").strip()
                    p = data.get("webui_port") or data.get("base_webui_port")
                    if u:
                        url = u
                    elif p:
                        port = p
                if not token:
                    token = str(data.get("webui_token") or "").strip()
            except Exception:
                pass

        if not url and port:
            try:
                url = f"http://127.0.0.1:{int(port)}"
            except (TypeError, ValueError):
                url = ""
        if not url:
            return None
        return {"url": url.rstrip("/"), "token": token}

    # ── 二维码：定位 llbot 的登录二维码文件（API 不可用时的兜底）──
    def qrcode_path(self):
        """返回 llbot 登录二维码文件（多个候选里取最新修改的那个）。

        候选位置：
          1) 工具目录本身：qrcode.png / bin/llbot/qrcode.png
          2) 由工具 config.json 的 llbot_path 推导：
             <llbot 所在目录>/qrcode.png
             <llbot 所在目录>/bin/llbot/qrcode.png
             <llbot 所在目录>/data/qrcode.png
          3) 工具 config.json 里可直接指定 qrcode_path
        """
        cands = []
        d = self.dir_path()
        cands += [d / "qrcode.png", d / "bin" / "llbot" / "qrcode.png"]

        cfg_path = d / "config.json"
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                llbot = str(data.get("llbot_path") or "").strip()
                if llbot:
                    base = Path(llbot).parent
                    cands += [
                        base / "qrcode.png",
                        base / "bin" / "llbot" / "qrcode.png",
                        base / "data" / "qrcode.png",
                        base / "bin" / "llbot" / "data" / "qrcode.png",
                    ]
                qr = str(data.get("qrcode_path") or "").strip()
                if qr:
                    cands.append(Path(qr))
            except Exception:
                pass

        best = None
        for p in cands:
            try:
                if p.is_file():
                    if best is None or p.stat().st_mtime > best.stat().st_mtime:
                        best = p
            except Exception:
                continue
        return best

    # ── 一键对接：把工具 config.json 切到走 Hub 代理 ──
    def bridge_apply(self, hub_base: str) -> dict:
        cfg_path = self.dir_path() / "config.json"
        if not cfg_path.exists():
            return {"ok": False, "message": f"未找到 {cfg_path}"}
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "message": f"读取 config.json 失败：{e}"}

        before = {k: data.get(k) for k in ("forward_mode", "gateway_url", "web_user_id", "web_agent_id")}
        data["forward_mode"] = "web"
        data["gateway_url"] = self.bridge_gateway_url(hub_base)
        if not str(data.get("web_user_id") or "").strip():
            data["web_user_id"] = str(self.cfg.get("msa_user_id") or "").strip()
        if not str(data.get("web_agent_id") or "").strip():
            data["web_agent_id"] = str(self.cfg.get("msa_agent_id") or "main").strip()
        data["web_keepalive"] = False      # 消息由 Hub 扣留，工具侧无需订阅事件流
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        after = {k: data.get(k) for k in ("forward_mode", "gateway_url", "web_user_id", "web_agent_id")}
        return {
            "ok": True,
            "message": f"已写入 {cfg_path.name}：gateway_url → {after['gateway_url']}（消息将被 Hub 扣留待审）",
            "changed": {"before": before, "after": after},
        }

    def bridge_revert(self, gateway_url: str = "http://127.0.0.1:8080") -> dict:
        """还原为直连 MSA 网关。"""
        cfg_path = self.dir_path() / "config.json"
        if not cfg_path.exists():
            return {"ok": False, "message": f"未找到 {cfg_path}"}
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "message": f"读取 config.json 失败：{e}"}
        data["gateway_url"] = gateway_url
        data["web_keepalive"] = True
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "message": f"已还原直连：{gateway_url}"}
