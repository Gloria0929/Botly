"""共享与私有记忆共用一个注入预算，按更新时间选择最近内容。"""
from .config import MEMORY_INJECT_LIMIT


def memory_context(items, limit=MEMORY_INJECT_LIMIT):
    groups = {"shared": [], "bot": []}
    total = 0
    used = 0
    ordered = sorted(items, key=lambda m: (m.updated_at or m.created_at, m.id), reverse=True)
    for item in ordered:
        piece = f"- {item.title}：{item.content}" if item.title else f"- {item.content}"
        total += len(piece) + 1
        remaining = max(0, limit - used)
        if not remaining:
            continue
        selected = piece[:max(0, remaining - 1)]
        if selected:
            groups[item.scope].append(selected)
            used += len(selected) + 1
    return {"shared": "\n".join(groups["shared"]), "private": "\n".join(groups["bot"]),
            "injected_length": used, "total_length": total, "truncated": total > used}
