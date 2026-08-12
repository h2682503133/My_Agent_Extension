# -*- coding: utf-8 -*-
"""
LLBot 群聊监控（独立工具，依附 My_Agent_MSA 的 LLBot/Satori 网关）

功能1：双击 start.bat 启动 LLBot（支持多个 QQ 账号同时运行）。
  - 账号数量由 config.json 的 account_count 控制，端口从 base 端口自动递增；
  - 所有账号共用一个 llbot.exe 和 qq.exe（工具自己的 LLBot 副本，与 MSA 项目实例隔离）；
  - 首次启动要求输入并保存路径，之后启动显示已存路径，可输入修改，直接回车使用存储值。

功能2：群聊监控（仅群聊、仅被 @ 机器人时处理）。
  - 读取 config.json 中 monitored（群号 -> [q号, ...]）；
  - 触发条件为"或"：监控列表内的用户（不 @ 也会处理）或 明确 @ 机器人（@全体不算）；
  - 命中后不发给模型，而是通过对应账号的机器人号私聊转发给 config.json 中 target_qq 指定的 QQ 号；
  - 多个账号各自连接自己的 Satori 网关，并发监控。

依赖：websockets、requests（与 Tools/agents'chat 相同）。
"""
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import requests
import websockets

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "llbot_path": "",       # 工具自己的 llbot.exe 完整路径（所有账号共用）
    "qq_path": "",          # QQ.exe 完整路径（所有账号共用）
    "account_count": 1,     # 账号数量，端口从 base 端口依次递增
    "base_pmhq_port": 13001,   # 账号1 的 PMHQ 端口，账号N = base + (N-1)
    "base_webui_port": 3081,   # 账号1 的 WebUI 端口
    "base_satori_port": 5601,  # 账号1 的 Satori 端口
    "satori_token": "",     # Satori 鉴权 token（本地一般为空）
    "headless": False,      # 是否无头模式启动 QQ（多开 QQ 窗口冲突时开启）
    "target_qq": "",        # 命中消息转发私聊给该 QQ 号
    "monitored": {},        # 群号 -> [q号, ...]
}


# ===================== 配置读写 =====================

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as e:
            print(f"[配置] config.json 解析失败：{e}，将按默认配置重建")

    # 旧版 accounts 列表迁移：取第一个账号的路径与端口作为共享配置
    accounts = cfg.get("accounts")
    if accounts and not cfg.get("llbot_path"):
        accs = [a for a in accounts if isinstance(a, dict)]
        if accs:
            first = accs[0]
            cfg["llbot_path"] = first.get("llbot_path", "")
            cfg["qq_path"] = first.get("qq_path", "")
            cfg["account_count"] = len(accs)
            cfg["base_pmhq_port"] = int(first.get("pmhq_port") or DEFAULT_CONFIG["base_pmhq_port"])
            cfg["base_webui_port"] = int(first.get("webui_port") or DEFAULT_CONFIG["base_webui_port"])
            cfg["base_satori_port"] = int(first.get("satori_port") or DEFAULT_CONFIG["base_satori_port"])
            cfg["headless"] = bool(first.get("headless", False))
    return cfg


def save_config(cfg: dict):
    data = dict(cfg)
    data.pop("accounts", None)  # 旧字段，已迁移
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[配置] 已保存到 {CONFIG_PATH}")


def build_accounts(cfg: dict) -> list:
    """按 account_count 生成账号列表，端口从 base 端口依次递增。"""
    try:
        count = max(1, int(cfg.get("account_count") or 1))
    except (TypeError, ValueError):
        count = 1
    accounts = []
    for i in range(count):
        accounts.append({
            "name": f"账号{i + 1}",
            "llbot_path": cfg.get("llbot_path", ""),
            "qq_path": cfg.get("qq_path", ""),
            "pmhq_port": int(cfg.get("base_pmhq_port", 13001)) + i,
            "webui_port": int(cfg.get("base_webui_port", 3081)) + i,
            "satori_port": int(cfg.get("base_satori_port", 5601)) + i,
            "satori_token": cfg.get("satori_token", ""),
            "bot_qq": "",
            "headless": cfg.get("headless", False),
        })
    return accounts


