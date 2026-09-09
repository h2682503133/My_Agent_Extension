# -*- coding: utf-8 -*-
"""
群聊版多智能体对话调度器 —— 手机比例 Web 前端服务
====================================================
在原有双智能体调度器 (web_server.py) 基础上改为「群聊」模式：

- 群聊成员任意多（配置 MEMBERS）
- 用户可以在任何轮次插话；插话后由调度 LLM 自动路由到合适的成员
- 用户消息为空 → 直接把上一轮回复路由给下一位成员（自动轮转）
- 消息统一带 <说话者id>: 前缀（避免智能体按 USER.md 把其他智能体误当用户）
- 聊天记录 (chat_history.json) 在内部注入调度提示词，由调度 LLM 决策

调度 LLM 默认用本地 ollama（llama3.1:8b），可配置。

用法：
  1. 先启动 gateway-backend-service 网关 (127.0.0.1:8080)
  2. python web_server.py
  3. 浏览器/手机访问 http://<本机IP>:8090
"""

import json
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

# ===================== 配置 =====================
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = "8080"
WEB_HOST = "0.0.0.0"
WEB_PORT = 8090

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")
# 群聊状态文件：在线成员/成员特征/离场名单持久化，重启不丢，可手工编辑
STATE_FILE = os.path.join(BASE_DIR, "chat_state.json")

# 登录账号（单账号即可；消息经 gateway 用同一 session 转发给不同 agent）
USER = "agent_e"
AGENT_DEFAULT = "main"

# ── 群聊成员（默认；可在前端设置面板填写/修改，运行时可换）──
MEMBERS_DEFAULT = ["lilith", "alice", "luna"]
MEMBERS = list(MEMBERS_DEFAULT)

# ── 群聊剧本（session）──
# 固定前缀 grp_agents_chat_ + 输入后缀；后缀空 = 保留旧 session grp_agents_chat（兼容现有历史）
SESSION_PREFIX = "grp_agents_chat_"
SESSION_SUFFIX = ""
# 群聊共享 session（openviking v32：grp_ 前缀 → 多 agent 共享历史）
SESSION_ID = "grp_agents_chat"

def current_history_file() -> str:
    """当前剧本对应的历史文件：后缀空 → chat_history.json，非空 → chat_history_<suffix>.json"""
    if SESSION_SUFFIX:
        return os.path.join(BASE_DIR, f"chat_history_{SESSION_SUFFIX}.json")
    return HISTORY_FILE

def current_session_id() -> str:
    """当前剧本的 session_id：后缀空 → grp_agents_chat（旧），非空 → grp_agents_chat_<suffix>"""
    if SESSION_SUFFIX:
        return f"{SESSION_PREFIX}{SESSION_SUFFIX}"
    return "grp_agents_chat"

# 真人用户（插话者）显示名：消息前缀用 <该名字>:
USER_SPEAKER = "user"
# 是否给用户消息注入 <USER_SPEAKER>: 前缀（False = 不注入，消息原样发送，模型按默认用户处理）
USER_INJECT = True

# 成员特征（可选）：帮助调度 LLM 把话题关联到成员；键=成员名，值=一行身份描述
MEMBER_PROFILES = {
    "lilith": "魅魔，lilith姐姐，热情主动",
    "alice": "精灵修道院的修女，傲娇妹妹",
    "luna": "月之魔女，夜间卖魔药，内向",
}

# ── 调度 LLM（用于选择下一位说话者）──
SCHEDULER_LLM = {
    "api_url": "http://127.0.0.1:11434/api/chat",   # 本机 ollama；k8s 内为 http://host.docker.internal:11434/api/chat
    "model": "llama3.1:8b",
    "temperature": 1.4,
    "options": {"num_ctx": 8192, "num_predict": 128},
    "timeout": 60,
}

# 路由提示词注入的历史条数
ROUTE_HISTORY_LIMIT = 12

# ================================================

lock = threading.Lock()
running = True
started = False
browser_queues = []
user_sessions = {}
listener_stops = {}
listener_threads = {}
recent_event_ids = []

# 群聊状态
pending_reply = None       # 最近收到的回复文本（待路由）
pending_speaker = None     # 该回复的说话者（agent_id）
waiting = False            # 是否正在等待回复
# 已知 agent 集合：所有出现过的 agent_id（含已离场成员），供文本前缀解析兜底
KNOWN_AGENTS = set()
# 离场成员名单：remove_member 加入、add_member 移除；供「编造离开期间事件」筛选
LEFT_MEMBERS = []
# 编造任务登记：task_id -> 离场角色名（do_fabricate 登记，handle_event 按 task_id 识别回复插入群聊）
FABRICATE_TASKS = {}
# 编造事件草稿：agent -> {"text","time","sent"}；编造只生成草稿（再次编造覆盖），
# 该角色重新入场(add_member)时才插入群聊并标记 sent=True
FABRICATE_DRAFTS = {}


