"""预置模型厂商目录。

设置页只需要用户「选厂商 + 填 API Key」：base_url 由厂商预置，模型列表由
`GET {base_url}/models` 自动探测；探测失败时退回这里的预置模型名。

logo 使用 LobeHub Icons 的静态 SVG（https://icons.lobehub.com），
彩色版走 `-color.svg`，单色版走 `.svg`（靠前端 CSS 适配深浅主题）。
CDN 不可达时前端回退到文字标识，color 仍作为回退底色/品牌色使用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# LobeHub Icons 静态 SVG：阿里云 npmmirror 镜像在国内访问更快。
# 版本固定，避免上游改名导致 404；升级图标时只改这里。
LOBE_ICONS_VERSION = "1.101.0"
LOBE_ICONS_BASE = (
    "https://registry.npmmirror.com/@lobehub/icons-static-svg/"
    f"{LOBE_ICONS_VERSION}/files/icons"
)


def lobe_icon(slug: str, color: bool = True) -> str:
    """厂商图标直链。color=False 用单色版，深色主题下由 CSS 反色。"""
    suffix = "-color" if color else ""
    return f"{LOBE_ICONS_BASE}/{slug}{suffix}.svg"


@dataclass
class Vendor:
    id: str
    name: str
    base_url: str
    logo: str
    color: str
    models: list[str] = field(default_factory=list)
    note: str = ""
    key_hint: str = "sk-..."
    docs: str = ""
    requires_endpoint: bool = False
    region: str = "intl"  # intl | cn


VENDORS: list[Vendor] = [
    # ---------------- 国内 ----------------
    Vendor(
        id="deepseek",
        name="DeepSeek 深度求索",
        base_url="https://api.deepseek.com/v1",
        logo=lobe_icon("deepseek"),
        color="#4D6BFE",
        region="cn",
        models=["deepseek-chat", "deepseek-reasoner"],
        note="工具调用稳定、价格低，适合总指挥与子 Bot",
        docs="https://platform.deepseek.com/api_keys",
    ),
    Vendor(
        id="moonshot",
        name="月之暗面 Kimi",
        base_url="https://api.moonshot.cn/v1",
        logo=lobe_icon("kimi"),
        color="#2B2B2B",
        region="cn",
        models=["kimi-k2-0905-preview", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        note="长上下文，中文写作稳定",
        docs="https://platform.moonshot.cn/console/api-keys",
    ),
    Vendor(
        id="zhipu",
        name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        logo=lobe_icon("zhipu"),
        color="#3859FF",
        region="cn",
        models=["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
        note="glm-4-flash 有免费额度，适合先跑通流程",
        key_hint="xxxx.xxxx",
        docs="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    Vendor(
        id="dashscope",
        name="阿里云百炼（通义千问）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        logo=lobe_icon("qwen"),
        color="#615CED",
        region="cn",
        models=["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
        note="compatible-mode 接口，模型名直接用 qwen-*",
        docs="https://bailian.console.aliyun.com/",
    ),
    Vendor(
        id="volcengine",
        name="火山方舟（豆包）",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        logo=lobe_icon("doubao"),
        color="#1664FF",
        region="cn",
        models=["doubao-pro-32k", "doubao-pro-128k", "doubao-lite-32k"],
        note="模型名需填「接入点 ID」（ep- 开头），不是展示名",
        docs="https://console.volcengine.com/ark",
        requires_endpoint=True,
    ),
    Vendor(
        id="hunyuan",
        name="腾讯混元",
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        logo=lobe_icon("hunyuan"),
        color="#0052D9",
        region="cn",
        models=["hunyuan-turbos-latest", "hunyuan-pro", "hunyuan-standard"],
        note="OpenAI 兼容接口，需在控制台开通",
        docs="https://console.cloud.tencent.com/hunyuan",
    ),
    Vendor(
        id="qianfan",
        name="百度千帆（文心）",
        base_url="https://qianfan.baidubce.com/v2",
        logo=lobe_icon("wenxin"),
        color="#2932E1",
        region="cn",
        models=["ernie-4.5-turbo-128k", "ernie-4.0-8k", "ernie-speed-128k"],
        note="v2 接口兼容 OpenAI 格式",
        docs="https://console.bce.baidu.com/qianfan",
    ),
    Vendor(
        id="minimax",
        name="MiniMax",
        base_url="https://api.minimaxi.com/v1",
        logo=lobe_icon("minimax"),
        color="#FF5A1F",
        region="cn",
        models=["MiniMax-M1", "abab6.5s-chat", "abab6.5g-chat"],
        note="长文本与语音场景见长",
        docs="https://platform.minimaxi.com/",
    ),
    Vendor(
        id="stepfun",
        name="阶跃星辰 StepFun",
        base_url="https://api.stepfun.com/v1",
        logo=lobe_icon("stepfun"),
        color="#16A34A",
        region="cn",
        models=["step-2-16k", "step-1v-8k", "step-1-flash"],
        note="多模态能力较强",
        docs="https://platform.stepfun.com/",
    ),
    Vendor(
        id="lingyi",
        name="零一万物",
        base_url="https://api.lingyiwanwu.com/v1",
        logo=lobe_icon("yi"),
        color="#0EA5E9",
        region="cn",
        models=["yi-large", "yi-medium", "yi-lightning"],
        note="中英双语，价格适中",
        docs="https://platform.lingyiwanwu.com/",
    ),
    Vendor(
        id="baichuan",
        name="百川智能",
        base_url="https://api.baichuan-ai.com/v1",
        logo=lobe_icon("baichuan"),
        color="#E11D48",
        region="cn",
        models=["Baichuan4", "Baichuan3-Turbo"],
        note="中文医疗、法律场景有专门模型",
        docs="https://platform.baichuan-ai.com/",
    ),
    Vendor(
        id="spark",
        name="讯飞星火",
        base_url="https://spark-api-open.xf-yun.com/v1",
        logo=lobe_icon("spark"),
        color="#1E88E5",
        region="cn",
        models=["generalv3.5", "4.0Ultra", "generalv3"],
        note="注意模型名与版本号的对应关系",
        docs="https://console.xfyun.cn/",
    ),
    Vendor(
        id="siliconflow",
        name="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        logo=lobe_icon("siliconcloud"),
        color="#7C3AED",
        region="cn",
        models=["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct"],
        note="一个 Key 可调多家开源模型",
        docs="https://cloud.siliconflow.cn/account/ak",
    ),
    # ---------------- 海外 ----------------
    Vendor(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        logo=lobe_icon("openai", color=False),
        color="#10A37F",
        region="intl",
        models=["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"],
        note="兼容性最好；o 系列部分模型不支持工具调用",
        docs="https://platform.openai.com/api-keys",
    ),
    Vendor(
        id="anthropic",
        name="Anthropic Claude",
        base_url="https://api.anthropic.com/v1",
        logo=lobe_icon("claude"),
        color="#D97757",
        region="intl",
        models=["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
        note="官方提供 OpenAI SDK 兼容层，模型名以 /models 返回为准",
        key_hint="sk-ant-...",
        docs="https://console.anthropic.com/settings/keys",
    ),
    Vendor(
        id="google",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        logo=lobe_icon("gemini"),
        color="#4285F4",
        region="intl",
        models=["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        note="用 OpenAI 兼容端点，Key 直接放 Authorization",
        key_hint="AIza...",
        docs="https://aistudio.google.com/apikey",
    ),
    Vendor(
        id="xai",
        name="xAI Grok",
        base_url="https://api.x.ai/v1",
        logo=lobe_icon("xai", color=False),
        color="#111827",
        region="intl",
        models=["grok-4", "grok-3", "grok-3-mini"],
        note="兼容 OpenAI 格式",
        key_hint="xai-...",
        docs="https://console.x.ai/",
    ),
    Vendor(
        id="mistral",
        name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        logo=lobe_icon("mistral"),
        color="#FA520F",
        region="intl",
        models=["mistral-large-latest", "mistral-small-latest", "open-mistral-nemo"],
        note="欧洲厂商，工具调用支持完整",
        docs="https://console.mistral.ai/api-keys",
    ),
    Vendor(
        id="cohere",
        name="Cohere",
        base_url="https://api.cohere.ai/compatibility/v1",
        logo=lobe_icon("cohere"),
        color="#39594D",
        region="intl",
        models=["command-r-plus", "command-r", "command-light"],
        note="使用 compatibility 端点",
        docs="https://dashboard.cohere.com/api-keys",
    ),
    Vendor(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        logo=lobe_icon("groq", color=False),
        color="#F55036",
        region="intl",
        models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        note="推理速度极快，适合做子 Bot",
        key_hint="gsk_...",
        docs="https://console.groq.com/keys",
    ),
    Vendor(
        id="together",
        name="Together AI",
        base_url="https://api.together.xyz/v1",
        logo=lobe_icon("together"),
        color="#0F6FFF",
        region="intl",
        models=["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
        note="开源模型托管，按 token 计费",
        docs="https://api.together.ai/settings/api-keys",
    ),
    Vendor(
        id="fireworks",
        name="Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        logo=lobe_icon("fireworks"),
        color="#5019C5",
        region="intl",
        models=["accounts/fireworks/models/llama-v3p3-70b-instruct"],
        note="模型名带 accounts/... 前缀",
        docs="https://fireworks.ai/account/api-keys",
    ),
    Vendor(
        id="perplexity",
        name="Perplexity",
        base_url="https://api.perplexity.ai",
        logo=lobe_icon("perplexity"),
        color="#20808D",
        region="intl",
        models=["sonar-pro", "sonar", "sonar-reasoning"],
        note="自带联网检索，适合做调研类子 Bot",
        key_hint="pplx-...",
        docs="https://www.perplexity.ai/settings/api",
    ),
    Vendor(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        logo=lobe_icon("openrouter"),
        color="#6467F2",
        region="intl",
        models=["openai/gpt-4o-mini", "anthropic/claude-sonnet-4", "google/gemini-2.5-flash"],
        note="一个 Key 聚合多家模型，按量计费",
        key_hint="sk-or-...",
        docs="https://openrouter.ai/keys",
    ),
    # ---------------- 其它 ----------------
    Vendor(
        id="custom",
        name="自定义 / 自建网关",
        base_url="",
        logo="＋",
        color="#6B7280",
        models=[],
        note="任何 OpenAI 兼容接口，需要自己填 base_url 与模型名",
        key_hint="可留空",
    ),
]

BY_ID = {v.id: v for v in VENDORS}

# 参考单价：USD / 百万 token（输入，输出）。
# 厂商会随时调价，这里的值只用于「添加厂商后立刻能看到费用估算」；
# 用户可以在「设置 → 已添加的厂商 → 价格」里逐项修改。
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-ai/deepseek-v3": (0.27, 1.10),
    "deepseek-ai/deepseek-r1": (0.55, 2.19),
    "kimi-k2-0905-preview": (0.60, 2.50),
    "moonshot-v1-128k": (0.84, 0.84),
    "moonshot-v1-32k": (0.34, 0.34),
    "moonshot-v1-8k": (0.17, 0.17),
    "glm-4-plus": (0.70, 0.70),
    "glm-4-air": (0.10, 0.10),
    "glm-4-flash": (0.0, 0.0),
    "glm-4-long": (0.14, 0.14),
    "qwen-max": (0.40, 1.20),
    "qwen-plus": (0.11, 0.28),
    "qwen-turbo": (0.05, 0.10),
    "qwen-long": (0.07, 0.28),
    "qwen/qwen2.5-72b-instruct": (0.40, 0.40),
    "qwen/qwen2.5-72b-instruct-turbo": (0.90, 0.90),
    "doubao-pro-32k": (0.11, 0.28),
    "doubao-pro-128k": (0.20, 0.50),
    "doubao-lite-32k": (0.04, 0.08),
    "hunyuan-turbos-latest": (0.10, 0.10),
    "hunyuan-pro": (0.14, 0.14),
    "hunyuan-standard": (0.07, 0.07),
    "ernie-4.5-turbo-128k": (0.11, 0.28),
    "ernie-4.0-8k": (0.11, 0.28),
    "ernie-speed-128k": (0.0, 0.0),
    "minimax-m1": (0.40, 1.60),
    "abab6.5s-chat": (0.14, 0.14),
    "abab6.5g-chat": (0.14, 0.14),
    "step-2-16k": (0.50, 1.50),
    "step-1v-8k": (0.20, 0.60),
    "step-1-flash": (0.05, 0.20),
    "yi-large": (0.50, 0.50),
    "yi-medium": (0.20, 0.20),
    "yi-lightning": (0.10, 0.10),
    "baichuan4": (0.50, 0.50),
    "baichuan3-turbo": (0.20, 0.20),
    "generalv3.5": (0.20, 0.20),
    "4.0ultra": (0.50, 0.50),
    "generalv3": (0.20, 0.20),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o3-mini": (1.10, 4.40),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-1": (15.00, 75.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "grok-4": (3.00, 15.00),
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
    "mistral-large-latest": (2.00, 6.00),
    "mistral-small-latest": (0.20, 0.60),
    "open-mistral-nemo": (0.15, 0.15),
    "command-r-plus": (2.50, 10.00),
    "command-r": (0.15, 0.60),
    "command-light": (0.30, 0.30),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "mixtral-8x7b-32768": (0.24, 0.24),
    "meta-llama/llama-3.3-70b-instruct-turbo": (0.88, 0.88),
    "accounts/fireworks/models/llama-v3p3-70b-instruct": (0.90, 0.90),
    "sonar-pro": (3.00, 15.00),
    "sonar": (1.00, 1.00),
    "sonar-reasoning": (1.00, 5.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "google/gemini-2.5-flash": (0.30, 2.50),
}

# 探测到的模型名不在上表时，用厂商的兜底参考价，避免费用一律显示 0。
VENDOR_DEFAULT_PRICE: dict[str, tuple[float, float]] = {
    "deepseek": (0.27, 1.10),
    "moonshot": (0.34, 0.34),
    "zhipu": (0.14, 0.14),
    "dashscope": (0.11, 0.28),
    "volcengine": (0.11, 0.28),
    "hunyuan": (0.10, 0.10),
    "qianfan": (0.11, 0.28),
    "minimax": (0.40, 1.60),
    "stepfun": (0.50, 1.50),
    "lingyi": (0.20, 0.20),
    "baichuan": (0.50, 0.50),
    "spark": (0.20, 0.20),
    "siliconflow": (0.27, 1.10),
    "openai": (2.50, 10.00),
    "anthropic": (3.00, 15.00),
    "google": (1.25, 10.00),
    "xai": (3.00, 15.00),
    "mistral": (0.20, 0.60),
    "cohere": (0.15, 0.60),
    "groq": (0.59, 0.79),
    "together": (0.90, 0.90),
    "fireworks": (0.90, 0.90),
    "perplexity": (1.00, 1.00),
    "openrouter": (1.00, 3.00),
}


def preset_price(vendor_id: str | None, model: str) -> tuple[float, float] | None:
    """按模型名查参考单价；支持版本后缀（如 glm-4-flash-250414）。"""
    key = (model or "").strip().lower()
    if not key:
        return None
    if key in MODEL_PRICES:
        return MODEL_PRICES[key]
    for name in sorted(MODEL_PRICES, key=len, reverse=True):
        if key.startswith(name):
            return MODEL_PRICES[name]
    return VENDOR_DEFAULT_PRICE.get(vendor_id or "")


def out(v: Vendor) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "base_url": v.base_url,
        "logo": v.logo,
        "color": v.color,
        "region": v.region,
        "models": v.models,
        "note": v.note,
        "key_hint": v.key_hint,
        "docs": v.docs,
        "requires_endpoint": v.requires_endpoint,
    }


def guess_vendor(base_url: str | None) -> str:
    if not base_url:
        return "custom"
    url = base_url.lower()
    for v in VENDORS:
        if v.base_url and v.base_url.lower().rstrip("/") in url:
            return v.id
    return "custom"
