"""模型接入：按「厂商 + API Key」创建，base_url 预置，模型自动探测。

设置页只负责登记厂商与 Key；具体用哪个模型，在 Bot 里选。
保存时会真实探测一次首个模型的流式输出与工具调用能力（MOD-02），
探测结果按模型写回 `models`，供 Bot / 群聊选模型时提示或拦截。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..capabilities import CAPABILITY_KEYS, apply_capability
from ..db import get_db
from ..deps import current_user, owned_or_404
from ..llm import list_models, probe_capabilities
from ..models import Bot, Provider, User, now
from ..security import decrypt_secret, encrypt_secret
from ..serializers import provider_out
from ..vendors import BY_ID, VENDORS, out as vendor_out, preset_price

router = APIRouter(prefix="/providers", tags=["providers"])

# 探测会真实发一次（甚至几次）模型请求：限制单次检测的模型数与并发，
# 避免用户在设置页点一下就把账单点爆。
PROBE_MAX_MODELS = 12
PROBE_CONCURRENCY = 4



class ModelIn(BaseModel):
    name: str
    input_price: float | None = None
    output_price: float | None = None


class ProviderIn(BaseModel):
    vendor_id: str = "custom"
    logo: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    name: str | None = None
    models: list[ModelIn] | None = None
    kind: str = "openai"  # openai | mock


class ProviderPatch(BaseModel):
    name: str | None = None
    logo: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: list[ModelIn] | None = None


class ProbeIn(BaseModel):
    models: list[str] | None = None


def _last4(api_key: str | None) -> str | None:
    """短 Key 不回显，避免把整串 Key 当成“末 4 位”返回给前端。"""
    return api_key[-4:] if api_key and len(api_key) > 4 else None


def _models_payload(
    names: list[str], existing: list[dict] | None = None, vendor_id: str | None = None
) -> list[dict]:
    """保留用户已填的单价与已探测的能力；新模型按厂商参考价预置。

    刷新模型列表时不能把探测结果丢掉，否则每次刷新都要重新检测。
    """
    old_by_name = {m.get("name"): m for m in (existing or [])}
    out = []
    for name in names:
        old = old_by_name.get(name) or {}
        pin = old.get("input_price")
        pout = old.get("output_price")
        if pin is None and pout is None:
            preset = preset_price(vendor_id, name)
            if preset:
                pin, pout = preset
        row = {"name": name, "input_price": pin, "output_price": pout}
        for key in CAPABILITY_KEYS:
            if old.get(key) is not None:
                row[key] = old[key]
        out.append(row)
    return out


async def _probe_many(
    *, base_url: str, api_key: str | None, models: list[str]
) -> list[dict]:
    """并发探测多个模型，返回每项的 {model, ok, message, streaming, tool_calling}。"""
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(name: str) -> dict:
        async with sem:
            caps = await probe_capabilities(base_url=base_url, api_key=api_key, model=name)
            return {
                "model": name,
                **caps,
                "checked_at": now().isoformat(),
            }

    return list(await asyncio.gather(*(one(m) for m in models)))



@router.get("/vendors")
def list_vendors():
    """预置厂商目录，供设置页选择。"""
    return [vendor_out(v) for v in VENDORS]


@router.get("")
def list_providers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Provider).where(Provider.user_id == user.id).order_by(Provider.created_at)
    ).all()
    return [provider_out(p) for p in rows]


@router.post("", status_code=201)
async def create_provider(
    payload: ProviderIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if payload.kind == "mock":
        p = Provider(
            user_id=user.id,
            name=payload.name or "演示模型",
            kind="mock",
            vendor_id="mock",
            logo=payload.logo,
            base_url="mock://local",
            models=[
                {
                    "name": "botly-mock",
                    "input_price": 0,
                    "output_price": 0,
                    # 演示模型本身支持流式与工具调用，直接标记，省一次探测
                    "streaming": True,
                    "tool_calling": True,
                }
            ],
            status="ok",
            last_checked_at=now(),
        )
        db.add(p)
        db.flush()
        return {"provider": provider_out(p), "detected": True, "message": "演示模型已就绪"}

    vendor = BY_ID.get(payload.vendor_id)
    if not vendor:
        raise HTTPException(400, "未知的模型厂商")
    base_url = (payload.base_url or vendor.base_url).strip()
    if not base_url:
        raise HTTPException(400, "请填写 base_url")
    if vendor.id != "custom" and not payload.api_key:
        raise HTTPException(400, "请填写 API Key")

    # 先探测模型；探测失败就退回预置列表，不阻断保存
    detected, names, message = (False, [], "")
    if payload.api_key or vendor.id == "custom":
        detected, names, message = await list_models(
            base_url=base_url, api_key=payload.api_key or None
        )
    if not detected:
        names = [m.name for m in (payload.models or [])] or list(vendor.models)
        if not names:
            raise HTTPException(
                400,
                f"未能自动获取模型（{message}）。请检查 Key 与 base_url，或手动填写模型名。",
            )

    models_payload = _models_payload(
        names, [m.model_dump() for m in (payload.models or [])], vendor.id
    )
    status = "ok" if detected else "unchecked"
    last_checked = now() if detected else None

    # MOD-02：真实探测首个模型（流式 + 工具调用），结果按模型持久化。
    # 不影响保存本身——自建网关的探测限制各不相同，探测失败只标红状态。
    probe_note = ""
    if names:
        caps = await probe_capabilities(
            base_url=base_url, api_key=payload.api_key or None, model=names[0]
        )
        caps["checked_at"] = now().isoformat()
        models_payload = apply_capability(models_payload, names[0], caps)
        status = "ok" if caps["ok"] else "error"
        last_checked = now()
        probe_note = f"；{names[0]}：{caps['message']}"

    p = Provider(
        user_id=user.id,
        name=(payload.name or vendor.name).strip(),
        kind="openai",
        vendor_id=vendor.id,
        logo=payload.logo,
        base_url=base_url,
        api_key_encrypted=encrypt_secret(payload.api_key),
        api_key_last4=_last4(payload.api_key),
        models=models_payload,
        status=status,
        last_checked_at=last_checked,
    )
    db.add(p)
    db.flush()
    base_message = message if detected else f"{message}，已使用预置模型列表"
    return {
        "provider": provider_out(p),
        "detected": detected,
        "message": base_message + probe_note,
    }


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: str,
    payload: ProviderPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = owned_or_404(db, Provider, provider_id, user.id)
    if payload.name is not None:
        p.name = payload.name
    if payload.logo is not None:
        p.logo = payload.logo or None
    if payload.base_url is not None and payload.base_url != p.base_url:
        p.base_url = payload.base_url
        p.status = "unchecked"
    if payload.api_key:
        p.api_key_encrypted = encrypt_secret(payload.api_key)
        p.api_key_last4 = _last4(payload.api_key)
        p.status = "unchecked"
    if payload.models is not None:
        # 前端只回传 name 与单价；能力与探测时间按名字保留，否则改价格就丢能力
        prev = {m.get("name"): m for m in (p.models or [])}
        rows = []
        for m in payload.models:
            row = {
                "name": m.name,
                "input_price": m.input_price,
                "output_price": m.output_price,
            }
            old = prev.get(m.name) or {}
            for key in CAPABILITY_KEYS:
                if old.get(key) is not None:
                    row[key] = old[key]
            rows.append(row)
        p.models = rows
    db.flush()
    return provider_out(p)


@router.delete("/{provider_id}")
def delete_provider(
    provider_id: str,
    force: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = owned_or_404(db, Provider, provider_id, user.id)
    in_use = db.scalars(
        select(Bot).where(Bot.provider_id == p.id, Bot.deleted_at.is_(None))
    ).all()
    if in_use and not force:
        names = "、".join(b.name for b in in_use)
        raise HTTPException(
            409, f"以下 Bot 正在使用该厂商：{names}。请先改绑，或确认将这些 Bot 置为不可用。"
        )
    for b in in_use:
        b.provider_id = None
        b.model = None
    db.delete(p)
    db.flush()
    return {"ok": True}


@router.post("/{provider_id}/refresh")
async def refresh_models(
    provider_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """重新拉取模型列表（换 Key、厂商上新模型时用）。"""
    p = owned_or_404(db, Provider, provider_id, user.id)
    if p.kind == "mock":
        return {"provider": provider_out(p), "detected": True, "message": "演示模型无需刷新"}
    key = decrypt_secret(p.api_key_encrypted)
    detected, names, message = await list_models(base_url=p.base_url, api_key=key)
    if detected:
        p.models = _models_payload(names, p.models or [], p.vendor_id)
        p.status = "ok"
    else:
        p.status = "error"
    p.last_checked_at = now()
    db.flush()
    return {"provider": provider_out(p), "detected": detected, "message": message}


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """真实发一次请求检测首个模型，并把结果写回该模型（MOD-02）。

    之前的实现只提示、不持久化，选模型的地方拿不到能力信息；现在检测完
    之后 Bot / 群聊页会直接按这份结果提示或拦截。
    """
    p = owned_or_404(db, Provider, provider_id, user.id)
    if p.kind == "mock":
        p.status = "ok"
        p.last_checked_at = now()
        db.flush()
        return {
            "ok": True,
            "message": "演示模型可用",
            "model": "botly-mock",
            "streaming": True,
            "tool_calling": True,
            "provider": provider_out(p),
        }
    model = (p.models or [{}])[0].get("name")
    if not model:
        raise HTTPException(400, "接入信息不完整，请先刷新模型列表")
    key = decrypt_secret(p.api_key_encrypted)
    caps = await probe_capabilities(base_url=p.base_url, api_key=key, model=model)
    caps["checked_at"] = now().isoformat()
    p.models = apply_capability(p.models or [], model, caps)
    p.status = "ok" if caps["ok"] else "error"
    p.last_checked_at = now()
    db.flush()
    return {
        "ok": caps["ok"],
        "message": caps["message"],
        "model": model,
        "streaming": caps["streaming"],
        "tool_calling": caps["tool_calling"],
        "provider": provider_out(p),
    }


@router.post("/{provider_id}/probe")
async def probe_models(
    provider_id: str,
    payload: ProbeIn | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """批量检测模型能力（MOD-02）：不传 models 时检测前 12 个模型。

    一次检测会按每个模型发真实请求，所以有上限；结果按模型持久化。
    """
    p = owned_or_404(db, Provider, provider_id, user.id)
    if p.kind == "mock":
        return {"results": [], "provider": provider_out(p), "message": "演示模型无需检测"}
    known = [m.get("name") for m in (p.models or []) if m.get("name")]
    wanted = (payload.models if payload and payload.models else None) or known
    targets = [n for n in wanted if n in known][:PROBE_MAX_MODELS]
    if not targets:
        raise HTTPException(400, "该厂商还没有模型，请先刷新模型列表")

    key = decrypt_secret(p.api_key_encrypted)
    results = await _probe_many(base_url=p.base_url, api_key=key, models=targets)

    models = p.models or []
    for caps in results:
        models = apply_capability(models, caps["model"], caps)
    p.models = models
    p.status = "ok" if any(r["ok"] for r in results) else "error"
    p.last_checked_at = now()
    db.flush()

    bad = [r["model"] for r in results if r["tool_calling"] is False]
    skipped = len(known) - len(results)
    message = f"已检测 {len(results)} 个模型"
    if bad:
        message += f"，{len(bad)} 个不支持工具调用"
    if skipped > 0:
        message += f"；还有 {skipped} 个未检测，可再次点击继续"
    return {"results": results, "provider": provider_out(p), "message": message}