# ===================== 历史记录 =====================
def load_history():
    fpath = current_history_file()
    with lock:
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []


def save_history(history):
    fpath = current_history_file()
    with lock:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)


def record_message(speaker: str, text: str):
    """记录一条群聊消息（speaker: text，与旧格式兼容）。"""
    history = load_history()
    history.append(f"{speaker}: {text}")
    if len(history) > 500:
        history = history[-500:]
    save_history(history)


# ===================== 调度 LLM =====================
def call_scheduler_llm(messages):
    """调用 ollama /api/chat 完成调度决策，返回文本。"""
    body = {
        "model": SCHEDULER_LLM["model"],
        "messages": messages,
        "stream": False,
        "temperature": SCHEDULER_LLM.get("temperature", 1.4),
    }
    if SCHEDULER_LLM.get("options"):
        body["options"] = SCHEDULER_LLM["options"]
    try:
        resp = requests.post(
            SCHEDULER_LLM["api_url"],
            json=body,
            timeout=SCHEDULER_LLM.get("timeout", 60),
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message") or {}).get("content", "").strip()
    except Exception as exc:
        print(f"[调度LLM失败] {exc}")
        return ""


def _log_route_decision(raw_out: str, names: list, last_speaker: str, result: str, reason: str):
    """打印一次路由决策日志：LLM 原始输出、解析出的名字序列、最终选择与原因。

    用于排查「为什么路由到 X」——区分 LLM 失败/解析失败/被 last_speaker 否决/正常。
    """
    print(
        f"[路由决策] LLM输出={raw_out[:100]!r} | 解析名字={names or '无'} | "
        f"last={last_speaker or '无'} | 最终={result} | 原因={reason}",
        flush=True,
    )


def pick_next_speaker(current_text: str, current_speaker: str, last_speaker: str = ""):
    """用调度 LLM 选择下一位发言成员。

    current_text   : 当前内容（用户插话 或 上轮回复）
    current_speaker: 当前内容的说话者（USER_SPEAKER 或 agent_id）
    last_speaker   : 刚发言过的成员（自动轮转时避免重复）
    返回选中的成员 agent_id；失败时按轮转兜底。
    """
    # 规则优先（仅限用户插话）：用户消息里整词精确点名某成员 → 直接路由该成员。
    # 自动轮转（成员间转发）不走此规则——成员提到其他成员只是对话内容，不代表点名，
    # 由 LLM 根据上下文决策更自然。
    if current_speaker == USER_SPEAKER:
        for m in MEMBERS:
            for token in str(current_text).replace("，", ",").replace("、", ",").replace(" ", ",").split(","):
                tok = token.strip("[]{}<>:：\"'。.!！?？\n\t")
                if tok == m:
                    if m != last_speaker:
                        return m
                    idx = MEMBERS.index(m)
                    return MEMBERS[(idx + 1) % len(MEMBERS)]

    history = load_history()[-ROUTE_HISTORY_LIMIT:]
    members_str = "、".join(MEMBERS)
    # 成员特征注入：帮助 LLM 把话题关联到成员
    profile_lines = "\n".join(
        f"- {m}：{MEMBER_PROFILES.get(m, '（无特征描述）')}" for m in MEMBERS
    )

    if current_speaker == USER_SPEAKER:
        scene = f"用户（{USER_SPEAKER}）刚刚插话：{current_text[:200]}"
    else:
        scene = f"成员 {current_speaker} 刚刚说完：{current_text[:200]}"

    system_prompt = (
        "你是群聊调度器。当前群聊在场成员（智能体）只有这些："
        f"{members_str}\n"
        "成员特征：\n"
        f"{profile_lines}\n"
        "规则：\n"
        "1. 根据群聊历史和当前消息，选择下一位最应该发言的成员\n"
        "2. 只输出一个成员名（agent_id），不要输出任何其他内容\n"
        f"3. 不要选择刚发言过的成员（{last_speaker}），除非场景明确需要\n"
        "4. 用户插话时，选择最应该回应这条消息的成员"
    )

    history_text = "\n".join(f"- {h}" for h in history) if history else "（暂无历史）"
    user_prompt = (
        f"群聊历史：\n{history_text}\n\n"
        f"当前：{scene}\n\n"
        "下一位发言成员："
    )

    out = call_scheduler_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    # 解析：收集 LLM 输出中出现的全部成员名（按出现位置排序，精确优先）。
    # last_speaker 从「硬否决」降级为「优先跳过」：
    # - 有多个名字 → 跳过 last_speaker 取下一个
    # - 只有 last_speaker（或全被跳过）→ 严格用它，不轮转
    # - 完全没输出任何名字 → 才走轮转兜底
    ordered = []  # [(pos, member, exact)]
    cleaned = out.strip()
    for m in MEMBERS:
        exact_hit = False
        for token in cleaned.replace("，", ",").replace("、", ",").replace(" ", ",").split(","):
            tok = token.strip("[]{}<>:：\"'。.!！?？\n\t")
            if tok == m:
                exact_hit = True
                pos = cleaned.find(m)
                break
        if not exact_hit:
            pos = cleaned.find(m)
            if pos < 0:
                continue
        ordered.append((pos, m, exact_hit))

    if ordered:
        # 精确匹配优先；无精确命中才用子串命中的
        exacts = [x for x in ordered if x[2]]
        pool = exacts if exacts else ordered
        pool.sort(key=lambda x: x[0])
        names = [x[1] for x in pool]
        # 只考虑仍在场的成员（离场成员名不作为路由目标）
        active = [n for n in names if n in MEMBERS]
        # 跳过 last_speaker 取第一个其他名字
        others = [n for n in active if n != last_speaker]
        if others:
            _log_route_decision(out, names, last_speaker, others[0], "跳过last取下一个")
            return others[0]
        if active:
            # 没别的名字（模型明确选了 last_speaker）→ 严格用它
            _log_route_decision(out, names, last_speaker, active[0], "模型明确选last，严格使用")
            return active[0]
        # 模型输出全为离场成员名 → 视同无有效名字，走轮转兜底
        if last_speaker and last_speaker in MEMBERS:
            idx = MEMBERS.index(last_speaker)
            _log_route_decision(out, names, last_speaker, MEMBERS[(idx + 1) % len(MEMBERS)], "输出均为离场成员，轮转兜底")
            return MEMBERS[(idx + 1) % len(MEMBERS)]
        _log_route_decision(out, names, last_speaker, MEMBERS[0], "输出均为离场成员且无last，取列表首位")
        return MEMBERS[0]

    # 兜底：完全没输出任何成员名 → 按名单顺序轮转（跳过 last_speaker）
    if last_speaker and last_speaker in MEMBERS:
        idx = MEMBERS.index(last_speaker)
        _log_route_decision(out, [], last_speaker, MEMBERS[(idx + 1) % len(MEMBERS)], "无名字，轮转兜底")
        return MEMBERS[(idx + 1) % len(MEMBERS)]
    _log_route_decision(out, [], last_speaker, MEMBERS[0], "无名字且无last，取列表首位")
    return MEMBERS[0]


# ===================== 网关交互 =====================
def login(uid):
    sess = user_sessions[uid]
    try:
        resp = sess.post(
            f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/login",
            json={"user_id": uid},
            timeout=10,
        )
    except requests.ConnectionError as e:
        raise Exception(f"{uid} 登录失败：无法连接网关 {GATEWAY_HOST}:{GATEWAY_PORT}（{e}）")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200 or not data.get("ok"):
        raise Exception(f"{uid} 登录失败：HTTP {resp.status_code} {data}")
    print(f"[登录成功] {uid} session={data.get('session_id')}")


def send_msg(uid, content, agent_id, session_id=None, metadata=None):
    """向指定 agent 发送群聊消息。

    - session_id 默认当前剧本 session；传自定义 session 用于隔离（如编造事件）
    - metadata 透传给 scheduler（如 fabricate 标记，供回复识别）
    """
    sess = user_sessions[uid]
    payload = {
        "user_id": uid,
        "content": content,
        "agent_id": agent_id,
        "session_id": session_id or current_session_id(),
    }
    if metadata:
        payload["metadata"] = metadata
    try:
        resp = sess.post(
            f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/messages",
            json=payload,
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        print(f"[发送失败] {uid}: {e}")
        return {"ok": False, "error": str(e)}
    print(f"[发送] {uid} -> {agent_id} (session={payload['session_id']}) : {content[:60]}...  返回:{data}")
    return data


# ===================== 推送给浏览器 =====================
def broadcast(ev):
    with lock:
        qs = list(browser_queues)
    for q in qs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass


def set_status(text, mode=None):
    broadcast({"type": "status", "text": text, "mode": mode})


def add_system(text):
    broadcast({"type": "system", "text": text})


def add_msg(speaker, text):
    broadcast({"type": "msg", "speaker": speaker, "text": text})


# ===================== SSE 网关监听 =====================
def _seen_event(event_id: str) -> bool:
    if not event_id:
        return False
    with lock:
        if event_id in recent_event_ids:
            return True
        recent_event_ids.append(event_id)
        if len(recent_event_ids) > 200:
            del recent_event_ids[:len(recent_event_ids) - 200]
        return False


def handle_event(obj):
    etype = obj.get("type", "")
    text = (obj.get("text") or "").strip()
    error = (obj.get("error") or "").strip()

    if _seen_event(obj.get("event_id") or ""):
        return

    global pending_reply, pending_speaker, waiting
    # 编造事件回复识别（优先于其它分支）：task_id 在 FABRICATE_TASKS 登记中
    # （do_fabricate 的隔离 session 任务）。终态事件负责清理登记，assistant_message 负责插入。
    task_id = obj.get("task_id") or ""
    with lock:
        fabricate_agent = FABRICATE_TASKS.get(task_id, "") if task_id else ""
        if task_id and etype in ("task_completed", "task_failed"):
            FABRICATE_TASKS.pop(task_id, None)
    if fabricate_agent:
        if etype == "task_failed":
            add_msg("系统", f"⚠ 编造失败：{error or text or '未知错误'}")
            set_status("编造失败，请检查日志", mode="waiting")
            return
        if etype != "assistant_message" or not text:
            return
        text_clean = text
        if text_clean.startswith(fabricate_agent + ":") or text_clean.startswith(fabricate_agent + "："):
            text_clean = text_clean[len(fabricate_agent) + 1:].strip()
        # 编造 ≠ 发送：按角色存草稿（再次编造覆盖旧草稿），
        # 该角色重新入场(add_member)时才自动插入群聊。
        with lock:
            FABRICATE_DRAFTS[fabricate_agent] = {
                "text": text_clean,
                "time": time.strftime("%H:%M:%S"),
                "sent": False,
            }
        broadcast({"type": "fabricated", "agent": fabricate_agent,
                   "text": text_clean, "time": FABRICATE_DRAFTS[fabricate_agent]["time"]})
        set_status(f"编造完成：{fabricate_agent} 的事件已生成（{fabricate_agent} 重新入场时自动发送）", mode="waiting")
        print(f"[编造事件] {fabricate_agent} 事件已生成草稿（待入场发送）: {text_clean[:60]}...", flush=True)
        return

    if etype == "task_failed":
        add_msg("系统", f"⚠ 任务失败：{error or text or '未知错误'}")
        set_status("任务失败，请检查日志", mode="waiting")
        return

    if not text:
        return

    # 说话者：scheduler 给 assistant_message 文本加了 "agent_id: " 前缀（无尖括号），
    # 从文本前缀解析；metadata.agent_id 兜底。
    # 注意：不要求 agent_id 在 MEMBERS——离场成员的告别回复也要正确归属，
    # 不能被误标成默认 agent。
    speaker = ""
    md_agent = (obj.get("metadata") or {}).get("agent_id", "") or ""
    if md_agent:
        speaker = md_agent
        with lock:
            KNOWN_AGENTS.add(md_agent)
        if text.startswith(md_agent + ":") or text.startswith(md_agent + "："):
            text = text.split(":", 1)[1].strip() if ":" in text[:20] else text
            text = text.split("：", 1)[1].strip() if "：" in text[:20] else text
    else:
        # 无 metadata.agent_id：从文本前缀解析（支持任意已知 agent，含离场成员）
        with lock:
            known = list(MEMBERS) + list(KNOWN_AGENTS) + [AGENT_DEFAULT]
        for m in known:
            if text.startswith(m + ":") or text.startswith(m + "："):
                speaker = m
                rest = text[len(m) + 1:].strip()
                text = rest
                break
    if not speaker:
        speaker = AGENT_DEFAULT

    with lock:
        pending_reply = text
        pending_speaker = speaker
        waiting = False
    add_msg(speaker, text)
    record_message(speaker, text)
    set_status(f"收到 {speaker} 回复 · 回车=自动路由下一位 / 输入=插话", mode="pending")


def sse_listener_thread(uid):
    sess = user_sessions[uid]
    stop_ev = listener_stops[uid]
    # 不过滤 agent：群聊中回复来自任意成员，收该 user 的全部事件
    url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/events?user_id={uid}"
    while running and not stop_ev.is_set():
        try:
            print(f"[{uid}] 正在连接事件流 (SSE, 全部agent)")
            with sess.get(url, stream=True, timeout=(10, 90)) as resp:
                print(f"[{uid}] 事件流连接成功")
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not running or stop_ev.is_set():
                        break
                    if not raw_line or raw_line.startswith(":"):
                        continue
                    if not raw_line.startswith("data:"):
                        continue
                    payload = raw_line[5:].strip()
                    if not payload:
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    handle_event(obj)
        except Exception as e:
            if running and not stop_ev.is_set():
                print(f"[{uid}] SSE异常: {e}, 5秒重连")
                time.sleep(5)


def _stop_listeners():
    with lock:
        stops = {uid: ev for uid, ev in listener_stops.items()}
        sessions = {uid: s for uid, s in user_sessions.items()}
        threads = {uid: t for uid, t in listener_threads.items()}
    for ev in stops.values():
        ev.set()
    for s in sessions.values():
        try:
            s.close()
        except Exception:
            pass
    for t in threads.values():
        try:
            t.join(timeout=3)
        except Exception:
            pass
    with lock:
        listener_stops.clear()
        listener_threads.clear()


# ===================== 群聊调度逻辑 =====================
def set_members(members_str: str):
    """设置群聊成员（前端填写，逗号/顿号/空格分隔）。"""
    global MEMBERS
    parsed = []
    for part in str(members_str or "").split(","):
        for sub in part.replace("，", ",").replace("、", ",").replace(" ", ",").split(","):
            m = sub.strip()
            if m and m not in parsed:
                parsed.append(m)
    if parsed:
        MEMBERS = parsed
        save_state()
    return list(MEMBERS)


def add_member(name: str, profile: str = ""):
    """成员入场：加入 MEMBERS（去重）+ 可选特征 + 移出离场名单。返回当前成员列表。

    若该角色有未发送的编造草稿（离开期间事件），入场时自动插入群聊并标记已发送；
    若此时没有待路由的回复，则把入场消息作为第一条，自动路由给下一位成员
    （无需用户再发首条消息）。
    """
    global MEMBERS, MEMBER_PROFILES, LEFT_MEMBERS
    global pending_reply, pending_speaker, waiting
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "成员名不能为空"}, list(MEMBERS)
    with lock:
        if name not in MEMBERS:
            MEMBERS.append(name)
        LEFT_MEMBERS = [m for m in LEFT_MEMBERS if m != name]
        if profile and profile.strip():
            merged = dict(MEMBER_PROFILES)
            merged[name] = profile.strip()
            MEMBER_PROFILES = merged
    add_system(f"【成员入场】{name} 已加入群聊")
    # 编造草稿发送：该角色离场期间编造的事件，入场时自动插入群聊
    draft = None
    auto_route = False
    with lock:
        d = FABRICATE_DRAFTS.get(name)
        if d and not d.get("sent"):
            draft = {"text": d["text"], "time": d.get("time", "")}
            d["sent"] = True
        # 入场消息无条件接管对话流：即使有上一轮回复待转发也抢占——
        # 上一轮回复已写入群聊共享 session，下一次发送只是追加为 session 最后一条，不亏信息
        if draft and started:
            pending_reply = draft["text"]
            pending_speaker = name
            waiting = False
            auto_route = True
    if draft:
        add_msg(name, draft["text"])
        record_message(name, draft["text"])
        add_system(f"【编造事件】{name} 离开期间的事件已插入对话（入场自动发送）")
        broadcast({"type": "fabricate_sent", "agent": name, "text": draft["text"], "time": draft["time"]})
        print(f"[编造事件] {name} 入场，离开期间事件已发送: {draft['text'][:60]}...", flush=True)
    if auto_route:
        # 入场消息作为第一条：后台自动路由给下一位成员，无需用户发首条消息
        threading.Thread(target=_auto_route_entry, args=(), daemon=True).start()
        print(f"[自动路由] {name} 的入场消息将自动路由给下一位成员", flush=True)
    save_state()
    print(f"[成员入场] {name}（当前：{'、'.join(MEMBERS)}）", flush=True)
    return {"ok": True}, list(MEMBERS)


def _auto_route_entry():
    """把入场消息（pending_reply）自动路由给下一位成员；稍等让前端先展示入场消息。"""
    time.sleep(1.5)
    try:
        do_confirm("")
    except Exception as e:
        print(f"[入场自动路由异常] {e}", flush=True)


def remove_member(name: str):
    """成员离场：从 MEMBERS 移除并加入离场名单（特征保留，再入场可恢复）。

    注意：pending_reply/pending_speaker 不清理——离场成员可能有告别话，
    该回复仍需被转发，避免丢掉告别。
    """
    global MEMBERS, LEFT_MEMBERS
    name = str(name or "").strip()
    with lock:
        if name not in MEMBERS:
            return {"ok": False, "error": f"{name} 不在当前成员中"}, list(MEMBERS)
        MEMBERS = [m for m in MEMBERS if m != name]
        if name not in LEFT_MEMBERS:
            LEFT_MEMBERS.append(name)
    add_system(f"【成员离场】{name} 已离开群聊")
    print(f"[成员离场] {name}（当前：{'、'.join(MEMBERS)}）", flush=True)
    save_state()
    return {"ok": True}, list(MEMBERS)


def set_user_identity(uid: str, inject: bool = True):
    """设置用户身份：uid 显示名/注入 id；inject 是否注入 <id>: 前缀。"""
    global USER_SPEAKER, USER_INJECT
    uid = str(uid or "").strip()
    with lock:
        USER_SPEAKER = uid or "user"
        USER_INJECT = bool(inject)
    mode = f"<{USER_SPEAKER}>: 前缀" if (USER_INJECT and USER_SPEAKER) else "不注入前缀（消息原样发送）"
    add_system(f"【用户身份】显示名={USER_SPEAKER or '(空)'}，{mode}")
    print(f"[用户身份] uid={USER_SPEAKER!r} inject={USER_INJECT}（{mode}）", flush=True)
    return {"ok": True, "user_speaker": USER_SPEAKER, "inject": USER_INJECT}


def set_member_profiles(profiles_str: str):
    """设置成员特征（前端填写，每行/每段 "成员名: 描述"，逗号或换行分隔）。"""
    global MEMBER_PROFILES
    merged = dict(MEMBER_PROFILES)
    for line in str(profiles_str or "").replace("\r", "\n").split("\n"):
        for seg in line.split(","):
            seg = seg.strip()
            if not seg:
                continue
            if ":" in seg:
                name, _, desc = seg.partition(":")
            elif "：" in seg:
                name, _, desc = seg.partition("：")
            else:
                continue
            name = name.strip()
            desc = desc.strip()
            if name and desc:
                merged[name] = desc
    if merged:
        MEMBER_PROFILES = merged
        save_state()
    return dict(MEMBER_PROFILES)


# ===================== 状态持久化（外部 JSON）=====================
# 在线成员 MEMBERS / 成员特征 MEMBER_PROFILES / 离场名单 LEFT_MEMBERS
# 不硬编码：首次启动生成 chat_state.json（用代码默认值），之后以文件为准，
# 运行时入场/离场/改特征实时保存；可直接手工编辑该文件，重启后生效。

def load_state():
    """启动时从 chat_state.json 加载成员状态；文件缺失/损坏则保留代码默认值。"""
    global MEMBERS, MEMBER_PROFILES, LEFT_MEMBERS
    data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            print(f"[状态加载] {STATE_FILE} 解析失败，使用代码默认值", flush=True)
    if isinstance(data.get("members"), list):
        MEMBERS = [str(m).strip() for m in data["members"] if str(m).strip()]
    if isinstance(data.get("profiles"), dict):
        MEMBER_PROFILES = {str(k).strip(): str(v) for k, v in data["profiles"].items() if str(k).strip()}
    if isinstance(data.get("left_members"), list):
        LEFT_MEMBERS = [str(m).strip() for m in data["left_members"] if str(m).strip()]


def save_state():
    """把成员状态写入 chat_state.json（临时文件 + 原子替换，避免写坏）。"""
    with lock:
        data = {
            "members": list(MEMBERS),
            "profiles": dict(MEMBER_PROFILES),
            "left_members": list(LEFT_MEMBERS),
        }
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[状态保存失败] {e}", flush=True)


def do_start(members_str: str = "", profiles_str: str = "", uid: str = "", inject: bool = True, session_suffix: str = ""):
    """启动：登录账号 + SSE 监听（群聊模式）。

    - members_str 已废弃（新机制用 add_member/remove_member 入场离场），保留兼容
    - profiles_str: 成员特征（设置面板填写）
    - uid/inject: 用户身份（设置面板配置），启动时应用
    - session_suffix: 剧本后缀 → session = grp_agents_chat_<suffix>，历史按后缀分文件；
      空 = 保留旧 session grp_agents_chat + chat_history.json
    """
    global started, pending_reply, pending_speaker, waiting, SESSION_SUFFIX
    if members_str:
        set_members(members_str)
    if profiles_str:
        set_member_profiles(profiles_str)
    # 应用用户身份（uid 空则保持默认 user；inject 按传入）
    set_user_identity(uid or "", inject)
    # 应用剧本后缀（只能启动前设置；运行中修改需重置）
    suffix = str(session_suffix or "").strip()
    with lock:
        SESSION_SUFFIX = suffix
    _stop_listeners()
    with lock:
        user_sessions[USER] = requests.Session()
        pending_reply = None
        pending_speaker = None
        waiting = False
        started = False
    try:
        login(USER)
    except Exception as e:
        with lock:
            started = False
        add_system(f"启动失败：{e}")
        set_status(f"启动失败：{e}", mode=None)
        return {"ok": False, "error": str(e)}

    with lock:
        started = True
        listener_stops[USER] = threading.Event()
    sess_id = current_session_id()
    hist_file = current_history_file()
    add_system(f"已登录 {USER}，群聊成员：{'、'.join(MEMBERS)}（session={sess_id}）")
    set_status("请输入首条消息，调度器将自动路由到合适的成员", mode="first")
    print(f"[历史记录] 保存到 {hist_file}")

    t = threading.Thread(target=sse_listener_thread, args=(USER,), daemon=True)
    with lock:
        listener_threads[USER] = t
    t.start()
    print("SSE 监听线程已启动")
    return {"ok": True, "members": list(MEMBERS), "session_id": sess_id, "session_suffix": SESSION_SUFFIX}


def do_confirm(content):
    """群聊输入处理：
    - 输入非空 → 用户插话 → 调度 LLM 选成员 → 发送 <user>: 内容
    - 输入为空 → 上轮回复 → 调度 LLM 选下一位成员（自动轮转）→ 转发 <上轮发言者>: 回复
    """
    global pending_reply, pending_speaker, waiting
    if not started:
        return {"ok": False, "error": "请先点击“开始”"}

    content = (content or "").strip()

    with lock:
        last_reply = pending_reply
        last_speaker = pending_speaker

    if content:
        # 用户插话：调度 LLM 选回应成员
        set_status("调度中：正在选择回应成员...", mode="waiting")
        target = pick_next_speaker(content, USER_SPEAKER, last_speaker or "")
        # 注入开关：开 → <id>: 内容；关 → 内容原样（模型按默认用户处理）
        if USER_INJECT and USER_SPEAKER:
            msg = f"<{USER_SPEAKER}>: {content}"
        else:
            msg = content
        result = send_msg(USER, msg, target)
        if not result.get("ok"):
            set_status(f"发送失败：{result.get('error') or result}", mode=None)
            return {"ok": False, "error": result.get("error") or result}
        record_message(USER_SPEAKER, content)
        add_msg(USER_SPEAKER, content)  # 用户插话内容也显示在屏幕上
        add_system(f"用户插话 → 路由到 {target}")
        with lock:
            pending_reply = None
            pending_speaker = None
            waiting = True
        set_status(f"已路由 {target}，等待回复...", mode="waiting")
        return {"ok": True, "action": "user", "target": target}

    # 空输入 → 自动轮转：把上轮回复路由给下一位
    if last_reply is None:
        return {"ok": False, "error": "暂无待转发的回复（先等成员回复，或输入插话）"}

    set_status("调度中：正在选择下一位发言成员...", mode="waiting")
    target = pick_next_speaker(last_reply, last_speaker or "", last_speaker or "")
    msg = f"<{last_speaker}>: {last_reply}"
    result = send_msg(USER, msg, target)
    if not result.get("ok"):
        set_status(f"转发失败：{result.get('error') or result}", mode=None)
        return {"ok": False, "error": result.get("error") or result}
    add_system(f"自动路由 {last_speaker} 的回复 → {target}")
    with lock:
        pending_reply = None
        pending_speaker = None
        waiting = True
    set_status(f"已路由 {target}，等待回复...", mode="waiting")
    return {"ok": True, "action": "route", "target": target}


def do_reset():
    global started, pending_reply, pending_speaker, waiting
    _stop_listeners()
    with lock:
        started = False
        pending_reply = None
        pending_speaker = None
        waiting = False
    add_system("已重置，可重新开始")
    set_status("未启动", mode=None)


def do_fabricate(name: str, present_roles=None):
    """编造「离开期间仅在场角色在场的随机事件」，以该离场角色口吻分享。

    - present_roles: 事件发生时在场的角色（用户发送时选择/输入）；空 → 只有目标角色自己
    - 复用系统正常调度：向该角色发消息（内容=提示词），由角色本人生成
    - session 隔离：fabricate_<agent>（非 grp 前缀），编造对话不进群聊共享历史
    - FABRICATE_TASKS 登记 task_id：回复被 handle_event 识别后插入群聊，不进入 pending_reply
    """
    global started
    if not started:
        return {"ok": False, "error": "请先点击“开始”"}
    name = str(name or "").strip()
    with lock:
        if name not in LEFT_MEMBERS:
            return {"ok": False, "error": f"{name} 不在离场成员中（当前离场：{'、'.join(LEFT_MEMBERS) or '无'}）"}
    # 在场角色：用户指定的（事件发生时在场的角色）；空 → 只有目标角色自己
    # 注意：不强制剔除被选中角色——前端候选已含离场成员，用户可选主角加入；
    # 用户没选则不自动包含（离开期间主角默认不在场）。
    present = []
    for p in (present_roles or []):
        p = str(p).strip()
        if p and p not in present:
            present.append(p)
    present_text = "、".join(present)
    if present:
        # 直接替换【在场角色】占位符
        prompt = (
            f"【系统信息】现需要你直接编造一个在离开期间仅{present_text}在场的随机事件，"
            "并以角色的口吻将其分享。"
        )
    else:
        prompt = (
            "【系统信息】现需要你直接编造一个在离开期间仅你自己在场的随机事件，"
            "并以角色的口吻将其分享。"
        )
    # 隔离 session：fabricate_<agent>（不带 grp 前缀 → 不入群聊共享历史）
    fabricate_session = f"fabricate_{name}"
    set_status(f"编造中：{name} 离开期间的事件...", mode="waiting")
    result = send_msg(USER, prompt, name, session_id=fabricate_session)
    if not result.get("ok"):
        set_status(f"编造请求失败：{result.get('error') or result}", mode=None)
        return {"ok": False, "error": result.get("error") or result}
    task_id = result.get("task_id") or ""
    if task_id:
        with lock:
            FABRICATE_TASKS[task_id] = name
    print(f"[编造] {name} 请求已发送（session={fabricate_session}，task={task_id}，在场={present_text or '仅自己'}），等待回复后插入", flush=True)
    return {"ok": True, "agent": name, "session": fabricate_session, "task_id": task_id}


# ===================== HTTP 服务 =====================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True
        except OSError:
            self.close_connection = True

    def _send_bytes(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", code)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_FILE, "rb") as f:
                    self._send_bytes(f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send_json({"ok": False, "error": "缺少 index.html"}, 500)
        elif path == "/api/history":
            self._send_json({
                "history": load_history(),
                "members": MEMBERS,
                "left_members": LEFT_MEMBERS,
                "profiles": MEMBER_PROFILES,
                "user_speaker": USER_SPEAKER,
                "user_inject": USER_INJECT,
                "session_suffix": SESSION_SUFFIX,
                "session_id": current_session_id(),
                "fabricate_drafts": FABRICATE_DRAFTS,
            })
        elif path == "/api/events":
            self._sse()
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        if path == "/api/start":
            self._send_json(do_start(
                data.get("members") or "",
                data.get("profiles") or "",
                data.get("uid") or "",
                bool(data.get("inject", True)),
                data.get("session_suffix") or "",
            ))
        elif path == "/api/confirm":
            self._send_json(do_confirm(data.get("content") or ""))
        elif path == "/api/reset":
            do_reset()
            self._send_json({"ok": True})
        elif path == "/api/member/add":
            ok, members = add_member(data.get("name") or "", data.get("profile") or "")
            self._send_json({"ok": ok.get("ok"), "error": ok.get("error"), "members": members})
        elif path == "/api/member/remove":
            ok, members = remove_member(data.get("name") or "")
            self._send_json({"ok": ok.get("ok"), "error": ok.get("error"), "members": members})
        elif path == "/api/fabricate":
            self._send_json(do_fabricate(
                data.get("name") or "",
                data.get("present") or [],
            ))
        elif path == "/api/user_identity":
            self._send_json(set_user_identity(
                data.get("uid") or "",
                bool(data.get("inject", True)),
            ))
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue(maxsize=2000)
        with lock:
            browser_queues.append(q)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while running:
                try:
                    ev = q.get(timeout=15)
                    payload = ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with lock:
                if q in browser_queues:
                    browser_queues.remove(q)


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    # 加载外部状态（在线成员/成员特征/离场名单）：首次启动生成 chat_state.json，
    # 之后以文件为准；可直接编辑文件，重启生效
    load_state()
    save_state()
    print("=" * 50)
    print("群聊版多智能体对话调度器 —— 手机比例 Web 前端")
    print(f"群聊成员：{'、'.join(MEMBERS)}（状态文件：{STATE_FILE}）")
    print(f"调度 LLM：{SCHEDULER_LLM['model']} @ {SCHEDULER_LLM['api_url']}")
    print("要求：gateway-backend-service 网关已运行于 127.0.0.1:8080")
    print("=" * 50)
    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    lan = get_lan_ip()
    print(f"本机访问:   http://127.0.0.1:{WEB_PORT}")
    print(f"手机访问:   http://{lan}:{WEB_PORT}   (需与电脑同一局域网)")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
