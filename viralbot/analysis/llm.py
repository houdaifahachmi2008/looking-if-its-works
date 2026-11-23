"""Minimal Gemini / OpenAI JSON clients (plain REST – no heavy SDKs), with
retry/backoff and per-call cost accounting."""
from __future__ import annotations

import json
import re
import time

import requests

from .. import db
from ..config import env
from ..logging_setup import get_logger

log = get_logger("llm")


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return env().active_llm != "none"


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _post(url: str, headers: dict, body: dict) -> dict:
    last = None
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as ex:
            last = ex
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"LLM request failed: {last}")


def _cost(tokens_in: int, tokens_out: int) -> float:
    e = env()
    return tokens_in / 1e6 * e.llm_price_in + tokens_out / 1e6 * e.llm_price_out


def complete_json(prompt: str, purpose: str, system: str = "", temperature: float = 0.4) -> dict:
    e = env()
    provider = e.active_llm
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{e.gemini_model}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = _post(url, {"x-goog-api-key": e.gemini_api_key, "Content-Type": "application/json"}, body)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as ex:
            raise RuntimeError(f"unexpected Gemini response: {str(data)[:300]}") from ex
        usage = data.get("usageMetadata", {})
        tin, tout = usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        model = e.gemini_model
    elif provider == "openai":
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        data = _post("https://api.openai.com/v1/chat/completions",
                     {"Authorization": f"Bearer {e.openai_api_key}", "Content-Type": "application/json"},
                     {"model": e.openai_model, "messages": msgs, "temperature": temperature,
                      "response_format": {"type": "json_object"}})
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        model = e.openai_model
    else:
        raise LLMUnavailable("no LLM provider configured")
    db.record_cost(provider, model, purpose, tin + tout, "tokens", _cost(tin, tout))
    return _extract_json(text)
