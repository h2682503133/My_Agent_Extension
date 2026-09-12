# -*- coding: utf-8 -*-
"""ToolsHub —— 工具聚合与 MSA 中间对接服务。

职责
  1. 聚合 Tools/ 下的独立工具：统一启动/停止/状态/日志（路径全部来自配置，不硬编码）
  2. 作为工具与 My_Agent_MSA 的中间对接层（同构代理，工具侧零改动）
  3. 扣留待审：工具上报的消息可被扣住，由人在网页上决定「上报」或「删除」，并可自行加料
  4. 网页前端：左边栏工具列表 + 主区面板（配置 / 日志 / 打开工具 / 待审队列）

新增工具接入：往 adapters/ 丢一个 .py（复制 _template.py），无需改本文件。

用法：python hub_server.py   （或双击 start.bat）
"""

import base64
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapters as adapter_registry          # noqa: E402
from msa.bridge import Bridge                # noqa: E402
from msa.chat import ChatStore, ChatSession  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "hub_config.json"
INDEX_FILE = BASE_DIR / "index.html"
PAGES_DIR = BASE_DIR / "pages"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

DEFAULT_CONFIG = {
    "hub": {"host": "0.0.0.0", "port": 8077, "tools_root": ""},
    "msa": {"gateway_url": "http://127.0.0.1:8080", "default_user_id": "", "default_agent_id": "main"},
    "tools": {},
}

lock = threading.Lock()
_conda_envs_cache = None


# ===================== 基础工具 =====================
def port_open(port, host="127.0.0.1", timeout=0.6) -> bool:
    if not port:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def conda_env_python(env_name: str) -> str:
    """解析 conda 环境的 python 绝对路径（避免 conda run 的 stdout 编码问题）。"""
    global _conda_envs_cache
    name = (env_name or "").strip()
    if not name:
        return "python"
    if _conda_envs_cache is None:
        envs = {}
        try:
            out = subprocess.run(["conda", "env", "list", "--json"],
                                 capture_output=True, text=True, timeout=30)
            data = json.loads(out.stdout or "{}")
            for p in data.get("envs", []):
                envs[Path(p).name] = p
        except Exception:
            pass
        # 兜底：常见环境根目录
        for root in (Path(r"D:\DuanKou\tools\Anacondaenvs"),
                     Path(os.path.expanduser("~")) / "anaconda3" / "envs",
                     Path(r"C:\Users\Asus\anaconda3\envs")):
            if root.is_dir():
                for d in root.iterdir():
                    if d.is_dir() and (d / "python.exe").exists():
                        envs.setdefault(d.name, str(d))
        _conda_envs_cache = envs
    prefix = _conda_envs_cache.get(name)
    if prefix:
        py = Path(prefix) / "python.exe"
        if py.exists():
            return str(py)
    return "python"


# ===================== 工具运行态 =====================
class Runtime:
    def __init__(self, tool_id: str):
        self.tool_id = tool_id
        self.proc: subprocess.Popen | None = None
        self.started_at: float = 0.0
        self.last_error = ""
        self.log_ring: deque = deque(maxlen=3000)
        self.stdin_pipe = None

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"{self.tool_id}.log"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def append_log(self, line: str):
        self.log_ring.append(line)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def tail(self, n: int = 300) -> list[str]:
        return list(self.log_ring)[-n:]


