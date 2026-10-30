"""模型调用：统一走 OpenAI 兼容接口，附带一个可离线演示的 mock provider。

流式接口约定（供编排层消费）：
    {"type": "delta", "text": "..."}                      增量文本
    {"type": "done", "content": str, "tool_calls": [...],
     "usage": {"input_tokens": int, "output_tokens": int},
     "finish_reason": str}
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator

import httpx

from .config import LLM_MAX_RETRIES, LLM_RETRY_BACKOFF, LLM_TIMEOUT_SECONDS

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 字符约 1 token/字，其余按 4 字符/token。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return max(1, cjk + (len(text) - cjk) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += 4
        total += estimate_tokens(str(m.get("content") or ""))
        total += estimate_tokens(str(m.get("reasoning_content") or ""))
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(tc.get("function", {}), ensure_ascii=False))
    return total


class LLMError(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None, retryable: bool = True):
        super().__init__(message)
        self.retry_after = retry_after
        self.retryable = retryable


# --------------------------------------------------------------------------- #
# OpenAI 兼容客户端
# --------------------------------------------------------------------------- #
async def _stream_openai(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float,
    timeout: float = LLM_TIMEOUT_SECONDS,
) -> AsyncIterator[dict]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    finish_reason = ""
    seen_done = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                if api_key:
                    body = body.replace(api_key, "[密钥已隐藏]")
                body = body[:600]
                delay = None
                if resp.status_code == 429 and resp.headers.get("Retry-After"):
                    header = resp.headers["Retry-After"]
                    try:
                        delay = float(header)
                    except ValueError:
                        try:
                            date = parsedate_to_datetime(header)
                            if date.tzinfo is None:
                                date = date.replace(tzinfo=timezone.utc)
                            delay = (date - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            pass
                    if delay is not None:
                        delay = max(0, delay) if math.isfinite(delay) else None
                raise LLMError(
                    f"模型接口返回 {resp.status_code}: {body}",
                    retry_after=delay,
                    # 4xx（除 429/408）通常是 Key、模型名或参数错误，重试没有意义
                    retryable=resp.status_code == 429
                    or resp.status_code == 408
                    or resp.status_code >= 500,
                )
            async for raw in resp.aiter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    seen_done = True
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = {
                        "input_tokens": chunk["usage"].get("prompt_tokens", 0),
                        "output_tokens": chunk["usage"].get("completion_tokens", 0),
                    }
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(delta["reasoning_content"])
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        yield {"type": "delta", "text": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(
                            idx,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

    content = "".join(content_parts)
    if not finish_reason and not seen_done:
        raise LLMError("模型流式输出在结束标记前中断，请检查模型服务连接")
    reasoning_content = "".join(reasoning_parts)
    tool_calls = [calls[k] for k in sorted(calls)]
    if not content.strip() and not tool_calls:
        raise LLMError("模型接口未返回有效文本或工具调用，请检查流式输出兼容性")
    for tc in tool_calls:
        if not tc["id"]:
            tc["id"] = f"call_{int(time.time() * 1000)}_{tc['function']['name']}"
    if not usage["output_tokens"]:
        usage["output_tokens"] = estimate_tokens(content) + sum(
            estimate_tokens(t["function"]["arguments"]) for t in tool_calls
        )
    if not usage["input_tokens"]:
        usage["input_tokens"] = estimate_messages_tokens(messages)

    yield {
        "type": "done",
        "content": content,
        "reasoning_content": reasoning_content,
        "tool_calls": tool_calls,
        "usage": usage,
        "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
    }


# --------------------------------------------------------------------------- #
# Mock provider：无需真实 Key 即可完整演示（M1/M2 验收用）
# --------------------------------------------------------------------------- #
def _called(messages: list[dict], name: str) -> int:
    n = 0
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if (tc.get("function") or {}).get("name") == name:
                n += 1
    return n


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _mock_decide(messages: list[dict], tools: list[dict], ctx: dict) -> tuple[str, dict | None]:
    names = {t["function"]["name"] for t in (tools or [])}
    role = ctx.get("role", "worker")
    members: list[str] = ctx.get("members") or []
    task = ctx.get("task") or _last_user(messages)
    q = task.strip().replace("\n", " ")[:40]

    if role == "supervisor":
        if "propose_plan" in names and _called(messages, "propose_plan") == 0 and not ctx.get("plan_approved"):
            targets = members or ["研究员", "写手"]
            steps = [
                {
                    "title": f"资料收集：{q}",
                    "bot": targets[0] if targets else "研究员",
                    "expected": "结构化的事实清单与来源",
                },
                {
                    "title": "撰写成稿",
                    "bot": targets[1] if len(targets) > 1 else (targets[0] if targets else "写手"),
                    "expected": "一份可直接交付的完整文档",
                },
            ]
            return "propose_plan", {"steps": steps}
        if "delegate" in names:
            n = _called(messages, "delegate")
            targets = members or ["研究员", "写手"]
            if n < len(targets):
                bot = targets[n]
                if n == 0:
                    instr = f"围绕「{q}」收集 3 条以上可靠信息，整理成要点清单，并写入 research.md。"
                    exp = "要点清单 + research.md"
                else:
                    instr = "读取 research.md，据此写一份结构清晰、约 800 字的完整稿件，写入 report.md。"
                    exp = "report.md"
                return "delegate", {"bot": bot, "instruction": instr, "expected_output": exp}
        return "", None

    # 子 Bot
    if role == "worker":
        writer = "写" in (ctx.get("bot_name") or "")
        if writer:
            # 写手：先读资料，再直接成稿，不重复写 research.md
            if "file_read" in names and _called(messages, "file_read") == 0:
                return "file_read", {"path": "research.md"}
            if "file_write" in names and _called(messages, "file_write") == 0:
                return (
                    "file_write",
                    {
                        "path": "report.md",
                        "content": f"# {q}\n\n## 一、总体判断\n\n该方向已进入工程化竞争阶段，"
                        "单纯的功能堆叠不再构成壁垒。\n\n## 二、关键发现\n\n1. 增长主要来自存量替换，"
                        "而非新增场景。\n2. 接入成本是决策的第一因素。\n3. 稳定性差距正在缩小，"
                        "服务能力成为分水岭。\n\n## 三、建议\n\n优先验证接入路径，再评估长期成本。\n",
                    },
                )
            return "", None

    if "web_search" in names and _called(messages, "web_search") == 0:
        return "web_search", {"query": q}
    if "file_write" in names and _called(messages, "file_write") == 0:
        return (
            "file_write",
            {
                "path": "research.md",
                "content": f"# 资料整理\n\n主题：{q}\n\n1. 该方向近两年增长明显，头部产品差异集中在工程化能力。\n"
                "2. 用户选择的主要因素是接入成本与稳定性。\n3. 价格战已经开始，差异化转向场景深度。\n",
            },
        )
    if "file_write" in names and _called(messages, "file_write") == 1:
        return (
            "file_write",
            {
                "path": "report.md",
                "content": f"# {q}\n\n## 一、总体判断\n\n该方向已进入工程化竞争阶段，单纯的功能堆叠不再构成壁垒。\n\n"
                "## 二、关键发现\n\n1. 增长主要来自存量替换，而非新增场景。\n2. 接入成本是决策的第一因素。\n"
                "3. 稳定性差距正在缩小，服务能力成为分水岭。\n\n## 三、建议\n\n优先验证接入路径，再评估长期成本。\n",
            },
        )
    return "", None


async def _stream_mock(
    *,
    messages: list[dict],
    tools: list[dict] | None,
    ctx: dict,
) -> AsyncIterator[dict]:
    await asyncio.sleep(0.25)
    name, args = _mock_decide(messages, tools or [], ctx)
    if name:
        call = {
            "id": f"call_mock_{int(time.time() * 1000)}_{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }
        yield {
            "type": "done",
            "content": "",
            "tool_calls": [call],
            "usage": {
                "input_tokens": estimate_messages_tokens(messages),
                "output_tokens": estimate_tokens(json.dumps(args, ensure_ascii=False)),
            },
            "finish_reason": "tool_calls",
        }
        return

    role = ctx.get("role", "worker")
    if role == "supervisor":
        text = (
            "已完成本轮拆解与汇总。\n\n"
            "研究员产出了资料要点（research.md），写手据此完成了成稿（report.md）。"
            "两份文件都在右侧文件面板，可直接预览或下载。\n\n"
            "结论：该方向已进入工程化竞争阶段，建议先验证接入成本，再评估长期总拥有成本。"
        )
    elif "写" in (ctx.get("bot_name") or ""):
        text = (
            "稿件已完成并写入 report.md。整体按「总体判断—关键发现—建议」三段组织，"
            "篇幅控制在 800 字左右，可直接交付。"
        )
    else:
        text = (
            "我已完成分配到的部分：先检索了公开资料，把要点整理进 research.md。"
            "关键结论是接入成本与稳定性是当前决策的主要变量。"
        )
    for i in range(0, len(text), 12):
        await asyncio.sleep(0.03)
        yield {"type": "delta", "text": text[i : i + 12]}
    yield {
        "type": "done",
        "content": text,
        "tool_calls": [],
        "usage": {
            "input_tokens": estimate_messages_tokens(messages),
            "output_tokens": estimate_tokens(text),
        },
        "finish_reason": "stop",
    }


# --------------------------------------------------------------------------- #
# 对外入口（含重试）
# --------------------------------------------------------------------------- #
async def stream_chat(
    *,
    kind: str,
    base_url: str,
    api_key: str | None,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.4,
    ctx: dict | None = None,
) -> AsyncIterator[dict]:
    last_err: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        started = False
        try:
            if kind == "mock":
                gen = _stream_mock(messages=messages, tools=tools, ctx=ctx or {})
            else:
                gen = _stream_openai(
                    base_url=base_url,
                    api_key=api_key or "",
                    model=model,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                )
            async for chunk in gen:
                started = True
                yield chunk
            return
        except (LLMError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            last_err = exc
            # 已经开始输出就不再重试，避免重复内容；不可重试的错误直接失败
            if started or attempt >= LLM_MAX_RETRIES:
                break
            if isinstance(exc, LLMError) and not exc.retryable:
                break
            delay = getattr(exc, "retry_after", None)
            await asyncio.sleep(delay if delay is not None else LLM_RETRY_BACKOFF[min(attempt, len(LLM_RETRY_BACKOFF) - 1)])
    detail = str(last_err).strip() if last_err is not None else ""
    if not detail:
        detail = (f"模型请求超时（{LLM_TIMEOUT_SECONDS} 秒）"
                  if isinstance(last_err, (httpx.TimeoutException, asyncio.TimeoutError))
                  else type(last_err).__name__ if last_err is not None else "模型调用失败")
    raise LLMError(detail)


def _redact(text: str, key: str | None) -> str:
    """错误响应里不返回明文 Key。"""
    return text.replace(key, "[密钥已隐藏]") if key else text


# --------------------------------------------------------------------------- #
# 模型能力探测（MOD-02）：连通性 / 流式输出 / 工具调用
#
# 能力值都是三态：True 支持、False 不支持、None 无法判断。只有明确不支持
# 才用于拦截（例如不允许把不支持工具调用的模型设为总指挥）；无法判断时
# 一律放行，避免把自建网关或临时网络故障当成能力缺失。
# --------------------------------------------------------------------------- #
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "测试用工具：原样回传一个整数",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    },
}


def _probe_headers(api_key: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def _probe_streaming(*, base_url: str, api_key: str, model: str) -> tuple[bool | None, str]:
    """流式探测：只关心有没有收到可解析的流式分片。

    不传 max_tokens：o 系列等模型会拒收该参数，会把能力探测误判成不支持。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST", url, json=payload, headers=_probe_headers(api_key)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    return None, f"HTTP {resp.status_code}: {_redact(body[:200], api_key)}"
                async for raw in resp.aiter_lines():
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("choices") is not None:
                        return True, "流式输出正常"
                return False, "服务端没有返回流式分片"
    except Exception as exc:  # noqa: BLE001
        return None, f"请求失败：{exc}"


