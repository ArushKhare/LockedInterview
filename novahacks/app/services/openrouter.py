import os
import json
import httpx
from typing import AsyncGenerator, Dict, Any

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def _headers() -> Dict[str, str]:
    hdrs = {
        "Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY','')}",
        "Content-Type": "application/json",
    }
    # Optional attribution (improves analytics/ranking)
    referer = os.environ.get("APP_REFERER")
    title = os.environ.get("APP_TITLE")
    if referer:
        hdrs["HTTP-Referer"] = referer
    if title:
        hdrs["X-Title"] = title
    return hdrs

async def stream_chat_completion(messages: list[Dict[str, Any]], model: str | None = None) -> AsyncGenerator[str, None]:
    """
    Calls OpenRouter with stream=true and yields raw 'delta' content tokens as they arrive.
    You can adapt this to forward SSE frames or NDJSON to the client.
    """
    model = model or os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", OPENROUTER_URL, headers=_headers(), json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # OpenRouter streams "data: {json}" lines and a final "data: [DONE]"
                if line.startswith("data: "):
                    data = line[len("data: "):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        # Extract text deltas if present
                        for choice in obj.get("choices", []):
                            delta = choice.get("delta", {})
                            if "content" in delta and delta["content"] is not None:
                                yield delta["content"]
                    except Exception:
                        # If non-JSON, just forward raw text
                        yield line + "\n"
