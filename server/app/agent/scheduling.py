"""Agent 判断任务依赖，执行器据此生成受并发容量约束的执行批次。"""
from __future__ import annotations

from app.core.config import MAX_PARALLEL_SUBTASKS


def schedule_plan(steps: list[dict]) -> list[dict]:
    if not any("depends_on" in step for step in steps):
        # 兼容已保存的旧计划；大批次分成多个并行批次，不再整体退回串行。
        groups: dict[int, list[dict]] = {}
        for index, step in enumerate(steps):
            batch = step.get("batch")
            key = batch if type(batch) is int and batch > 0 else -(index + 1)
            groups.setdefault(key, []).append(step)
        if all(len(group) <= MAX_PARALLEL_SUBTASKS for group in groups.values()):
            return steps
        result = []
        batch = 1
        for group in groups.values():
            for offset in range(0, len(group), MAX_PARALLEL_SUBTASKS):
                result.extend({**step, "batch": batch} for step in group[offset:offset + MAX_PARALLEL_SUBTASKS])
                batch += 1
        return result

    titles = [(step.get("title") or "").strip() for step in steps]
    if not all(titles) or len(set(titles)) != len(titles):
        raise ValueError("计划步骤标题不能为空或重复，依赖必须引用唯一的步骤标题")
    dependencies = {}
    for title, step in zip(titles, steps):
        deps = step.get("depends_on")
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise ValueError(f"「{title}」必须填写 depends_on 数组；独立任务填写 []")
        deps = list(dict.fromkeys(dep.strip() for dep in deps))
        if title in deps:
            raise ValueError(f"「{title}」不能依赖自己")
        unknown = [dep for dep in deps if dep not in titles]
        if unknown:
            raise ValueError(f"「{title}」引用了不存在的前置步骤：{'、'.join(unknown)}")
        dependencies[title] = deps

    remaining = set(titles)
    result = []
    batch = 1
    by_title = dict(zip(titles, steps))
    while remaining:
        ready = [title for title in titles if title in remaining and not (set(dependencies[title]) & remaining)]
        if not ready:
            raise ValueError("计划存在循环依赖，请重新判断前置关系")
        for offset in range(0, len(ready), MAX_PARALLEL_SUBTASKS):
            for title in ready[offset:offset + MAX_PARALLEL_SUBTASKS]:
                result.append({**by_title[title], "title": title, "depends_on": dependencies[title], "batch": batch})
            batch += 1
        remaining.difference_update(ready)
    return result