# ===================== Hub 主体 =====================
class Hub:
    def __init__(self):
        self.base_dir = BASE_DIR
        self._cfg = self._load_config()
        self.runtimes: dict[str, Runtime] = {}
        self.clients: list[queue.Queue] = []
        self._chat_stores: dict[str, ChatStore] = {}
        self._chat_sessions: dict[str, ChatSession] = {}
        self.bridge = Bridge(self)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # ── 聊天会话 ────────────────────────────────────────
    def chat_store(self, tool_id: str) -> ChatStore:
        with lock:
            st = self._chat_stores.get(tool_id)
            if st is None:
                st = ChatStore(self, tool_id)
                self._chat_stores[tool_id] = st
            return st

    def start_chat_sessions(self):
        """为「已启用 + 接 MSA」的工具启动 agent 回复订阅。"""
        for tid, cls in adapter_registry.all_adapters().items():
            if getattr(cls, "bridge_mode", "none") != "proxy":
                continue
            cfg = self.raw_tool_cfg(tid)
            if not cfg.get("enabled", True):
                continue
            with lock:
                s = self._chat_sessions.get(tid)
                if s is None:
                    s = ChatSession(self, tid)
                    self._chat_sessions[tid] = s
            s.start()

    # ── 配置 ────────────────────────────────────────────
    def _load_config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                            cfg[k].update(v)
                        else:
                            cfg[k] = v
            except Exception as e:
                print(f"[配置] hub_config.json 解析失败：{e}")
        if not cfg["hub"].get("tools_root"):
            cfg["hub"]["tools_root"] = str(BASE_DIR.parent)
        return cfg

    def config(self) -> dict:
        with lock:
            return self._cfg

    def save_config(self, cfg: dict) -> None:
        with lock:
            self._cfg = cfg
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def data_dir(self) -> str:
        return str(DATA_DIR)

    # ── 工具配置（配置优先、默认兜底）────────────────────
    def adapter_cls(self, tool_id: str):
        return adapter_registry.get(tool_id)

    def raw_tool_cfg(self, tool_id: str) -> dict:
        cfg = self.config()
        item = dict((cfg.get("tools") or {}).get(tool_id) or {})
        cls = self.adapter_cls(tool_id)
        if cls is None:
            return item
        root = str((cfg.get("hub") or {}).get("tools_root") or "")
        if not str(item.get("dir") or "").strip():
            dn = getattr(cls, "default_dirname", "") or ""
            item["dir"] = str(Path(root) / dn) if root and dn else ""
        defaults = {
            "entry": getattr(cls, "default_entry", ""),
            "args": list(getattr(cls, "default_args", []) or []),
            "env_name": getattr(cls, "default_env_name", ""),
            "port": getattr(cls, "default_port", None),
            "intercept": getattr(cls, "default_intercept", False),
            "stdin_lines": getattr(cls, "default_stdin_lines", 0),
            "enabled": getattr(cls, "default_enabled", True),
        }
        for k, v in defaults.items():
            if k not in item or item[k] is None:
                item[k] = v
        return item

    def adapter(self, tool_id: str):
        cls = self.adapter_cls(tool_id)
        if cls is None:
            return None
        return cls(self.raw_tool_cfg(tool_id))

    def save_tool_cfg(self, tool_id: str, patch: dict) -> dict:
        cfg = json.loads(json.dumps(self.config()))
        tools = cfg.setdefault("tools", {})
        item = dict(tools.get(tool_id) or {})
        for k, v in (patch or {}).items():
            item[k] = v
        tools[tool_id] = item
        self.save_config(cfg)
        return item

    def runtime(self, tool_id: str) -> Runtime:
        with lock:
            rt = self.runtimes.get(tool_id)
            if rt is None:
                rt = Runtime(tool_id)
                self.runtimes[tool_id] = rt
            return rt

    # ── 前端推送 ────────────────────────────────────────
    def notify(self, ev: dict):
        with lock:
            qs = list(self.clients)
        for q in qs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    # ── 路径校验（限定 Tools 根目录内）───────────────────
    def check_dir(self, d: str) -> tuple[bool, str]:
        root = Path(str((self.config().get("hub") or {}).get("tools_root") or "")).resolve()
        try:
            p = Path(d).resolve()
        except Exception:
            return False, "路径无效"
        if not str(root):
            return True, ""
        try:
            p.relative_to(root)
        except ValueError:
            return False, f"目录必须位于工具根目录内：{root}"
        return True, ""

    # ── 状态汇总 ────────────────────────────────────────
    def tool_view(self, tool_id: str) -> dict:
        cls = self.adapter_cls(tool_id)
        ad = self.adapter(tool_id)
        rt = self.runtime(tool_id)
        tcfg = self.raw_tool_cfg(tool_id)
        running = rt.running()
        p = ad.port() if ad else None
        view = {
            "id": tool_id,
            "name": getattr(cls, "name", tool_id),
            "description": getattr(cls, "description", ""),
            "icon": getattr(cls, "icon", "🔧"),
            "ui_type": getattr(cls, "ui_type", "none"),
            "bridge_mode": getattr(cls, "bridge_mode", "none"),
            "defaults": {
                "dirname": getattr(cls, "default_dirname", ""),
                "entry": getattr(cls, "default_entry", ""),
                "env_name": getattr(cls, "default_env_name", ""),
                "port": getattr(cls, "default_port", None),
            },
            "config": tcfg,
            "status": {
                "running": running,
                "ready": bool(ad.ready(running, port_open(p))) if ad else running,
                "pid": rt.proc.pid if running else None,
                "started_at": rt.started_at or None,
                "port": p,
                "port_open": port_open(p) if p else None,
                "last_error": rt.last_error,
            },
            "open_url": ad.open_url() if ad else "",
            "gateway_url": ad.bridge_gateway_url(self.hub_base()) if ad else "",
            "probe": ad.probe() if ad else {},
            "command": self.build_command(ad) if ad else [],
            "auto_send": self.bridge.auto_send(tool_id),
            "has_page": (PAGES_DIR / f"{tool_id}.js").exists(),
            "has_qrcode": bool(ad.qrcode_path()) if ad else False,
        }
        return view

    def all_tools(self) -> list[dict]:
        views = []
        for tid in adapter_registry.all_adapters().keys():
            views.append(self.tool_view(tid))
        views.sort(key=lambda v: (not v["config"].get("enabled", True), v["name"]))
        return views

    def hub_base(self) -> str:
        host = str(self.config().get("hub", {}).get("host") or "127.0.0.1")
        port = int(self.config().get("hub", {}).get("port") or 8077)
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    # ── 进程管理 ────────────────────────────────────────
    def build_command(self, ad) -> list:
        cmd = list(ad.command())
        if not cmd:
            return []
        entry = ad.entry()
        env_name = ad.env_name()
        if Path(entry).suffix.lower() == ".py" and env_name:
            py = conda_env_python(env_name)
            # 替换命令中的 python 为本环境解释器（保持原有参数）
            if cmd and Path(cmd[0]).name.lower() in ("python", "python.exe"):
                cmd[0] = py
        return cmd

    def start_tool(self, tool_id: str) -> dict:
        ad = self.adapter(tool_id)
        if ad is None:
            return {"ok": False, "message": "未知工具"}
        rt = self.runtime(tool_id)
        if rt.running():
            return {"ok": False, "message": f"{ad.name} 已在运行（PID {rt.proc.pid}）"}

        ok, msg = self.check_dir(str(ad.dir_path()))
        if not ok:
            return {"ok": False, "message": msg}
        cmd = self.build_command(ad)
        if not cmd:
            return {"ok": False, "message": "未配置启动入口（entry）"}
        cwd = ad.cwd()
        if not Path(cwd).is_dir():
            return {"ok": False, "message": f"工作目录不存在：{cwd}"}

        p = ad.port()
        if p and port_open(p):
            return {"ok": False, "message": f"端口 {p} 已被占用（可能是该工具已在运行，或端口冲突）"}

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["HUB_URL"] = self.hub_base()
        env["HUB_TOOL_ID"] = tool_id
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        n_stdin = ad.stdin_lines()
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if n_stdin else subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                creationflags=creationflags,
            )
        except Exception as e:
            rt.last_error = str(e)
            return {"ok": False, "message": f"启动失败：{e}"}

        rt.proc = proc
        rt.started_at = time.time()
        rt.last_error = ""
        rt.stdin_pipe = proc.stdin if n_stdin else None
        rt.append_log(f"===== [{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 {ad.name} =====")
        rt.append_log(f"$ {' '.join(cmd)}   (cwd={cwd})")

        if n_stdin and rt.stdin_pipe is not None:
            def _answer():
                try:
                    for _ in range(n_stdin):
                        time.sleep(0.8)
                        rt.stdin_pipe.write("\n")
                        rt.stdin_pipe.flush()
                except Exception:
                    pass
            threading.Thread(target=_answer, daemon=True).start()

        threading.Thread(target=self._reader, args=(tool_id, proc), daemon=True).start()
        threading.Thread(target=self._watcher, args=(tool_id, proc), daemon=True).start()
        self.notify({"type": "tool_status", "tool": tool_id})
        return {"ok": True, "message": f"{ad.name} 已启动（PID {proc.pid}）", "pid": proc.pid}

    def _reader(self, tool_id: str, proc: subprocess.Popen):
        rt = self.runtime(tool_id)
        try:
            for line in proc.stdout or []:
                line = (line or "").rstrip("\r\n")
                rt.append_log(line)
                self.notify({"type": "log", "tool": tool_id, "line": line})
        except Exception:
            pass

    def _watcher(self, tool_id: str, proc: subprocess.Popen):
        code = proc.wait()
        rt = self.runtime(tool_id)
        rt.append_log(f"===== 进程退出，code={code} =====")
        if code != 0:
            rt.last_error = f"进程异常退出（code={code}）"
        self.notify({"type": "tool_status", "tool": tool_id, "exit_code": code})

    def stop_tool(self, tool_id: str) -> dict:
        rt = self.runtime(tool_id)
        if not rt.running():
            return {"ok": False, "message": "该工具未在运行"}
        pid = rt.proc.pid
        try:
            # /T 连子进程一起结束（Windows 上 bat 会派生子进程）
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, timeout=20)
        except Exception as e:
            try:
                rt.proc.kill()
            except Exception:
                pass
            rt.last_error = str(e)
        try:
            rt.proc.wait(timeout=10)
        except Exception:
            pass
        self.notify({"type": "tool_status", "tool": tool_id})
        return {"ok": True, "message": f"已停止 PID {pid}"}

    # ── 桥代理 ──────────────────────────────────────────
    def proxy_login(self, tool_id: str, body: bytes) -> tuple[int, dict]:
        cfg = self.config()
        url = str((cfg.get("msa") or {}).get("gateway_url") or "http://127.0.0.1:8080").rstrip("/")
        try:
            r = requests.post(f"{url}/api/login", data=body,
                              headers={"Content-Type": "application/json"}, timeout=15)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {"ok": False, "raw": r.text[:300]}
        except Exception as e:
            return 502, {"ok": False, "error": f"网关不可达：{e}"}

    def proxy_messages(self, tool_id: str, body: bytes) -> tuple[int, dict]:
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
        except Exception:
            payload = {}
        content = str(payload.get("content") or "")
        uid = str(payload.get("user_id") or "").strip()
        aid = str(payload.get("agent_id") or "").strip()
        if not content.strip():
            return 400, {"ok": False, "detail": "missing content"}

        if self.bridge.should_intercept(tool_id):
            if self.bridge.auto_send(tool_id):
                # 自动发送：不扣留，按「携带信息」模板加工后立即上报
                uid2, aid2 = uid, aid
                if not uid2:
                    uid2 = self.bridge._defaults(tool_id)[0]
                final = self.apply_auto_carry(tool_id, content)
                try:
                    res = self.bridge.forward(tool_id, final, uid2, aid2, source="auto")
                    return 200, dict(res, status="auto-sent", carried=bool(final != content))
                except Exception as e:
                    return 502, {"ok": False, "error": str(e)}
            item = self.bridge.hold(tool_id, content, extra={"user_id": uid, "agent_id": aid})
            return 200, {
                "ok": True,
                "task_id": "hub-held-" + item["id"],
                "status": "held",
                "message": "消息已被 ToolsHub 扣留，等待人工决定上报或删除",
                "pending_id": item["id"],
            }
        try:
            res = self.bridge.forward(tool_id, content, uid, aid, source="direct")
            return 200, res
        except Exception as e:
            return 502, {"ok": False, "error": str(e)}

    def probe_msa(self) -> dict:
        try:
            return self.bridge.client_for("").health()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 壁纸同步（代理 MSA 前端的 /backgrounds/）────────────
    def frontend_url(self) -> str:
        msa = self.config().get("msa") or {}
        return str(msa.get("frontend_url") or msa.get("gateway_url") or "http://127.0.0.1:8080").rstrip("/")

    def backgrounds(self, name: str):
        """name 为空 → 返回壁纸清单 JSON；否则返回图片内容（全部来自 MSA 前端）。"""
        base = self.frontend_url()
        if not name:
            r = requests.get(f"{base}/backgrounds/", timeout=10)
            return r.content, r.headers.get("Content-Type") or "application/json; charset=utf-8"
        if not re.match(r"^[A-Za-z0-9_.\-]+$", name) or ".." in name:
            return b'{"ok":false,"error":"invalid name"}', "application/json"
        r = requests.get(f"{base}/backgrounds/{quote(name)}", timeout=30)
        return r.content, r.headers.get("Content-Type") or "application/octet-stream"

    # ── 二维码取源：优先 WebUI API（按实例端口，带精确过期时间），回落文件 ──
    def qrcode_fetch_api(self, tool_id: str):
        """调工具 WebUI 的 /api/login-qrcode。返回 (png_bytes|None, meta|None, error|None)。"""
        ad = self.adapter(tool_id)
        src = ad.qrcode_api() if ad is not None else None
        if not src:
            return None, None, "未配置 WebUI 二维码源"
        url, token = str(src.get("url") or "").rstrip("/"), str(src.get("token") or "")
        if not url:
            return None, None, "未配置 WebUI 地址"
        headers = {"x-webui-token": token} if token else {}
        try:
            r = requests.get(f"{url}/api/login-qrcode", headers=headers, timeout=8)
        except Exception as e:
            return None, None, f"连接 WebUI 失败：{e}"
        if r.status_code != 200:
            try:
                msg = r.json().get("message") or r.text[:80]
            except Exception:
                msg = r.text[:80]
            return None, None, f"HTTP {r.status_code}：{msg}"
        try:
            d = r.json()
        except Exception:
            return None, None, "返回不是 JSON"
        if not d.get("success"):
            return None, None, d.get("message") or "接口返回失败"
        data = d.get("data") or {}
        b64 = str(data.get("pngBase64QrcodeData") or "")
        if not b64:
            return None, None, "返回中没有二维码数据"
        try:
            raw = base64.b64decode(b64.split(",")[-1])
        except Exception as e:
            return None, None, f"二维码解码失败：{e}"
        meta = {
            "expire_time": data.get("expireTime"),
            "fetched_at": time.time(),
            "url": url,
        }
        return raw, meta, None

    def qrcode_fetch_file(self, tool_id: str):
        """读工具的二维码文件。返回 (path|None, stat|None, error|None)。"""
        ad = self.adapter(tool_id)
        qp = ad.qrcode_path() if ad is not None else None
        if qp is None:
            return None, None, "未找到二维码文件"
        try:
            return qp, qp.stat(), None
        except Exception as e:
            return None, None, str(e)

    def qrcode_info(self, tool_id: str) -> dict:
        """汇总两个来源的状态，供前端展示。"""
        api_ok, api_meta, api_err = self.qrcode_fetch_api(tool_id)
        fpath, fstat, ferr = self.qrcode_fetch_file(tool_id)
        out = {
            "ok": True,
            "source": "api" if api_ok else ("file" if fstat else "none"),
            "api": {
                "available": bool(api_ok),
                "error": api_err or "",
                "url": (self.adapter(tool_id).qrcode_api() or {}).get("url", "")
                       if self.adapter(tool_id) and self.adapter(tool_id).qrcode_api() else "",
                "expire_in": None,
                "fetched_str": None,
            },
            "file": {
                "exists": bool(fstat),
                "path": str(fpath) if fpath else "",
                "size": fstat.st_size if fstat else 0,
                "updated_at": fstat.st_mtime if fstat else 0,
                "updated_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fstat.st_mtime)) if fstat else "",
                "age_sec": int(time.time() - fstat.st_mtime) if fstat else None,
                "error": ferr or "",
            },
        }
        if api_meta:
            exp = api_meta.get("expire_time")
            out["api"]["fetched_str"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                                      time.localtime(api_meta.get("fetched_at") or time.time()))
            if exp:
                left = float(exp) - time.time() if float(exp) > 10 ** 10 else float(exp)
                out["api"]["expire_in"] = int(left)
        return out

    def qrcode_image(self, tool_id: str):
        """返回 (png_bytes, source)。优先 API，失败回落文件。"""
        raw, _meta, _err = self.qrcode_fetch_api(tool_id)
        if raw:
            return raw, "api"
        fpath, _stat, _ferr = self.qrcode_fetch_file(tool_id)
        if fpath is not None:
            try:
                return fpath.read_bytes(), "file"
            except Exception:
                return None, "error"
        return None, "none"

    # ── 自动上报的「携带信息」模板 ────────────────────────
    def apply_auto_carry(self, tool_id: str, content: str) -> str:
        """按工具配置的 auto_prefix 模板加工自动上报的消息。

        可用占位符：
            {group} {sender} {sender_id} {account} —— 由适配器从原文解析
            {content}                                —— 消息正文（解析结果，无解析时=原文）
            {raw}                                    —— 完整原文
        若模板中既未写 {content} 也未写 {raw}，则把整条原文接在模板之后。
        """
        tcfg = self.raw_tool_cfg(tool_id)
        tpl = str(tcfg.get("auto_prefix") or "")
        if not tpl.strip():
            return content
        parsed = {}
        ad = self.adapter(tool_id)
        if ad is not None:
            try:
                payload = ad.on_held_message({"raw": content, "parsed": {}, "summary": ""}) or {}
                parsed = payload.get("parsed") or {}
            except Exception:
                pass
        mapping = {
            "group": parsed.get("group", ""),
            "sender": parsed.get("sender", ""),
            "sender_id": parsed.get("sender_id", ""),
            "account": parsed.get("account", ""),
            "content": parsed.get("content") or content,
            "raw": content,
        }
        out = tpl
        for k, v in mapping.items():
            out = out.replace("{" + k + "}", str(v))
        if "{content}" not in tpl and "{raw}" not in tpl:
            out = out.rstrip() + "\n" + content
        return out


