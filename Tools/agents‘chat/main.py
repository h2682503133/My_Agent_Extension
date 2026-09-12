# -*- coding: utf-8 -*-
"""
群聊版多智能体对话调度器 —— Tkinter 桌面版
====================================================
与 web_server.py 群聊版逻辑一致：
- 群聊成员任意多（MEMBERS）
- 用户可随时插话，调度 LLM 自动路由
- 空输入 → 自动路由上轮回复给下一位成员
- 消息带 <说话者id>: 前缀（避免 USER.md 固有身份认知干扰）
- 聊天记录注入调度提示词，由调度 LLM（默认 ollama llama3.1:8b）决策
"""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

import requests

# ===================== 配置 =====================
# 网关入口：直接用 ingress 的 80 端口（不依赖 kubectl port-forward）
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = "80"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats", "chat_history.json")

USER = "agent_e"
AGENT_DEFAULT = "main"
MEMBERS = ["lilith", "alice", "luna"]
SESSION_ID = "grp_agents_chat"
USER_SPEAKER = "user"

SCHEDULER_LLM = {
    "api_url": "http://127.0.0.1:11434/api/chat",
    "model": "llama3.1:8b",
    "temperature": 1.4,
    "options": {"num_ctx": 8192, "num_predict": 128},
    "timeout": 60,
}
ROUTE_HISTORY_LIMIT = 12
# ================================================

running = True
started = False
sess = None
pending_reply = None
pending_speaker = None
history_lock = threading.Lock()
# 已接受过首条回复的 task_id：同一次请求只保留第一条用户可见回复，
# 其余（工具轮次的中间/收尾消息）全部丢弃，避免内容被后一条顶掉。
FIRST_REPLY_TASKS = []
REPLY_EVENT_TYPES = ("assistant_message", "task_waiting_user")

root = None
txt_box = None
entry = None
status_text = None
btn_start = None
members_label = None


# ===================== 历史记录 =====================
def load_history():
    with history_lock:
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []


def save_history(history):
    with history_lock:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)


def record_message(speaker, text):
    history = load_history()
    history.append(f"{speaker}: {text}")
    if len(history) > 500:
        history = history[-500:]
    save_history(history)


# ===================== 调度 LLM =====================
def call_scheduler_llm(messages):
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


def pick_next_speaker(current_text, current_speaker, last_speaker=""):
    history = load_history()[-ROUTE_HISTORY_LIMIT:]
    members_str = "、".join(MEMBERS)
    if current_speaker == USER_SPEAKER:
        scene = f"用户（{USER_SPEAKER}）刚刚插话：{current_text[:200]}"
    else:
        scene = f"成员 {current_speaker} 刚刚说完：{current_text[:200]}"

    system_prompt = (
        "你是群聊调度器。当前群聊在场成员（智能体）只有这些："
        f"{members_str}\n"
        "规则：\n"
        "1. 根据群聊历史和当前消息，选择下一位最应该发言的成员\n"
        "2. 只输出一个成员名（agent_id），不要输出任何其他内容\n"
        f"3. 不要选择刚发言过的成员（{last_speaker}），除非场景明确需要\n"
        "4. 用户插话时，选择最应该回应这条消息的成员"
    )
    history_text = "\n".join(f"- {h}" for h in history) if history else "（暂无历史）"
    user_prompt = f"群聊历史：\n{history_text}\n\n当前：{scene}\n\n下一位发言成员："

    out = call_scheduler_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    for ch in "[]{}<>:：,，、 \n\t\"'":
        out = out.replace(ch, "")
    candidate = ""
    for m in MEMBERS:
        if m in out:
            candidate = m
            break
    if candidate and candidate != last_speaker:
        return candidate
    if last_speaker and last_speaker in MEMBERS:
        idx = MEMBERS.index(last_speaker)
        return MEMBERS[(idx + 1) % len(MEMBERS)]
    return MEMBERS[0]


# ===================== 网关交互 =====================
def login(uid):
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


