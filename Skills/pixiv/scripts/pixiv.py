#!/usr/bin/env python3
"""Pixiv 搜索下载工具 — Cookie 鉴权模式"""

import requests
import yaml
import os
import re
import argparse
import sys

sys.stdout.reconfigure(encoding='utf-8')

CONFIG_PATH = "config.yaml"

COOKIE_HELP = """
╔══════════════════════════════════════════════════════╗
║         PHPSESSID 已过期或无效，请更新 Cookie         ║
╠══════════════════════════════════════════════════════╣
║  获取步骤：                                          ║
║  1. 浏览器打开 https://www.pixiv.net 并登录           ║
║  2. 按 F12 → Application → Cookies → pixiv.net      ║
║  3. 找到 PHPSESSID，复制它的值                        ║
║                                                      ║
║  更新命令：                                          ║
║  python3 scripts/pixiv.py cookie --set "你的值"       ║
╚══════════════════════════════════════════════════════╝
"""


def show_cookie_help():
    print(COOKIE_HELP, file=sys.stderr)


class PixivClient:
    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        pixiv_cfg = self.config.get("pixiv", {})
        self.cookie = pixiv_cfg.get("cookie", "")
        self.download_dir = os.path.abspath(pixiv_cfg.get("download_dir", "./downloads"))
        self.default_limit = self.config.get("search", {}).get("limit", 5)

        self.session = requests.Session()

    def _save_cookie(self, phpsessid):
        cookie_str = f"PHPSESSID={phpsessid};"
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        config.setdefault("pixiv", {})["cookie"] = cookie_str
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
        self.cookie = cookie_str
        print(f"[成功] PHPSESSID 已保存到 {self.config_path}")

    def _headers(self):
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.pixiv.net/",
            "Cookie": self.cookie,
        }

    def _is_cookie_error(self, data):
        """检测响应是否因为 Cookie 失效"""
        if not isinstance(data, dict):
            return False
        if not data.get("error"):
            return False
        # body 为空列表 + error=true → Cookie 失效
        if isinstance(data.get("body"), list) and len(data.get("body", [])) == 0:
            return True
        msg = str(data.get("message", "")).lower()
        return any(kw in msg for kw in [
            "auth", "login", "unauthorized", "session", "cookie",
            "不明なエラー",  # Pixiv 的通用错误（Cookie 失效时常见）
        ])

    def _handle_cookie_error(self):
        """Cookie 失效时显示帮助并尝试交互式更新"""
        show_cookie_help()
        try:
            choice = input("是否现在输入新的 PHPSESSID？[y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                phpsessid = input("PHPSESSID: ").strip()
                if phpsessid:
                    m = re.search(r"PHPSESSID=([^;]+)", phpsessid)
                    if m:
                        phpsessid = m.group(1)
                    self._save_cookie(phpsessid)
                    return True
        except (EOFError, KeyboardInterrupt):
            pass
        return False

    def search(self, keyword, page=1, limit=None):
        fetch_limit = limit if limit is not None else self.default_limit
        start = (page - 1) * fetch_limit
        end = start + fetch_limit

        url = f"https://www.pixiv.net/ajax/search/illustrations/{keyword}"
        params = {"p": page, "mode": "all", "order": "date_d"}

        r = self.session.get(url, params=params, headers=self._headers())
        data = r.json()

        if self._is_cookie_error(data):
            if self._handle_cookie_error():
                # 重试
                r = self.session.get(url, params=params, headers=self._headers())
                data = r.json()
            else:
                return []

        body = data.get("body", {})
        if isinstance(body, dict):
            items = body.get("illust", {}).get("data", [])
        else:
            items = []
        return items[start:end]

    def get_illust_info(self, illust_id):
        url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
        r = self.session.get(url, headers=self._headers())
        data = r.json()

        if self._is_cookie_error(data):
            if self._handle_cookie_error():
                r = self.session.get(url, headers=self._headers())
                data = r.json()
            else:
                raise RuntimeError("Cookie 无效，无法获取作品信息")

        return data["body"]

    def download(self, illust_id):
        try:
            info = self.get_illust_info(illust_id)
        except (KeyError, RuntimeError) as e:
            print(f"[错误] 获取作品信息失败: {e}", file=sys.stderr)
            show_cookie_help()
            return []

        page_count = info.get("pageCount", 1)
        saved_paths = []

        img_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.pixiv.net/",
            "Cookie": self.cookie,
        }

        for p in range(page_count):
            img_url = info["urls"]["original"].replace("_p0", f"_p{p}")
            proxy_url = img_url.replace("i.pximg.net", "i.pixiv.re")

            os.makedirs(self.download_dir, exist_ok=True)
            filename = os.path.basename(img_url)
            save_path = os.path.join(self.download_dir, filename)

            try:
                resp = requests.get(proxy_url, headers=img_headers, timeout=40)
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                saved_paths.append(save_path)
            except Exception as e:
                print(f"[下载失败] {proxy_url}，错误：{str(e)}")
                continue

        return saved_paths


