const files = new Set(["baichuan-color.svg", "claude-color.svg", "cohere-color.svg", "deepseek-color.svg", "doubao-color.svg", "fireworks-color.svg", "gemini-color.svg", "groq.svg", "hunyuan-color.svg", "kimi-color.svg", "minimax-color.svg", "mistral-color.svg", "openai.svg", "openrouter-color.svg", "perplexity-color.svg", "qwen-color.svg", "siliconcloud-color.svg", "spark-color.svg", "stepfun-color.svg", "together-color.svg", "wenxin-color.svg", "xai.svg", "yi-color.svg", "zhipu-color.svg"]);

export function resolveVendorIcon(value: string): string {
  if (!value.includes("@lobehub/icons-static-svg") && !value.includes("icons.lobehub.com")) return value;
  try {
    const name = new URL(value).pathname.split("/").pop() || "";
    return files.has(name) ? `/vendor-icons/${name}` : value;
  } catch { return value; }
}
