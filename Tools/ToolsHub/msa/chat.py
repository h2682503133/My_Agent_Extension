# -*- coding: utf-8 -*-
"""工具聊天会话层：维护「工具 ⇄ agent」的聊天记录，并订阅 agent 回复。

每个工具一份记录（data/chat_<tool>.json），条目类型：
    pending  —— 被扣留、等待人工决定的消息（全文显示，带发送/删除）
    outgoing —— 已发给 agent 的消息（工具转发或人工发送）
    incoming —— agent 的回复（来自 MSA SSE 订阅）

界面按聊天流呈现，所以这里既存「工具上报」也存「人工发送」与「agent 回复」。
"""

import json
import re
import threading
import time
import uuid
from pathlib import Path

MAX_ITEMS = 400


class ChatStore:
    def __init__(self, hub, tool_id: str):
        self.hub = hub
        self.tool_id = tool_id
        self._lock = threading.Lock()
        self._items: list[dict] = []
        self._load()

    # ── 持久化 ───────────────────────────────────────────
    def _path(self) -> Path:
        return Path(self.hub.data_dir()) / f"chat_{self.tool_id}.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._items = data
            except Exception:
                pass

    def _save(self):
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._items[-MAX_ITEMS:], ensure_ascii=False, indent=2),
                     encoding="utf-8")

    # ── 追加 ─────────────────────────────────────────────
    def add(self, kind: str, text: str, **extra) -> dict:
        item = {
            "id": "c_" + uuid.uuid4().hex[:10],
            "kind": kind,                      # pending / outgoing / incoming
            "text": text,
            "time": time.strftime("%H:%M:%S"),
            "ts": time.time(),
            "status": extra.pop("status", "ok"),
        }
        item.update(extra)
        with self._lock:
            self._items.append(item)
            del self._items[:-MAX_ITEMS]
        self._save()
        self.hub.notify({"type": "chat", "tool": self.tool_id, "item": item})
        return item

    def find(self, item_id: str) -> dict | None:
        with self._lock:
            for i in self._items:
                if i["id"] == item_id:
                    return i
        return None

    def update(self, item_id: str, **patch) -> dict | None:
        it = self.find(item_id)
        if it is None:
            return None
        it.update(patch)
        self._save()
        self.hub.notify({"type": "chat", "tool": self.tool_id, "item": it})
        return it

    def list(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return list(self._items[-limit:])

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items = []
        self._save()
        self.hub.notify({"type": "chat", "tool": self.tool_id, "item": None})
        return n


class ChatSession:
    """订阅某个 user_id 的 agent 回复，写入对应工具的聊天记录。"""

    def __init__(self, hub, tool_id: str):
        self.hub = hub
        self.tool_id = tool_id
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        tool = self.hub.adapter(self.tool_id)
        if tool is None:
            return
        cfg = self.hub.raw_tool_cfg(self.tool_id)
        uid = str(cfg.get("msa_user_id") or
                  (self.hub.config().get("msa", {}) or {}).get("default_user_id") or "").strip()
        if not uid:
            return
        print(f"[chat:{self.tool_id}] 订阅 agent 回复 user={uid}")
        store = self.hub.chat_store(self.tool_id)
        client = self.hub.bridge.client_for(self.tool_id)
        for ev in client.iter_events(uid):
            if self._stop.is_set():
                break
            if ev.get("type") != "assistant_message":
                continue
            text = (ev.get("text") or "").strip()
            if not text:
                continue
            meta = ev.get("metadata") or {}
            agent_id = meta.get("agent_id") or ev.get("agent_id") or ""
            # 平台可能给文本加前缀：`agent_id: ` 或 `<agent_id>: `，去掉以便阅读
            m = re.match(r"^<([A-Za-z0-9_\-]+)>\s*[:：]\s*", text)
            if m:
                agent_id = agent_id or m.group(1)
                text = text[m.end():].strip()
            elif agent_id and (text.startswith(agent_id + ":") or text.startswith(agent_id + "：")):
                text = text[len(agent_id) + 1:].strip()
            store.add("incoming", text, agent_id=agent_id, task_id=ev.get("task_id") or "")
