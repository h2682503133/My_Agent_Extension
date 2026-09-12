# -*- coding: utf-8 -*-
"""桥：工具 ⇄ My_Agent_MSA 的中间对接层。

核心能力
  1. 同构代理：Hub 暴露与网关同形的接口，工具只改 gateway_url 即可接入
        /t/<tool_id>/api/login      → 透传给 MSA
        /t/<tool_id>/api/messages   → intercept ? 扣留待审 : 直接上报
        /t/<tool_id>/api/events     → intercept ? 空闲流 : 透传事件流
  2. 扣留待审（hold）：把工具上报的消息存起来，由人在前端决定「上报」或「删除」，
     并可在上报前自行添加其他信息（前缀 + 正文编辑）。
  3. 收发流水：所有经桥的消息都记账，供前端查看。
"""

import json
import threading
import time
import uuid
from pathlib import Path

from .client import MsaClient

MAX_PENDING = 300


class Bridge:
    def __init__(self, hub):
        """hub: 提供 config()、adapter(tool_id)、data_dir() 的宿主对象。"""
        self.hub = hub
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self._traffic: list[dict] = []
        self._load()

    # ── 持久化 ───────────────────────────────────────────
    def _path(self) -> Path:
        return Path(self.hub.data_dir()) / "pending.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._pending = list(data.get("pending") or [])
                    self._traffic = list(data.get("traffic") or [])
            except Exception:
                pass

    def _save(self):
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pending": self._pending[-MAX_PENDING:],
            "traffic": self._traffic[-MAX_PENDING:],
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── MSA 客户端 ───────────────────────────────────────
    def client_for(self, tool_id: str = "") -> MsaClient:
        cfg = self.hub.config()
        url = str(cfg.get("msa", {}).get("gateway_url") or "http://127.0.0.1:8080")
        # 每个工具一个客户端实例（隔离去重状态）
        with self._lock:
            cache = getattr(self, "_clients", None)
            if cache is None:
                cache = {}
                self._clients = cache
            key = f"{tool_id}|{url}"
            c = cache.get(key)
            if c is None:
                c = MsaClient(url)
                cache[key] = c
            return c

    def _defaults(self, tool_id: str) -> tuple[str, str]:
        """取该工具的默认 user_id / agent_id（工具配置优先，回落全局）。"""
        cfg = self.hub.config()
        msa = cfg.get("msa", {}) or {}
        tool_cfg = (cfg.get("tools", {}) or {}).get(tool_id, {}) or {}
        uid = str(tool_cfg.get("msa_user_id") or msa.get("default_user_id") or "").strip()
        aid = str(tool_cfg.get("msa_agent_id") or msa.get("default_agent_id") or "main").strip()
        return uid, aid

    # ── 扣留待审 ─────────────────────────────────────────
    def hold(self, tool_id: str, raw: str, extra: dict | None = None) -> dict:
        ad = self.hub.adapter(tool_id)
        payload = {"raw": raw, "parsed": {}, "summary": raw[:80]}
        if ad is not None:
            try:
                payload = ad.on_held_message(payload) or payload
            except Exception:
                pass
        uid, aid = self._defaults(tool_id)
        item = {
            "id": "p_" + uuid.uuid4().hex[:10],
            "tool": tool_id,
            "tool_name": getattr(ad, "name", tool_id) if ad else tool_id,
            "created_at": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "raw": raw,
            "parsed": payload.get("parsed") or {},
            "summary": payload.get("summary") or raw[:80],
            "user_id": uid,
            "agent_id": aid,
            "final_text": "",
            "result": None,
            "extra": extra or {},
        }
        with self._lock:
            self._pending.append(item)
            del self._pending[:-MAX_PENDING]
        self._save()
        # 同步写入聊天流（界面上按「收到待审」呈现，带 发送/删除 按钮）
        try:
            store = self.hub.chat_store(tool_id)
            chat_item = store.add(
                "pending", raw,
                parsed=payload.get("parsed") or {},
                summary=payload.get("summary") or raw[:80],
                pending_id=item["id"],
                status="pending",
            )
            item["chat_id"] = chat_item["id"]
            self._save()
        except Exception:
            pass
        self.hub.notify({"type": "pending", "action": "add", "item": item})
        return item

    def pending_list(self, status: str = "") -> list[dict]:
        with self._lock:
            items = list(self._pending)
        if status:
            items = [i for i in items if i.get("status") == status]
        return list(reversed(items))

    def get_pending(self, pid: str) -> dict | None:
        with self._lock:
            for i in self._pending:
                if i["id"] == pid:
                    return i
        return None

    def approve(self, pid: str, text: str = "", prefix: str = "",
                user_id: str = "", agent_id: str = "") -> dict:
        """上报该扣留消息（可带自定义前缀 / 编辑后的正文）。"""
        item = self.get_pending(pid)
        if item is None:
            return {"ok": False, "message": "待审条目不存在"}
        if item.get("status") != "pending":
            return {"ok": False, "message": f"该条目已处理（{item.get('status')}）"}

        body = (text if text.strip() else item.get("raw") or "").strip()
        final_text = (prefix.strip() + body) if prefix.strip() else body
        uid = (user_id or item.get("user_id") or "").strip()
        aid = (agent_id or item.get("agent_id") or "main").strip()
        if not uid:
            return {"ok": False, "message": "未配置上报用的 user_id（前端设置里填写）"}

        ad = self.hub.adapter(item.get("tool") or "")
        if ad is not None:
            try:
                final_text = ad.on_approved_message(final_text)
            except Exception:
                pass

        try:
            res = self.client_for(item.get("tool") or "").send(uid, final_text, aid)
        except Exception as e:
            self._log_traffic(item.get("tool"), "approve-failed", final_text, uid, aid, str(e))
            return {"ok": False, "message": f"上报失败：{e}"}

        item["status"] = "approved"
        item["decided_at"] = time.time()
        item["final_text"] = final_text
        item["user_id"], item["agent_id"] = uid, aid
        item["result"] = res
        self._save()
        try:
            store = self.hub.chat_store(item.get("tool") or "")
            if item.get("chat_id"):
                store.update(item["chat_id"], status="sent", final_text=final_text)
            store.add("outgoing", final_text, source="approve", agent_id=aid, user_id=uid)
        except Exception:
            pass
        self._log_traffic(item.get("tool"), "approve", final_text, uid, aid, "")
        self.hub.notify({"type": "pending", "action": "update", "item": item})
        return {"ok": True, "message": f"已上报给 {aid}（user={uid}）", "item": item, "result": res}

    def delete(self, pid: str) -> dict:
        item = self.get_pending(pid)
        if item is None:
            return {"ok": False, "message": "待审条目不存在"}
        if item.get("status") != "pending":
            return {"ok": False, "message": f"该条目已处理（{item.get('status')}）"}
        item["status"] = "deleted"
        item["decided_at"] = time.time()
        self._save()
        try:
            store = self.hub.chat_store(item.get("tool") or "")
            if item.get("chat_id"):
                store.update(item["chat_id"], status="deleted")
        except Exception:
            pass
        self._log_traffic(item.get("tool"), "delete", item.get("raw") or "",
                          item.get("user_id", ""), item.get("agent_id", ""), "")
        self.hub.notify({"type": "pending", "action": "update", "item": item})
        return {"ok": True, "message": "已删除（未上报）"}

    def clear_decided(self) -> dict:
        """清理已处理的历史条目。"""
        with self._lock:
            before = len(self._pending)
            self._pending = [i for i in self._pending if i.get("status") == "pending"]
            removed = before - len(self._pending)
        self._save()
        self.hub.notify({"type": "pending", "action": "reload"})
        return {"ok": True, "message": f"已清理 {removed} 条历史记录"}

    # ── 直通 / 拦截决策 ───────────────────────────────────
    def should_intercept(self, tool_id: str) -> bool:
        ad = self.hub.adapter(tool_id)
        if ad is None:
            return False
        tool_cfg = (self.hub.config().get("tools", {}) or {}).get(tool_id, {}) or {}
        if "intercept" in tool_cfg:
            return bool(tool_cfg.get("intercept"))
        return bool(getattr(ad, "default_intercept", False))

    def forward(self, tool_id: str, content: str,
                user_id: str = "", agent_id: str = "", source: str = "direct") -> dict:
        """直接上报（不扣留）。"""
        duid, daid = self._defaults(tool_id)
        uid = (user_id or duid).strip()
        aid = (agent_id or daid or "main").strip()
        if not uid:
            raise RuntimeError("未配置上报用的 user_id")
        res = self.client_for(tool_id).send(uid, content, aid)
        self._log_traffic(tool_id, source, content, uid, aid, "")
        try:
            self.hub.chat_store(tool_id).add("outgoing", content, source=source,
                                             agent_id=aid, user_id=uid)
        except Exception:
            pass
        return res

    def auto_send(self, tool_id: str) -> bool:
        """该工具是否开启「自动发送」（收到即上报，不扣留）。"""
        item = (self.hub.config().get("tools", {}) or {}).get(tool_id, {}) or {}
        if "auto_send" in item:
            return bool(item.get("auto_send"))
        ad = self.hub.adapter(tool_id)
        return bool(getattr(ad, "default_auto_send", False))

    # ── 流水 ─────────────────────────────────────────────
    def _log_traffic(self, tool_id, action, text, uid, aid, error):
        with self._lock:
            self._traffic.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "tool": tool_id,
                "action": action,
                "text": (text or "")[:500],
                "user_id": uid,
                "agent_id": aid,
                "error": error,
            })
            del self._traffic[:-MAX_PENDING]
        self._save()

    def traffic(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(reversed(self._traffic[-limit:]))

    def stats(self) -> dict:
        with self._lock:
            items = list(self._pending)
        return {
            "pending": sum(1 for i in items if i.get("status") == "pending"),
            "approved": sum(1 for i in items if i.get("status") == "approved"),
            "deleted": sum(1 for i in items if i.get("status") == "deleted"),
        }