async def _probe_plain(*, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """普通非流式请求：只用来区分「连不上」和「不支持流式」。"""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload, headers=_probe_headers(api_key))
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {_redact(r.text[:200], api_key)}"
        return True, "连接正常"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败：{exc}"


async def _probe_tools(*, base_url: str, api_key: str, model: str) -> tuple[bool | None, str]:
    """工具调用探测：要求模型调用 ping，看返回里有没有 tool_calls。

    先强制 tool_choice（「接受 tools 参数但从不调用」和「不支持工具」很难
    区分，强制调用能让后者更明确）；个别网关不接受强制选择，再退回 auto。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    choices: list[object] = [
        {"type": "function", "function": {"name": "ping"}},
        "auto",
    ]
    last = "未发起工具调用"
    for tool_choice in choices:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "调用 ping 工具，参数 value=1。"}],
            "tools": [_PROBE_TOOL],
            "tool_choice": tool_choice,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(url, json=payload, headers=_probe_headers(api_key))
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}: {_redact(r.text[:200], api_key)}"
                continue
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
            if msg.get("tool_calls"):
                return True, "支持工具调用"
            return False, "模型没有发起工具调用"
        except Exception as exc:  # noqa: BLE001
            last = f"请求失败：{exc}"
    return None, last


async def probe_capabilities(*, base_url: str, api_key: str | None, model: str) -> dict:
    """一次探测某模型的三项能力（MOD-02）。

    返回 {"ok", "message", "streaming", "tool_calling"}；ok 只表示能连上并
    拿到正常响应，两项能力各自可能是 None（无法判断）。
    """
    streaming, smsg = await _probe_streaming(
        base_url=base_url, api_key=api_key or "", model=model
    )
    if streaming is True:
        tool_calling, tmsg = await _probe_tools(
            base_url=base_url, api_key=api_key or "", model=model
        )
        if tool_calling is True:
            message = "连接正常，支持流式输出与工具调用"
        elif tool_calling is False:
            message = f"连接正常，支持流式输出，但{tmsg}"
        else:
            message = f"连接正常，支持流式输出；工具调用无法确认（{tmsg}）"
        return {"ok": True, "message": message, "streaming": True, "tool_calling": tool_calling}

    # 流式没确认：再用一次非流式请求，判断是连不上还是确实不支持流式
    reachable, pmsg = await _probe_plain(
        base_url=base_url, api_key=api_key or "", model=model
    )
    if not reachable:
        return {"ok": False, "message": pmsg, "streaming": streaming, "tool_calling": None}
    if streaming is False:
        message = "连接正常，但不支持流式输出；Botly 的执行依赖流式，建议换模型"
    else:
        message = f"连接正常，但流式探测失败（{smsg}）"
    return {"ok": True, "message": message, "streaming": streaming, "tool_calling": None}


async def list_models(*, base_url: str, api_key: str | None) -> tuple[bool, list[str], str]:
    """自动探测可用模型：GET {base_url}/models。

    返回 (ok, models, message)。多数 OpenAI 兼容服务都实现了该接口；
    少数网关没有，此时调用方退回预置模型列表即可，不算错误。
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers)
        if r.status_code >= 400:
            return False, [], f"HTTP {r.status_code}：{_redact(r.text[:160], api_key)}"
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return False, [], "返回格式不是模型列表"
        names: list[str] = []
        for it in items:
            if isinstance(it, str):
                names.append(it)
            elif isinstance(it, dict):
                name = it.get("id") or it.get("name") or it.get("model")
                if name:
                    names.append(str(name))
        # 去重并排序，把对话类模型排前面
        seen: set[str] = set()
        uniq = [n for n in names if not (n in seen or seen.add(n))]
        chat_like = [n for n in uniq if not any(
            k in n.lower() for k in ("embed", "whisper", "tts", "dall-e", "moderation", "rerank")
        )]
        return True, sorted(chat_like or uniq), f"已获取 {len(chat_like or uniq)} 个模型"
    except Exception as exc:  # noqa: BLE001
        return False, [], f"探测失败：{exc}"
