import requests
import yaml
import os
import argparse
import time
import json
from pathlib import Path

# 解决 Windows GBK 编码问题
import sys
sys.stdout.reconfigure(encoding='utf-8')

# Pixiv 官方 APP 的 OAuth 凭据（公开信息）
PIXIV_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
PIXIV_CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
PIXIV_AUTH_URL = "https://oauth.secure.pixiv.net/auth/token"
CONFIG_PATH = "config.yaml"


class PixivClient:
    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        pixiv_cfg = self.config.get("pixiv", {})
        self.cookie = pixiv_cfg.get("cookie", "")
        self.download_dir = os.path.abspath(self.config["pixiv"]["download_dir"])
        self.default_limit = self.config.get("search", {}).get("default_limit", 5)

        # OAuth 认证
        auth_cfg = pixiv_cfg.get("auth", {})
        self.refresh_token = auth_cfg.get("refresh_token", "")
        self.access_token = auth_cfg.get("access_token", "")
        self.token_expires_at = auth_cfg.get("expires_at", 0)

        self.session = requests.Session()

    def _ensure_auth(self):
        """确保 access_token 有效，必要时自动刷新"""
        if not self.refresh_token:
            return False
        if self.access_token and time.time() < self.token_expires_at - 60:
            return True
        # 刷新 token
        try:
            resp = requests.post(PIXIV_AUTH_URL, data={
                "client_id": PIXIV_CLIENT_ID,
                "client_secret": PIXIV_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "get_secure_url": 1,
            }, timeout=15)
            data = resp.json()
            if "access_token" in data:
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self.token_expires_at = time.time() + data.get("expires_in", 3600)
                self._save_auth()
                return True
            else:
                print(f"[OAuth] 刷新 token 失败: {data}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[OAuth] 刷新 token 异常: {e}", file=sys.stderr)
            return False

    def _save_auth(self):
        """将刷新后的 token 写回配置文件"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            config.setdefault("pixiv", {}).setdefault("auth", {})
            config["pixiv"]["auth"]["access_token"] = self.access_token
            config["pixiv"]["auth"]["refresh_token"] = self.refresh_token
            config["pixiv"]["auth"]["expires_at"] = self.token_expires_at
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            print(f"[OAuth] 保存 token 失败: {e}", file=sys.stderr)

    def _headers(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.pixiv.net/",
        }
        if self._ensure_auth():
            headers["Authorization"] = f"Bearer {self.access_token}"
        elif self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def search(self, keyword, page=1, limit=None):
        fetch_limit = limit if limit is not None else self.default_limit
        start = (page - 1) * fetch_limit
        end = start + fetch_limit

        url = f"https://www.pixiv.net/ajax/search/illustrations/{keyword}"
        params = {"p": page, "mode": "all", "order": "date_d"}

        r = self.session.get(url, params=params, headers=self._headers())
        data = r.json()
        # 兼容新旧 API 返回格式
        body = data.get("body", {})
        if isinstance(body, dict):
            items = body.get("illust", {}).get("data", [])
        else:
            items = []
        return items[start:end]

    def get_illust_info(self, illust_id):
        url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
        r = self.session.get(url, headers=self._headers())
        return r.json()["body"]

    def download(self, illust_id):
        info = self.get_illust_info(illust_id)
        page_count = info.get("pageCount", 1)
        saved_paths = []

        # 下载时使用 Referer + Cookie（图片 CDN 不走 OAuth）
        img_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.pixiv.net/",
        }
        if self.cookie:
            img_headers["Cookie"] = self.cookie

        for p in range(page_count):
            img_url = info["urls"]["original"].replace("_p0", f"_p{p}")
            proxy_url = img_url.replace("i.pximg.net", "i.pixiv.re")

            os.makedirs(self.download_dir, exist_ok=True)
            filename = os.path.basename(img_url)
            save_path = os.path.join(self.download_dir, filename)

            try:
                resp = requests.get(
                    proxy_url,
                    headers=img_headers,
                    timeout=40
                )
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

    # 搜索命令
    search_parser = subparsers.add_parser("search", help="搜索插画")
    search_parser.add_argument("--keywords", required=True, help="搜索关键词")
    search_parser.add_argument("--page", type=int, default=1, help="页码，默认1")
    search_parser.add_argument("--limit", type=int, help="单次获取上限，默认读取config")

    # 下载命令
    download_parser = subparsers.add_parser("download", help="下载插画（支持多页）")
    download_parser.add_argument("--id", required=True, help="作品ID")

    # 配置信息
    info_parser = subparsers.add_parser("info", help="查看当前配置信息")

    # 登录命令
    login_parser = subparsers.add_parser("login", help="通过用户名密码登录获取 refresh_token")
    login_parser.add_argument("--username", required=True, help="Pixiv 用户名/邮箱")
    login_parser.add_argument("--password", required=True, help="Pixiv 密码")

    args = parser.parse_args()
    client = PixivClient()

    if args.cmd == "search":
        items = client.search(args.keywords, page=args.page, limit=args.limit)
        print(f"[搜索] {args.keywords} | 第{args.page}页\n")

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

    elif args.cmd == "info":
        print("=" * 50)
        print(f"[下载目录] {client.download_dir}")
        print(f"[默认搜索限制] {client.default_limit} 条")
        print(f"[Cookie状态] 已配置" if client.cookie else "[Cookie状态] 未配置")
        print(f"[OAuth] refresh_token: {'已配置' if client.refresh_token else '未配置'}")
        print(f"[OAuth] access_token: {'已配置' if client.access_token else '未配置'}")
        print("=" * 50)

    elif args.cmd == "login":
        print("[登录] 正在获取 refresh_token...")
        try:
            resp = requests.post(PIXIV_AUTH_URL, data={
                "client_id": PIXIV_CLIENT_ID,
                "client_secret": PIXIV_CLIENT_SECRET,
                "grant_type": "password",
                "username": args.username,
                "password": args.password,
                "get_secure_url": 1,
            }, timeout=15)
            data = resp.json()
            if "access_token" in data:
                client.access_token = data["access_token"]
                client.refresh_token = data["refresh_token"]
                client.token_expires_at = time.time() + data.get("expires_in", 3600)
                client._save_auth()
                print(f"[登录成功] access_token 已保存，有效期 {data.get('expires_in', 3600)} 秒")
                print(f"[登录成功] refresh_token 已保存到 config.yaml")
            else:
                print(f"[登录失败] {data}")
        except Exception as e:
            print(f"[登录异常] {e}")

if __name__ == "__main__":
    main()
