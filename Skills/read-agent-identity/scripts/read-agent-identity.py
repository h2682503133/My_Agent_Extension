#!/usr/bin/env python3
"""读取指定智能体的 IDENTITY.md"""
import sys
from pathlib import Path

SYSTEM_PROMPT_DIR = Path("/app/system_prompts")


def main():
    agent_id = sys.argv[1] if len(sys.argv) > 1 else ""

    if not agent_id:
        print("❌ 请提供 agent_id，例如：read-agent-identity|agent_id:main")
        sys.exit(1)

    identity_path = SYSTEM_PROMPT_DIR/ "orchestrator"/ "system_prompt" / agent_id / "IDENTITY.md"

    if not identity_path.exists():
        print(f"智能体 {agent_id} 没有 IDENTITY.md 文件")
        sys.exit(0)

    content = identity_path.read_text(encoding="utf-8").strip()
    if not content:
        print(f"智能体 {agent_id} 的 IDENTITY.md 为空")
    else:
        print(f"【智能体 {agent_id} 的 IDENTITY.md】\n\n{content}")


if __name__ == "__main__":
    main()
