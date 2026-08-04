---
name: read-agent-identity
description: 读取指定智能体的 IDENTITY.md 内容，用于了解其他智能体的角色和设定。
metadata: {"clawdbot":{"emoji":"🪪"}}
---

# read-agent-identity

读取任意智能体的 IDENTITY.md 文件。

## 调用方式

```
read-agent-identity|agent_id:main
```

- `agent_id`：目标智能体 ID，如 `main`、`tool`、`alice` 等

## 返回

- 若该智能体存在 `IDENTITY.md`，则返回其完整内容
- 若不存在，则提示"该智能体没有 IDENTITY.md"
