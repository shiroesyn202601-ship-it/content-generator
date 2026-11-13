"""Satu antarmuka untuk 3 LLM gratis: Gemini (free tier), Groq (free tier), Ollama (lokal).
Semua pemanggilan meminta output JSON agar bisa langsung dipakai tahap berikutnya."""
import json
import re

import httpx

from .. import config

TIMEOUT = httpx.Timeout(180.0, connect=15.0)


class LLMError(RuntimeError):
    pass


def chat_json(system: str, user: str, provider: str | None = None, temperature: float = 0.8) -> dict:
    provider = (provider or config.LLM_PROVIDER).lower()
    fn = {"gemini": _gemini, "groq": _groq, "ollama": _ollama}.get(provider)
    if not fn:
        raise LLMError(f"Provider LLM tidak dikenal: {provider}")
    raw = fn(system, user, temperature)
    return parse_json(raw)


def parse_json(raw: str) -> dict:
    """Toleran terhadap ```json fences``` atau teks pembuka dari model kecil."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            return json.loads(m.group(0))
        raise LLMError(f"Output LLM bukan JSON valid:\n{raw[:500]}")


def _gemini(system: str, user: str, temperature: float) -> str:
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY belum diisi di .env (gratis di aistudio.google.com)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    r = httpx.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise LLMError(f"Gemini {r.status_code}: {r.text[:400]}")
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise LLMError(f"Respons Gemini tak terduga: {r.text[:400]}")


def _groq(system: str, user: str, temperature: float) -> str:
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY belum diisi di .env (gratis di console.groq.com)")
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json={
            "model": config.GROQ_MODEL,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"Groq {r.status_code}: {r.text[:400]}")
    return r.json()["choices"][0]["message"]["content"]


def _ollama(system: str, user: str, temperature: float) -> str:
    try:
        r = httpx.post(
            f"{config.OLLAMA_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "options": {"temperature": temperature},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
    except httpx.ConnectError:
        raise LLMError("Ollama tidak berjalan. Jalankan `ollama serve` dan `ollama pull <model>`.")
    if r.status_code != 200:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:400]}")
    return r.json()["message"]["content"]