def main():
    parser = argparse.ArgumentParser(description="Pixiv 搜索下载工具")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    sp = subparsers.add_parser("search", help="搜索插画")
    sp.add_argument("--keyword", required=True, help="搜索关键词")
    sp.add_argument("--page", type=int, default=1, help="页码")
    sp.add_argument("--limit", type=int, help="获取条数，默认读取 config")

    dp = subparsers.add_parser("download", help="下载插画（支持多页）")
    dp.add_argument("--id", required=True, help="作品ID")

    subparsers.add_parser("info", help="查看当前配置")

    cp = subparsers.add_parser("cookie", help="设置 PHPSESSID Cookie")
    cp.add_argument("--set", dest="phpsessid", help="PHPSESSID 值")

    args = parser.parse_args()

    if args.cmd == "cookie":
        client = PixivClient()
        if hasattr(args, "phpsessid") and args.phpsessid:
            phpsessid = args.phpsessid.strip()
        else:
            print("=" * 50)
            print("获取 PHPSESSID 步骤：")
            print("  1. 浏览器打开 https://www.pixiv.net 并登录")
            print("  2. F12 → Application → Cookies → pixiv.net")
            print("  3. 复制 PHPSESSID 的值")
            print("=" * 50)
            phpsessid = input("\nPHPSESSID: ").strip()

        if not phpsessid:
            print("[错误] PHPSESSID 不能为空")
            return

        m = re.search(r"PHPSESSID=([^;]+)", phpsessid)
        if m:
            phpsessid = m.group(1)

        client._save_cookie(phpsessid)
        print("[提示] 运行 python3 scripts/pixiv.py info 验证配置")
        return

    client = PixivClient()

    if args.cmd == "search":
        items = client.search(args.keyword, page=args.page, limit=args.limit)
        if not items:
            print(f"[搜索] {args.keyword} | 无结果（可能 Cookie 已过期）", file=sys.stderr)
            return
        print(f"[搜索] {args.keyword} | 第{args.page}页\n")
        for item in items:
            print(f"[ID] {item['id']}")
            print(f"[标题] {item['title']}")
            print(f"[作者] {item['userName']}")
            print(f"[分辨率] {item.get('width','?')}x{item.get('height','?')}")
            print(f"[页数] {item.get('pageCount',1)}")
            print(f"[标签] {', '.join(item.get('tags', []))}")
            print(f"[分级] {'R18' if item.get('xRestrict',0) else '全年龄'}")
            print(f"[AI创作] {'是' if item.get('aiType',1)-1 else '否'}")
            print("-" * 60)

    elif args.cmd == "download":
        print(f"[下载] 作品ID: {args.id}")
        paths = client.download(args.id)
        for p in paths:
            print(f"[完成] {p}")
        if not paths:
            print("[提示] 下载失败，请检查 Cookie 是否有效", file=sys.stderr)

    elif args.cmd == "info":
        print("=" * 50)
        print(f"[下载目录] {client.download_dir}")
        print(f"[默认搜索上限] {client.default_limit} 条")
        print(f"[Cookie] {'已配置' if client.cookie else '未配置'}")
        if client.cookie:
            m = re.search(r"PHPSESSID=([^;]+)", client.cookie)
            if m:
                print(f"  PHPSESSID: {m.group(1)[:16]}...")
        print("=" * 50)


if __name__ == "__main__":
    main()
