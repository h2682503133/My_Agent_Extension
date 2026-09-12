# -*- coding: utf-8 -*-
"""My_Agent_MSA 外部接口客户端（gateway-backend-service）。

只使用平台对外暴露的 HTTP 接口，绝不修改 MSA 项目本身：
    POST /api/login          用户登录（网关会绑定 web 渠道）
    POST /api/messages       提交消息给指定 agent
    GET  /api/events         SSE 事件流（agent 回复）
    GET  /api/health         健康检查
    GET  /api/conversations  会话列表

内置：登录会话缓存、自动重连、event_id 去重、task_id 首条回复规则。
"""

import json
import threading
import time

import requests


class MsaClient:
    def __init__(self, gateway_url: str):
        self.gateway_url = (gateway_url or "http://127.0.0.1:8080").rstrip("/")
        self._sessions: dict[str, requests.Session] = {}
        self._lock = threading.Lock()
        self._seen_event_ids: list[str] = []
        self._first_reply_tasks: list[str] = []

    # ── 基础设施 ─────────────────────────────────────────
    def health(self) -> dict:
        try:
            r = requests.get(f"{self.gateway_url}/api/health", timeout=5)
            data = {}
            try:
                data = r.json()
            except ValueError:
                pass
            return {"ok": r.status_code == 200, "status": r.status_code, "detail": data}
        except Exception as e:
            return {"ok": False, "status": 0, "error": str(e)}

    def login(self, user_id: str) -> requests.Session:
        """登录（同一 user_id 复用会话）。"""
        uid = (user_id or "").strip()
        if not uid:
            raise RuntimeError("缺少 user_id")
        with self._lock:
            sess = self._sessions.get(uid)
        if sess is not None:
            return sess
        sess = requests.Session()
        r = sess.post(f"{self.gateway_url}/api/login", json={"user_id": uid}, timeout=10)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or not data.get("ok"):
            raise RuntimeError(f"登录失败 user={uid}: HTTP {r.status_code} {data}")
        with self._lock:
            self._sessions[uid] = sess
        return sess

    def send(self, user_id: str, content: str, agent_id: str = "main") -> dict:
        """把消息发给 agent（不扣留，直接上报）。"""
        sess = self.login(user_id)
        r = sess.post(
            f"{self.gateway_url}/api/messages",
            json={"user_id": user_id, "content": content, "agent_id": agent_id or "main"},
            timeout=30,
        )
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or not data.get("ok"):
            raise RuntimeError(f"上报失败: HTTP {r.status_code} {data}")
        return data

    # ── 事件流 ───────────────────────────────────────────
    def iter_events(self, user_id: str, agent_id: str = ""):
        """生成器：持续产出该 user 的 SSE 事件（dict）。断线自动重连。

        内置去重与「同一 task 只保留首条用户可见回复」规则（与
        agents‘chat 的修复保持一致）。
        """
        q = f"?user_id={user_id}"
        if agent_id:
            q += f"&agent_id={agent_id}"
        url = f"{self.gateway_url}/api/events{q}"
        while True:
            try:
                sess = self.login(user_id)
                with sess.get(url, stream=True, timeout=(10, 90)) as resp:
                    if resp.status_code != 200:
                        time.sleep(5)
                        continue
                    for raw in resp.iter_lines(decode_unicode=True):
                        if not raw or raw.startswith(":"):
                            continue
                        if not raw.startswith("data:"):
                            continue
                        try:
                            obj = json.loads(raw[5:].strip())
                        except Exception:
                            continue
                        if self._is_duplicate(obj):
                            continue
                        yield obj
            except Exception:
                time.sleep(5)

    def _is_duplicate(self, obj: dict) -> bool:
        """event_id 去重 + 同 task 只放行首条用户可见回复。"""
        eid = obj.get("event_id") or ""
        if eid:
            with self._lock:
                if eid in self._seen_event_ids:
                    return True
                self._seen_event_ids.append(eid)
                if len(self._seen_event_ids) > 500:
                    del self._seen_event_ids[:len(self._seen_event_ids) - 500]

        if obj.get("type") not in ("assistant_message", "task_waiting_user"):
            return False
        meta = obj.get("metadata") or {}
        if str(meta.get("visible_to_user", "true")).strip().lower() == "false":
            return True
        tid = obj.get("task_id") or ""
        if not tid:
            return False
        with self._lock:
            if tid in self._first_reply_tasks:
                return True
            self._first_reply_tasks.append(tid)
            if len(self._first_reply_tasks) > 500:
                del self._first_reply_tasks[:len(self._first_reply_tasks) - 500]
        return False

    def conversations(self, user_id: str) -> dict:
        try:
            r = requests.get(
                f"{self.gateway_url}/api/conversations",
                params={"user_id": user_id},
                timeout=10,
            )
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}
