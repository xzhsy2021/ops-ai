from __future__ import annotations

import os
from typing import Any, Dict, List

PROMPTS_DIR = os.path.dirname(__file__)

PROMPT_META: Dict[str, Dict[str, str]] = {
    "release_plan": {"title": "发布计划", "category": "deploy", "description": "分析发布请求并生成结构化的发布计划"},
    "diagnostic_triage": {"title": "诊断分流", "category": "diagnostics", "description": "分析系统状态并识别问题"},
    "db_workflow": {"title": "数据库工作流", "category": "database", "description": "引导安全的数据库操作流程"},
}


def prompt_registry() -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = []
    for name, meta in PROMPT_META.items():
        path = os.path.join(PROMPTS_DIR, f"{name}.md")
        exists = os.path.isfile(path)
        prompts.append({
            "name": name,
            "title": meta["title"],
            "category": meta["category"],
            "description": meta["description"],
            "path": path,
            "available": exists,
        })
    return prompts


def get_prompt_content(name: str) -> str | None:
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()