"""编排层共用的控制流信号。

单独放一个模块，避免 orchestrator 与 graph 互相 import。
"""
from __future__ import annotations


class PauseSignal(Exception):
    """暂停点：计划确认 / 提问 / 审批 / 逐步确认。

    payload 会原样落进 `runs.resume_payload`，同时作为 LangGraph `interrupt()` 的值。
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("kind", "pause"))


class StopSignal(Exception):
    pass

class LeaseLost(Exception):
    pass

class AwaitChildren(Exception):
    """总指挥的并行子任务还没全部返回：图停在 tool 节点，执行转入 waiting_children。"""

class LimitExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