def send_msg(uid, content, agent_id):
    try:
        resp = sess.post(
            f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/messages",
            json={
                "user_id": uid,
                "content": content,
                "agent_id": agent_id,
                "session_id": SESSION_ID,
            },
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        print(f"[发送失败] {uid}: {e}")
        return {"ok": False, "error": str(e)}
    print(f"[发送] {uid} -> {agent_id} : {content[:60]}...  返回:{data}")
    return data


# ===================== GUI =====================
def add_log(sender, text):
    def _inner():
        txt_box.config(state=tk.NORMAL)
        txt_box.insert(tk.END, f"\n————————————\n【{sender}】\n{text}\n")
        txt_box.see(tk.END)
        txt_box.config(state=tk.DISABLED)
    root.after(0, _inner)


def set_status(msg):
    def _inner():
        status_text.config(text=msg)
    root.after(0, _inner)


# ===================== SSE 事件流监听 =====================
def _accept_reply_event(obj) -> bool:
    """同一次请求(task)只放行第一条用户可见回复，其余丢弃。

    智能体调用工具时同一 task 会产生多条 assistant_message
    （第 1 条是完整角色回复，后续是工具收尾），若全部接受会互相顶掉。
    """
    if obj.get("type") not in REPLY_EVENT_TYPES:
        return True
    meta = obj.get("metadata") or {}
    if str(meta.get("visible_to_user", "true")).strip().lower() == "false":
        return False
    task_id = obj.get("task_id") or ""
    if not task_id:
        return True
    with history_lock:
        if task_id in FIRST_REPLY_TASKS:
            return False
        FIRST_REPLY_TASKS.append(task_id)
        if len(FIRST_REPLY_TASKS) > 500:
            del FIRST_REPLY_TASKS[:len(FIRST_REPLY_TASKS) - 500]
    return True


def handle_event(obj):
    global pending_reply, pending_speaker
    etype = obj.get("type", "")
    text = (obj.get("text") or "").strip()
    error = (obj.get("error") or "").strip()

    if etype == "task_failed":
        add_log("任务失败", error or text or "未知错误")
        set_status(f"任务失败：{error or text}")
        return
    if not text:
        return
    if not _accept_reply_event(obj):
        print(f"[忽略] 同一 task 的后续消息已丢弃 task={obj.get('task_id')} type={etype}")
        return

    # 说话者：scheduler 给 assistant_message 文本加了 "agent_id: " 前缀（无尖括号）；
    # metadata.agent_id 兜底
    speaker = ""
    md_agent = (obj.get("metadata") or {}).get("agent_id", "") or ""
    if md_agent and md_agent in MEMBERS:
        speaker = md_agent
        if text.startswith(md_agent + ":") or text.startswith(md_agent + "："):
            rest = text.split(":", 1)[1].strip() if ":" in text[:20] else text.split("：", 1)[1].strip()
            text = rest
    else:
        for m in MEMBERS:
            if text.startswith(m + ":") or text.startswith(m + "："):
                speaker = m
                text = text[len(m) + 1:].strip()
                break
    if not speaker:
        speaker = AGENT_DEFAULT
    with history_lock:
        pending_reply = text
        pending_speaker = speaker
    add_log(f"{speaker} 回复", text)
    record_message(speaker, text)
    set_status(f"收到 {speaker} 回复 · 空输入=自动路由 / 输入=插话")


def sse_listener_thread():
    global running
    # 不过滤 agent：群聊中回复来自任意成员，收该 user 的全部事件
    url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/events?user_id={USER}"
    while running:
        try:
            print(f"[{USER}] 正在连接事件流 (SSE)")
            with sess.get(url, stream=True, timeout=(10, 90)) as resp:
                print(f"[{USER}] 事件流连接成功")
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not running:
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
            if running:
                print(f"[{USER}] SSE异常: {e}, 5秒重连")
                time.sleep(5)


# ===================== 启动 / 输入逻辑 =====================
def on_start():
    global started, sess
    if started:
        return
    try:
        sess = requests.Session()
        login(USER)
    except Exception as e:
        set_status(f"启动失败：{e}")
        add_log("系统提示", f"启动失败：{e}")
        return

    started = True
    btn_start.config(state=tk.DISABLED)
    members_label.config(text=f"群聊成员：{'、'.join(MEMBERS)}")
    add_log("系统提示", f"已登录 {USER}，群聊成员：{'、'.join(MEMBERS)}（session={SESSION_ID}）")
    set_status("请输入首条消息，调度器自动路由")

    t = threading.Thread(target=sse_listener_thread, daemon=True)
    t.start()
    print(f"[历史记录] 保存到 {HISTORY_FILE}")


def on_confirm():
    global pending_reply, pending_speaker
    if not started:
        set_status("请先点击“开始群聊”")
        return

    input_txt = entry.get().strip()
    entry.delete(0, tk.END)

    with history_lock:
        last_reply = pending_reply
        last_speaker = pending_speaker

    if input_txt:
        # 用户插话 → 调度路由
        set_status("调度中：选择回应成员...")
        target = pick_next_speaker(input_txt, USER_SPEAKER, last_speaker or "")
        result = send_msg(USER, f"<{USER_SPEAKER}>: {input_txt}", target)
        if not result.get("ok"):
            set_status(f"发送失败：{result.get('error') or result}")
            return
        record_message(USER_SPEAKER, input_txt)
        add_log(USER_SPEAKER, input_txt)  # 用户插话内容也显示在屏幕上
        add_log("用户插话", f"→ 路由到 {target}")
        with history_lock:
            pending_reply = None
            pending_speaker = None
        set_status(f"已路由 {target}，等待回复...")
        return

    # 空输入 → 自动路由上轮回复
    if last_reply is None:
        set_status("暂无待转发回复（先等成员回复，或输入插话）")
        return

    set_status("调度中：选择下一位成员...")
    target = pick_next_speaker(last_reply, last_speaker or "", last_speaker or "")
    result = send_msg(USER, f"<{last_speaker}>: {last_reply}", target)
    if not result.get("ok"):
        set_status(f"转发失败：{result.get('error') or result}")
        return
    add_log("自动路由", f"{last_speaker} 的回复 → {target}")
    with history_lock:
        pending_reply = None
        pending_speaker = None
    set_status(f"已路由 {target}，等待回复...")


def build_gui():
    global root, txt_box, entry, status_text, btn_start, members_label
    win = tk.Tk()
    win.title("群聊版多智能体对话调度器")
    win.geometry("900x700")

    txt_box = scrolledtext.ScrolledText(win, wrap=tk.WORD, state=tk.DISABLED, font=("微软雅黑", 10))
    txt_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

    status_text = ttk.Label(win, text="初始化...")
    status_text.pack(anchor="w", padx=8)

    frame_set = ttk.Frame(win)
    frame_set.pack(fill=tk.X, padx=8, pady=4)
    members_label = ttk.Label(frame_set, text=f"群聊成员：{'、'.join(MEMBERS)}")
    members_label.pack(side=tk.LEFT)
    btn_start = ttk.Button(frame_set, text="开始群聊", command=on_start)
    btn_start.pack(side=tk.LEFT, padx=(12, 0))

    frame_bottom = ttk.Frame(win)
    frame_bottom.pack(fill=tk.X, padx=8, pady=6)
    entry = ttk.Entry(frame_bottom, font=("微软雅黑", 11))
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    entry.bind("<Return>", lambda e: on_confirm())
    btn = ttk.Button(frame_bottom, text="发送（空=自动路由）", command=on_confirm)
    btn.pack(side=tk.RIGHT, padx=(6, 0))

    def close_win():
        global running
        running = False
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close_win)
    root = win


def main():
    build_gui()
    add_log("系统提示", "群聊版多智能体调度器：输入=用户插话（自动路由），空输入=自动路由下一位成员")
    set_status("点击“开始群聊”")
    root.mainloop()


if __name__ == "__main__":
    main()
