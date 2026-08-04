# pixiv

Pixiv 搜索下载技能（Cookie 鉴权模式）。

## 目录结构

- `SKILL.md`：AI 技能说明
- `scripts/pixiv.py`：主入口（搜索、下载、Cookie 配置）
- `config.yaml`：配置文件（含 PHPSESSID）
- `config.example.yaml`：配置模板
- `process.md`：开发记录

## 快速开始

1. 登录 pixiv.net，从浏览器 DevTools 复制 PHPSESSID
2. 设置 Cookie：
   ```bash
   python3 scripts/pixiv.py cookie --set "你的PHPSESSID值"
   ```
3. 搜索：
   ```bash
   python3 scripts/pixiv.py search --keyword "初音ミク"
   ```

## 常用命令

```bash
python3 scripts/pixiv.py info
python3 scripts/pixiv.py cookie
python3 scripts/pixiv.py search --keyword "初音ミク" --limit 5
python3 scripts/pixiv.py download --id 12345678
```
