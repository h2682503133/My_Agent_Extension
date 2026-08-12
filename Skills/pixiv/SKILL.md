---
name: pixiv
description: Pixiv 搜索、分页、元信息获取、多图下载技能。基于 PHPSESSID Cookie 鉴权，优先缓存元信息，按需下载图片。
---

# Pixiv Skill（Cookie 鉴权）

## 1) 鉴权与配置

使用 PHPSESSID Cookie 鉴权。配置文件 `config.yaml`：

```yaml
pixiv:
  cookie: "PHPSESSID=你的值;"
  download_dir: "./downloads"

search:
  limit: 5
```

### 获取 PHPSESSID
1. 浏览器打开 https://www.pixiv.net 并登录
2. F12 → Application → Cookies → pixiv.net
3. 复制 PHPSESSID 的值
4. 运行：`python3 scripts/pixiv.py cookie --set "值"`

---

## 2) 命令说明

### 配置 Cookie
```bash
python3 scripts/pixiv.py cookie          # 交互式输入
python3 scripts/pixiv.py cookie --set "PHPSESSID值"
```

### 查看配置
```bash
python3 scripts/pixiv.py info
```

### 搜索
```bash
python3 scripts/pixiv.py search --keyword "初音ミク"
```
常用参数：
- `--page` 页码
- `--limit` 单次获取条数（不传入则使用 config 默认值）

输出：ID、标题、作者、分辨率、页数、标签、分级、AI 标记

### 下载
```bash
python3 scripts/pixiv.py download --id 12345678
```
- 多图作品自动下载全部分页 p0/p1/p2…
- 通过 i.pixiv.re 反代下载

---

## 3) 推荐工作流

1. 先跑 `info`，确认 Cookie 已配置
2. 使用 `search` 获取元信息
3. 仅在用户明确要图时再 `download --id 作品ID`

---

## 4) 常见问题

- **搜索无结果**：Cookie 过期，重新获取 PHPSESSID 并更新
- **图片下载失败**：i.pixiv.re 反代可能不稳定，重试即可
- **403**：Cookie 失效，重新登录 Pixiv 获取最新 PHPSESSID