def ensure_path(title: str, key: str, cfg: dict):
    """读取/输入并保存一个路径配置。已有值显示后可修改，直接回车用存储值。"""
    current = str(cfg.get(key, "") or "").strip()
    if current:
        print(f"[配置] 当前 {title}：{current}")
        val = input("        输入新路径可修改，直接回车使用存储值：").strip()
        if val:
            cfg[key] = val
    else:
        val = input(f"[配置] 请输入 {title}：").strip()
        if val:
            cfg[key] = val
        else:
            print(f"[配置] 未填写 {title}，可在 config.json 中手动补充")


# ===================== 功能1：启动 LLBot =====================

def launch_llbot(acct: dict) -> bool:
    name = acct.get("name") or "账号"
    llbot = str(acct.get("llbot_path", "") or "").strip()
    if not llbot:
        print(f"[LLBot {name}] 未配置 llbot.exe 路径，跳过启动")
        return False
    if not os.path.exists(llbot):
        print(f"[LLBot {name}] llbot.exe 不存在：{llbot}")
        return False

    cmd = [llbot]
    qq_path = str(acct.get("qq_path", "") or "").strip()
    if qq_path:
        if not os.path.exists(qq_path):
            print(f"[LLBot {name}] 警告：QQ.exe 不存在：{qq_path}，仍按原参数启动")
        cmd += ["--qq-path", qq_path]

    if acct.get("pmhq_port"):
        cmd.append(f"--pmhq-port={acct['pmhq_port']}")

    if acct.get("headless"):
        cmd.append("--headless")

    print(f"[LLBot {name}] 启动命令：" + " ".join(cmd))
    subprocess.Popen(cmd, cwd=str(Path(llbot).parent))
    print(f"[LLBot {name}] 已启动，请在 LLBot 窗口确认登录状态")
    return True


# ===================== 功能2：Satori 群聊监控 =====================

AT_TAG_RE = re.compile(r"<at\s[^>]*/>|<at\s[^>]*>.*?</at>", re.S)
AT_ID_RE = re.compile(r'<at\s[^>]*?id="([^"]*)"')


def strip_at(content: str) -> str:
    """去掉所有 @ 元素（含 @全体），返回剩余文本。"""
    return AT_TAG_RE.sub("", content).strip()


def at_targets(content: str) -> list:
    """返回消息中所有 @ 元素的 id 列表。"""
    return AT_ID_RE.findall(content)


def match_monitored(cfg: dict, group_id: str, user_id: str) -> bool:
    """判断 (群号, q号) 是否在 config 的被监控列表中。"""
    monitored = cfg.get("monitored") or {}
    users = monitored.get(str(group_id)) or []
    return str(user_id) in [str(u) for u in users]