HUB = Hub()


# ===================== HTTP 服务 =====================
RE_TOOL_API = re.compile(r"^/t/(?P<tool>[A-Za-z0-9_\-]+)(?P<sub>/api/.*)$")
RE_TOOL_ACTION = re.compile(r"^/api/tools/(?P<tool>[A-Za-z0-9_\-]+)/(?P<action>[a-z\-]+)$")
RE_PENDING_ACTION = re.compile(r"^/api/pending/(?P<pid>[A-Za-z0-9_\-]+)/(?P<action>approve|delete)$")
RE_CHAT = re.compile(r"^/api/chat/(?P<tool>[A-Za-z0-9_\-]+)/(?P<action>messages|send|clear)$")
RE_QR = re.compile(r"^/api/tools/(?P<tool>[A-Za-z0-9_\-]+)/qrcode(?P<sub>/info)?$")
RE_STATIC = re.compile(r"^/(?P<kind>pages|static)/(?P<name>[A-Za-z0-9_\-]+\.(?:js|css))$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            self.close_connection = True

    # ── 响应助手 ────────────────────────────────────────
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        return self.rfile.read(n) if n else b""

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    # ── GET ─────────────────────────────────────────────
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            try:
                body = INDEX_FILE.read_bytes()
            except FileNotFoundError:
                return self._json({"ok": False, "error": "缺少 index.html"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)

        if path == "/api/hub/state":
            return self._json({
                "ok": True,
                "hub": HUB.config().get("hub", {}),
                "hub_base": HUB.hub_base(),
                "msa": HUB.config().get("msa", {}),
                "msa_health": HUB.probe_msa(),
                "tools": HUB.all_tools(),
                "pending_stats": HUB.bridge.stats(),
                "adapter_errors": adapter_registry.discover_errors(),
            })

        if path == "/api/hub/events":
            return self._hub_sse()

        if path == "/api/pending":
            status = (qs.get("status") or [""])[0]
            return self._json({"ok": True, "items": HUB.bridge.pending_list(status),
                               "stats": HUB.bridge.stats()})

        # 静态资源：工具专属页面脚本 / 样式
        sm = RE_STATIC.match(path)
        if sm:
            kind, name = sm.group("kind"), sm.group("name")
            fp = (PAGES_DIR if kind == "pages" else BASE_DIR / "static") / name
            if not fp.exists():
                return self._json({"ok": False, "error": "not found"}, 404)
            ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)

        # 壁纸同步：/backgrounds/ 与 /backgrounds/<file> 代理到 MSA 前端
        if path == "/backgrounds/" or path.startswith("/backgrounds/"):
            name = path[len("/backgrounds/"):]
            try:
                body, ctype = HUB.backgrounds(name)
            except Exception as e:
                return self._json({"ok": False, "error": f"背景图获取失败：{e}"}, 502)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)

        # 工具二维码（优先 WebUI API，回落文件）
        qm = RE_QR.match(path)
        if qm:
            tool_id, sub = qm.group("tool"), qm.group("sub")
            if sub == "/info":
                return self._json(HUB.qrcode_info(tool_id))
            body, source = HUB.qrcode_image(tool_id)
            if not body:
                return self._json({"ok": False, "message": "未取得二维码（API 不可用且无二维码文件）"}, 404)
            ctype = "image/png"
            if body[:2] == b"\xff\xd8":
                ctype = "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Qrcode-Source", source)
            self.end_headers()
            return self.wfile.write(body)

        cm = RE_CHAT.match(path)
        if cm and cm.group("action") == "messages":
            tool = cm.group("tool")
            limit = int((qs.get("limit") or ["200"])[0] or 200)
            return self._json({
                "ok": True,
                "items": HUB.chat_store(tool).list(limit),
                "auto_send": HUB.bridge.auto_send(tool),
                "intercept": HUB.bridge.should_intercept(tool),
                "defaults": {"user_id": HUB.bridge._defaults(tool)[0],
                             "agent_id": HUB.bridge._defaults(tool)[1]},
            })

        if path == "/api/traffic":
            limit = int((qs.get("limit") or ["100"])[0] or 100)
            return self._json({"ok": True, "items": HUB.bridge.traffic(limit)})

        m = RE_TOOL_ACTION.match(path)
        if m and m.group("action") == "log":
            tool = m.group("tool")
            tail = int((qs.get("tail") or ["400"])[0] or 400)
            rt = HUB.runtime(tool)
            return self._json({"ok": True, "lines": rt.tail(tail), "log_file": str(rt.log_path)})
        if m and m.group("action") == "probe":
            ad = HUB.adapter(m.group("tool"))
            if ad is None:
                return self._json({"ok": False, "message": "未知工具"}, 404)
            return self._json({"ok": True, "probe": ad.probe(), "command": HUB.build_command(ad)})

        mm = RE_TOOL_API.match(path)
        if mm:
            return self._tool_proxy_get(mm.group("tool"), mm.group("sub"))

        return self._json({"ok": False, "error": "not found"}, 404)

    # ── POST ────────────────────────────────────────────
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        body = self._read_body()

        if path == "/api/hub/config":
            try:
                patch = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                return self._json({"ok": False, "message": "body 不是合法 JSON"}, 400)
            cfg = json.loads(json.dumps(HUB.config()))
            for k, v in (patch or {}).items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            HUB.save_config(cfg)
            HUB.notify({"type": "config"})
            return self._json({"ok": True, "message": "已保存 hub 配置", "config": cfg})

        if path == "/api/hub/reload-adapters":
            found = adapter_registry.reload_adapters()
            return self._json({"ok": True, "message": f"已重新扫描，发现 {len(found)} 个适配器",
                               "tools": list(found.keys()),
                               "errors": adapter_registry.discover_errors()})

        if path == "/api/pending/clear":
            return self._json(HUB.bridge.clear_decided())

        cm = RE_CHAT.match(path)
        if cm:
            tool, action = cm.group("tool"), cm.group("action")
            if action == "clear":
                n = HUB.chat_store(tool).clear()
                return self._json({"ok": True, "message": f"已清空 {n} 条聊天记录"})
            if action == "send":
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                except Exception:
                    data = {}
                text = str(data.get("text") or "").strip()
                if not text:
                    return self._json({"ok": False, "message": "内容为空"}, 400)
                prefix = str(data.get("prefix") or "")
                if prefix:
                    text = prefix + text
                uid = str(data.get("user_id") or "").strip()
                aid = str(data.get("agent_id") or "").strip()
                try:
                    res = HUB.bridge.forward(tool, text, uid, aid, source="manual")
                    return self._json({"ok": True, "message": "已发送给 agent", "result": res})
                except Exception as e:
                    return self._json({"ok": False, "message": f"发送失败：{e}"}, 502)

        m = RE_PENDING_ACTION.match(path)
        if m:
            pid, action = m.group("pid"), m.group("action")
            if action == "delete":
                return self._json(HUB.bridge.delete(pid))
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                data = {}
            return self._json(HUB.bridge.approve(
                pid,
                text=str(data.get("text") or ""),
                prefix=str(data.get("prefix") or ""),
                user_id=str(data.get("user_id") or ""),
                agent_id=str(data.get("agent_id") or ""),
            ))

        m = RE_TOOL_ACTION.match(path)
        if m:
            tool, action = m.group("tool"), m.group("action")
            if action == "config":
                try:
                    patch = json.loads(body.decode("utf-8") or "{}")
                except Exception:
                    return self._json({"ok": False, "message": "body 不是合法 JSON"}, 400)
                if patch.get("dir"):
                    ok, msg = HUB.check_dir(str(patch["dir"]))
                    if not ok:
                        return self._json({"ok": False, "message": msg}, 400)
                item = HUB.save_tool_cfg(tool, patch)
                HUB.notify({"type": "tool_status", "tool": tool})
                return self._json({"ok": True, "message": "已保存工具配置", "config": item})
            if action == "start":
                return self._json(HUB.start_tool(tool))
            if action == "stop":
                return self._json(HUB.stop_tool(tool))
            if action == "restart":
                HUB.stop_tool(tool)
                time.sleep(1.0)
                return self._json(HUB.start_tool(tool))
            if action == "bridge-apply":
                ad = HUB.adapter(tool)
                if ad is None or not hasattr(ad, "bridge_apply"):
                    return self._json({"ok": False, "message": "该工具不支持自动对接"}, 400)
                return self._json(ad.bridge_apply(HUB.hub_base()))
            if action == "bridge-revert":
                ad = HUB.adapter(tool)
                if ad is None or not hasattr(ad, "bridge_revert"):
                    return self._json({"ok": False, "message": "该工具不支持还原"}, 400)
                cfg = HUB.config()
                url = str((cfg.get("msa") or {}).get("gateway_url") or "http://127.0.0.1:8080")
                return self._json(ad.bridge_revert(url))

        mm = RE_TOOL_API.match(path)
        if mm:
            return self._tool_proxy_post(mm.group("tool"), mm.group("sub"), body)

        return self._json({"ok": False, "error": "not found"}, 404)

    # ── 桥：同构代理 ────────────────────────────────────
    def _tool_proxy_post(self, tool: str, sub: str, body: bytes):
        if sub == "/api/login":
            code, data = HUB.proxy_login(tool, body)
            return self._json(data, code)
        if sub == "/api/messages":
            code, data = HUB.proxy_messages(tool, body)
            return self._json(data, code)
        return self._json({"ok": False, "message": f"未实现的桥接口：{sub}"}, 404)

    def _tool_proxy_get(self, tool: str, sub: str):
        if sub != "/api/events":
            return self._json({"ok": False, "message": f"未实现的桥接口：{sub}"}, 404)

        self._sse_headers()
        intercept = HUB.bridge.should_intercept(tool)
        try:
            if intercept:
                # 扣留模式：工具无需接收回复，返回空闲流保持连接
                self.wfile.write(b": hub-held-mode\n\n")
                self.wfile.flush()
                while True:
                    time.sleep(15)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            else:
                uid = HUB.bridge._defaults(tool)[0]
                for ev in HUB.bridge.client_for(tool).iter_events(uid):
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ── 前端 SSE ────────────────────────────────────────
    def _hub_sse(self):
        self._sse_headers()
        q: queue.Queue = queue.Queue(maxsize=2000)
        with lock:
            HUB.clients.append(q)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with lock:
                if q in HUB.clients:
                    HUB.clients.remove(q)


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    cfg = HUB.config()
    host = str(cfg.get("hub", {}).get("host") or "0.0.0.0")
    port = int(cfg.get("hub", {}).get("port") or 8077)
    found = adapter_registry.all_adapters()
    errs = adapter_registry.discover_errors()

    print("=" * 56)
    print("ToolsHub —— 工具聚合 / MSA 中间对接")
    print("=" * 56)
    print(f"工具根目录：{cfg.get('hub', {}).get('tools_root')}")
    print(f"已发现适配器（{len(found)}）：{', '.join(found.keys()) or '（无）'}")
    for mod, err in errs.items():
        print(f"  [适配器加载失败] {mod}: {err.splitlines()[-1] if err else ''}")
    print(f"本机访问:   http://127.0.0.1:{port}")
    print(f"局域网访问: http://{get_lan_ip()}:{port}   (手机需与电脑同一网络)")
    print("按 Ctrl+C 停止服务")
    # 单实例保护：Windows 上 SO_REUSEADDR 允许多个进程绑定同一端口，
    # 多实例会导致请求被随机分配、界面行为错乱，这里先用 connect 探测。
    if port_open(port):
        print(f"[错误] 端口 {port} 已被占用：可能已有一个 ToolsHub 实例在运行。")
        print("       请先关闭旧实例（或修改 hub_config.json 的 hub.port），避免多实例抢答。")
        sys.exit(1)
    HUB.start_chat_sessions()
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
