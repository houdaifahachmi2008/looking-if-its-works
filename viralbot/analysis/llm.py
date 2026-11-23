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


class ModelNotFound(RuntimeError):
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
            if r.status_code == 404:
                raise ModelNotFound(r.text[:300])
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


_gemini_model_override: str | None = None


def _pick_gemini_model(api_key: str) -> str | None:
    """If the configured model is retired, choose the newest available Flash model."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": api_key}, params={"pageSize": 200}, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not any(x in n for x in ("image", "tts", "audio", "live", "exp"))]
    pool = flash or names
    return sorted(pool, reverse=True)[0] if pool else None


def complete_json(prompt: str, purpose: str, system: str = "", temperature: float = 0.4) -> dict:
    global _gemini_model_override
    e = env()
    provider = e.active_llm
    if provider == "gemini":
        model = _gemini_model_override or e.gemini_model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        headers = {"x-goog-api-key": e.gemini_api_key, "Content-Type": "application/json"}
        try:
            data = _post(url, headers, body)
        except ModelNotFound:
            alt = _pick_gemini_model(e.gemini_api_key)
            if not alt or alt == model:
                raise
            log.warning("Gemini model '%s' unavailable – switching to '%s' (set GEMINI_MODEL to make it permanent)", model, alt)
            _gemini_model_override = model = alt
            data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers, body)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as ex:
            raise RuntimeError(f"unexpected Gemini response: {str(data)[:300]}") from ex
        usage = data.get("usageMetadata", {})
        tin, tout = usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
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
    elif provider == "ollama":
        # Local model via Ollama (https://ollama.com): free, private, no API key.
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        try:
            r = requests.post(f"{e.ollama_host}/api/chat", timeout=600, json={
                "model": e.ollama_model, "messages": msgs, "stream": False, "format": "json",
                "options": {"temperature": temperature, "num_ctx": 8192}})
            r.raise_for_status()
        except requests.RequestException as ex:
            raise RuntimeError(f"Ollama not reachable at {e.ollama_host} ({ex}). Is 'ollama serve' running and "
                               f"is the model pulled (ollama pull {e.ollama_model})?") from ex
        data = r.json()
        text = data.get("message", {}).get("content", "")
        tin, tout = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        model = e.ollama_model
        db.record_cost(provider, model, purpose, tin + tout, "tokens", 0.0)
        return _extract_json(text)
    else:
        raise LLMUnavailable("no LLM provider configured")
    db.record_cost(provider, model, purpose, tin + tout, "tokens", _cost(tin, tout))
    return _extract_json(text)