def send_private_message(acct: dict, bot_info: dict, user_id: str, content: str):
    """通过该账号的机器人号给指定 QQ 号发私聊消息（Satori HTTP API）。"""
    base = f"http://127.0.0.1:{acct.get('satori_port', 5601)}/v1"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {acct.get('satori_token', '') or ''}",
        "X-Platform": bot_info.get("platform", ""),
        "X-Self-ID": bot_info.get("self_id", ""),
        "Satori-Platform": bot_info.get("platform", ""),
        "Satori-User-ID": bot_info.get("self_id", ""),
    }
    # 1. 获取/创建与目标的私聊频道
    resp = requests.post(
        f"{base}/user.channel.create",
        json={"user_id": str(user_id)},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    channel_id = resp.json()["id"]
    # 2. 发送消息
    resp = requests.post(
        f"{base}/message.create",
        json={"channel_id": channel_id, "content": content},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()


async def handle_event(cfg: dict, acct: dict, bot_info: dict, body: dict):
    if body.get("type") != "message-created":
        return

    channel = body.get("channel") or {}
    guild = body.get("guild") or {}
    user = body.get("user") or {}
    message = body.get("message") or {}
    content = message.get("content") or ""

    # 仅群聊（Satori 私聊 channel.type == 1）
    if channel.get("type") == 1:
        return

    group_id = str(guild.get("id") or channel.get("id") or "")
    user_id = str(user.get("id") or "")
    if not group_id or not user_id:
        return

    name = acct.get("name") or "账号"
    bot_qq = str(bot_info.get("self_id", "") or "")
    if user_id == bot_qq:
        return  # 机器人自己发的消息

    # 触发条件（或）：监控列表内的用户 或 明确 @ 机器人（@全体不算）
    at_bot = bool(bot_qq) and bot_qq in at_targets(content)
    if not match_monitored(cfg, group_id, user_id) and not at_bot:
        print(f"[忽略] 群 {group_id} 用户 {user_id} 未@机器人且不在监控列表")
        return

    text = strip_at(content)
    if not text:
        return

    sender_name = user.get("name") or user.get("nick") or user_id
    print(f"[命中] 群 {group_id} 用户 {sender_name}({user_id})：{text[:80]}")

    target = str(cfg.get("target_qq", "") or "").strip()
    if not target:
        print("[警告] config.json 未配置 target_qq，无法转发")
        return

    forward = (
        f"【群聊监控转发 · {name}】\n"
        f"群号：{group_id}\n"
        f"发送者：{sender_name} ({user_id})\n"
        f"内容：{text}"
    )
    try:
        send_private_message(acct, bot_info, target, forward)
        print(f"[转发] 已通过 {name} 私聊发送给 {target}")
    except Exception as e:
        print(f"[转发失败] {e}")


async def monitor(cfg: dict, acct: dict):
    name = acct.get("name") or "账号"
    bot_info = {"self_id": "", "platform": ""}
    # LLBot 的 Satori WS 只接受 /v1/events 路径（其他路径会 1008 invalid address）
    url = f"ws://127.0.0.1:{acct.get('satori_port', 5601)}/v1/events"

    while True:
        try:
            print(f"[监控 {name}] 连接 {url} ...")
            async with websockets.connect(url) as ws:
                # Satori identify 鉴权
                await ws.send(json.dumps({
                    "op": 3,
                    "body": {"token": acct.get("satori_token", "") or ""},
                }))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    op = data.get("op")
                    body = data.get("body") or {}
                    if op == 4:  # READY
                        for login in body.get("logins") or []:
                            login_user = login.get("user") or {}
                            if login_user.get("id"):
                                bot_info["self_id"] = str(login_user["id"])
                                bot_info["platform"] = login.get("platform", "")
                        if acct.get("bot_qq"):
                            bot_info["self_id"] = str(acct["bot_qq"])
                        print(
                            f"[监控 {name}] 已连接，机器人账号：{bot_info['self_id']} "
                            f"({bot_info['platform']})"
                        )
                    elif op == 0:  # EVENT
                        await handle_event(cfg, acct, bot_info, body)
                    # op 1=PING 2=PONG 5=META 忽略
        except Exception as e:
            print(f"[监控 {name}] 连接异常：{e}，5 秒后重连")
            await asyncio.sleep(5)


# ===================== 入口 =====================

def main():
    print("=" * 46)
    print("LLBot 群聊监控（多账号）")
    print("=" * 46)

    cfg = load_config()
    count = cfg.get("account_count") or 1

    # 功能1：配置并启动 LLBot
    print("\n---- 功能1：LLBot 启动配置 ----")
    ensure_path("llbot.exe 路径（所有账号共用）", "llbot_path", cfg)
    ensure_path("QQ.exe 路径（所有账号共用）", "qq_path", cfg)
    save_config(cfg)

    accounts = build_accounts(cfg)
    print(f"\n账号数量：{len(accounts)}，端口从 base 依次递增：")
    for acct in accounts:
        print(
            f"  {acct['name']}: PMHQ {acct['pmhq_port']} / "
            f"WebUI {acct['webui_port']} / Satori {acct['satori_port']}"
        )
    for acct in accounts:
        launch_llbot(acct)

    # 功能2：群聊监控
    print("\n---- 功能2：群聊监控 ----")
    if not cfg.get("target_qq"):
        print("[提示] config.json 尚未配置 target_qq，命中消息将无法转发")
    if not cfg.get("monitored"):
        print("[提示] config.json 的 monitored 为空，请按 群号 -> [q号] 填写后重启")

    async def run_all():
        await asyncio.gather(*(monitor(cfg, acct) for acct in accounts))

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
