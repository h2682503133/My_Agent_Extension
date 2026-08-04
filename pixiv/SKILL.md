---
name: pixiv
description: Pixiv 搜索、分页、元信息获取、多图下载、配置查看技能。基于 Cookie 鉴权，不自动登录，优先缓存元信息，按需下载图片。
---

# Pixiv Skill（Cookie 鉴权）

## 1) 鉴权与配置

仅支持 Cookie 鉴权（已移除自动登录流程）。

`config.yaml` 最小示例：

```yaml
pixiv:
  cookie: "PHPSESSID=你的值;"
  proxy: ""
  download_dir: "./downloads"
  r: false
  auto_download: false

search:
  default_limit: 5
```
---

## 2) 脚本职责（给 AI 的快速路由）

### `scripts/pixiv.py`（主入口，优先使用）
- 负责：抓榜、搜索、缓存、下载、互动、监控
- 适用：绝大多数任务

---

## 3) `scripts/pixiv.py` 命令说明

## 3.1 配置与状态查看
```bash
python3 scripts/pixiv.py info
```
- 用途：检查当前是否有可用鉴权状态。

## 3.2 搜索（默认只缓存，不下载）
```bash
python3 scripts/pixiv.py search --keyword "初音ミク"
```
常用参数：
- `--page` 页码
- `--order`: `popular_desc|date_desc|date_asc`
- `--limit`: `单次获取条数（不传入则使用 config 默认）`

输出行为：
- 返回作品元信息：`id、title、分辨率、页数、tags、分级`
- 仅输出信息，不自动下载图片

## 3.3 下载指定作品
```bash
python3 scripts/pixiv.py download --id 12345678
```
- 用途：按作品 ID 下载原图（多图自动 `p0/p1...`）。
- 多图作品自动下载全部分页：p0/p1/p2…

## 4) 推荐工作流（AI 执行顺序）

1. 先跑 `info`，确认鉴权与配置正常。  
2. 使用 `search` 获取元信息（默认不下载）。将会自动输出用户关心字段：`ID、标题、分辨率、标签、页数`。
3. 用 `cache` 输出用户关心字段（标题、作者、直链、热度等）。  
4. 仅在用户明确要图时再：
   - 使用 `download` `--id` `作品ID` 本地下载
  
---

## 5) 常见问题处理

- **403 / 图片直链打不开**：pximg 防盗链，直接外链可能失败。优先本地下载后发送。  
- **搜索无结果或接口异常**：`Cookie` 失效，更新 `config.yaml` 中的 `cookie`。  
- **鉴权失效**：重新登录 Pixiv 获取最新 `PHPSESSID` 并更新配置 
- **多关键词搜索失败**：用""将关键词包裹起来，如"键山雏 东方project"
