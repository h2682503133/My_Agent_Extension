import asyncio
import websockets
import requests
import json
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

# ===================== 配置 =====================
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 5210
USER_A = "agent_a"
USER_B = "agent_b"

user_sessions = {
    USER_A: requests.Session(),
    USER_B: requests.Session()
}
reply_buf = {
    USER_A: None,
    USER_B: None
}
running = True
current_wait = USER_A
target_user = USER_B
first_round = True

root = None
txt_box = None
entry = None
status_text = None
# ================================================

def login(uid: str):
    sess = user_sessions[uid]
    resp = sess.post(
        f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/do_login",
        data={"user_id": uid},
        allow_redirects=False
    )
    if resp.status_code not in (200, 302):
        raise Exception(f"{uid} 登录失败，网关未就绪")
    print(f"[登录成功] {uid}")

def send_msg(uid: str, content: str):
    sess = user_sessions[uid]
    resp = sess.post(
        f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/chat",
        headers={"Content-Type": "application/json"},
        json={"msg": content}
    )
    data = resp.json()
    print(f"[发送] {uid} -> {content}  返回:{data}")
    return data

def add_log(sender: str, text: str):
    def _inner():
        txt_box.config(state=tk.NORMAL)
        txt_box.insert(tk.END, f"\n————————————\n【{sender}】\n{text}\n")
        txt_box.see(tk.END)
        txt_box.config(state=tk.DISABLED)
    root.after(0, _inner)

def set_status(msg: str):
    def _inner():
        status_text.config(text=msg)
    root.after(0, _inner)

# ===================== 独立子线程 WS 监听（修复header传参错误） =====================
def ws_listener_thread(uid: str):
    sess = user_sessions[uid]
    cookies = sess.cookies.get_dict()
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    ws_addr = f"ws://{GATEWAY_HOST}:{GATEWAY_PORT}/ws"
    # 直接构造header列表，不再传函数
    header_list = [("Cookie", cookie_str)]

    while running:
        try:
            print(f"[{uid}] 正在尝试连接WebSocket")
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()

            async def ws_loop():
                # 直接传入 header 列表，兼容所有 websockets 版本
                async with websockets.connect(
                    ws_addr,
                    additional_headers=header_list
                ) as ws:
                    print(f"[{uid}] WebSocket 连接成功")
                    while running:
                        raw = await ws.recv()
                        print(f"[{uid}] 原始报文: {raw}")
                        obj = json.loads(raw)
                        if "text" in obj and obj["text"].strip():
                            reply_buf[uid] = obj["text"]
                            add_log(f"{uid} 回复", obj["text"])
                            set_status(f"已收到 {uid} 回复，可修改转发或回车直发")

            loop.run_until_complete(ws_loop())
        except Exception as e:
            print(f"[{uid}] WS异常: {e}, 5秒重连")
            time.sleep(5)

def on_confirm():
    global current_wait, target_user, first_round
    input_txt = entry.get().strip()
    entry.delete(0, tk.END)

    if first_round:
        if not input_txt:
            set_status("首轮不能为空，请输入起始消息")
            return
        send_msg(USER_A, input_txt)
        add_log("用户首轮发起", input_txt)
        first_round = False
        set_status(f"等待 {current_wait} 回复中...")
        return

    raw_reply = reply_buf[current_wait]
    if raw_reply is None:
        set_status(f"暂无 {current_wait} 待转发内容，等待回复")
        return

    use_txt = input_txt if input_txt else raw_reply
    send_msg(target_user, use_txt)
    add_log(f"转发至 {target_user}", use_txt)

    reply_buf[current_wait] = None
    current_wait, target_user = target_user, current_wait
    set_status(f"等待 {current_wait} 回复中...")

def build_gui():
    global root, txt_box, entry, status_text
    win = tk.Tk()
    win.title("双线程版双智能体对话调度器")
    win.geometry("900x650")

    txt_box = scrolledtext.ScrolledText(win, wrap=tk.WORD, state=tk.DISABLED, font=("微软雅黑",10))
    txt_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

    status_text = ttk.Label(win, text="初始化...")
    status_text.pack(anchor="w", padx=8)

    frame_bottom = ttk.Frame(win)
    frame_bottom.pack(fill=tk.X, padx=8, pady=6)
    entry = ttk.Entry(frame_bottom, font=("微软雅黑",11))
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    entry.bind("<Return>", lambda e: on_confirm())
    btn = ttk.Button(frame_bottom, text="确认转发", command=on_confirm)
    btn.pack(side=tk.RIGHT, padx=(6,0))

    def close_win():
        global running
        running = False
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close_win)
    root = win

def main():
    login(USER_A)
    login(USER_B)

    build_gui()
    add_log("系统提示", f"已登录 {USER_A}、{USER_B}，请输入首轮消息开启对话")
    set_status("请输入首轮起始消息，点击确认转发")

    # 两个独立子线程分别监听A、B
    t_a = threading.Thread(target=ws_listener_thread, args=(USER_A,), daemon=True)
    t_b = threading.Thread(target=ws_listener_thread, args=(USER_B,), daemon=True)
    t_a.start()
    t_b.start()
    print("两个WebSocket监听子线程已启动")

    root.mainloop()

if __name__ == "__main__":
    main()