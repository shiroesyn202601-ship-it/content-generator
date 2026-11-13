"""Satu antarmuka untuk 3 LLM gratis: Gemini (free tier), Groq (free tier), Ollama (lokal).
Semua pemanggilan meminta output JSON agar bisa langsung dipakai tahap berikutnya."""
import json
import re
import time

import httpx

from .. import config
from ..jobs import report

TIMEOUT = httpx.Timeout(180.0, connect=15.0)
PROVIDER_LABEL = {"gemini": "Gemini", "groq": "Groq", "ollama": "Ollama (lokal)"}
STATUS_REASON = {429: "kuota gratis habis", 500: "error di server Google", 503: "server Google sedang sibuk",
                 404: "model sudah tidak tersedia"}


class LLMError(RuntimeError):
    pass


def _configured(provider: str) -> bool:
    """Provider cadangan hanya dicoba kalau memang sudah disiapkan (API key terisi)."""
    return {"gemini": bool(config.GEMINI_API_KEY), "groq": bool(config.GROQ_API_KEY), "ollama": True}.get(provider, False)


def chat_json(system: str, user: str, provider: str | None = None, temperature: float = 0.8) -> dict:
    providers = {"gemini": _gemini, "groq": _groq, "ollama": _ollama}
    primary = (provider or config.LLM_PROVIDER).lower()
    if primary not in providers:
        raise LLMError(f"Provider LLM tidak dikenal: {primary}")
    # Provider utama dulu, lalu cadangan (LLM_FALLBACK_PROVIDERS) yang sudah dikonfigurasi.
    chain = [primary] + [p for p in config.LLM_FALLBACK_PROVIDERS
                         if p != primary and p in providers and _configured(p)]
    errors = []
    for i, name in enumerate(chain):
        if errors:
            report(f"{PROVIDER_LABEL[chain[i - 1]]} gagal → beralih ke cadangan {PROVIDER_LABEL[name]}…")
        try:
            raw = providers[name](system, user, temperature)
        except (LLMError, httpx.HTTPError) as e:
            errors.append(f"[{name}] {e}")
            continue
        if errors:
            print(f"LLM: {primary} gagal, memakai cadangan '{name}'.")
        report(f"Jawaban AI ({PROVIDER_LABEL[name]}) diterima, membaca hasilnya…")
        return parse_json(raw)
    hint = ("" if len(chain) > 1 else
            "\n\nSolusi cepat: isi GROQ_API_KEY di .env (gratis, https://console.groq.com/keys) — "
            "aplikasi otomatis memakai Groq saat Gemini gagal.")
    raise LLMError("\n\n".join(errors) + hint)


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


GEMINI_RETRY_STATUS = {429, 500, 503}  # sibuk / kuota per menit: coba lagi, lalu pindah model
GEMINI_SKIP_STATUS = {404}             # model dipensiunkan / tidak tersedia: langsung pindah model
GEMINI_TIMEOUT = httpx.Timeout(90.0, connect=15.0)  # lebih pendek: model yang menggantung → pindah model


def _gemini(system: str, user: str, temperature: float) -> str:
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY belum diisi di .env (gratis di aistudio.google.com)")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    models = list(dict.fromkeys([config.GEMINI_MODEL, *config.GEMINI_FALLBACK_MODELS]))
    errors = []
    for idx, model in enumerate(models):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = None
        for attempt in range(3):
            tag = "" if attempt == 0 else f" (percobaan {attempt + 1}/3)"
            report(f"Menunggu jawaban AI dari Gemini {model}{tag} — biasanya 10–30 dtk, maks 90 dtk…")
            try:
                r = httpx.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=GEMINI_TIMEOUT)
            except httpx.TimeoutException:
                r = None  # model menggantung (biasanya karena overload) → langsung pindah model
                break
            if r.status_code == 200:
                try:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    raise LLMError(f"Respons Gemini ({model}) tak terduga: {r.text[:400]}")
            # 429 "kuota habis" tidak pulih dalam hitungan detik → jangan buang kuota, langsung pindah model.
            if r.status_code in GEMINI_RETRY_STATUS and attempt < 2 and r.status_code != 429:
                wait = 2 * (attempt + 1)
                report(f"Gemini {model}: {STATUS_REASON.get(r.status_code, f'error {r.status_code}')} "
                       f"— mencoba lagi dalam {wait} dtk…")
                time.sleep(wait)
                continue
            break
        nxt = f" → mencoba {models[idx + 1]}…" if idx + 1 < len(models) else ""
        if r is None:
            errors.append(f"{model} → timeout")
            report(f"Gemini {model} tidak menjawab dalam 90 dtk{nxt}")
            continue
        if r.status_code in GEMINI_RETRY_STATUS | GEMINI_SKIP_STATUS:
            report(f"Gemini {model}: {STATUS_REASON.get(r.status_code, f'error {r.status_code}')}{nxt}")
        errors.append(f"{model} → {r.status_code}")
        if r.status_code not in GEMINI_RETRY_STATUS | GEMINI_SKIP_STATUS:
            raise LLMError(f"Gemini {r.status_code} ({model}): {r.text[:400]}")
    if any("→ 429" in e for e in errors):
        why = ("Kuota gratis Gemini untuk API key ini habis (429). Kuota per menit pulih dalam ±1 menit, "
               "kuota harian pulih besok (sekitar pukul 14.00–15.00 WIB). Cek pemakaian: https://ai.dev/rate-limit")
    elif all("→ 503" in e or "timeout" in e for e in errors):
        why = "Server Google sedang sibuk. Coba lagi beberapa menit lagi."
    else:
        why = "Model tidak tersedia atau error di server Google. Coba lagi nanti, atau ganti GEMINI_MODEL di .env."
    raise LLMError(f"Semua model Gemini gagal ({', '.join(errors)}). {why}")


def _groq(system: str, user: str, temperature: float) -> str:
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY belum diisi di .env (gratis di console.groq.com)")
    models = list(dict.fromkeys([config.GROQ_MODEL, *config.GROQ_FALLBACK_MODELS]))
    errors = []
    for idx, model in enumerate(models):
        report(f"Menunggu jawaban AI dari Groq {model} — biasanya 1–10 dtk…")
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        errors.append(f"{model} → {r.status_code}")
        # Model dipensiunkan (404), kuota habis (429), atau server error → coba model Groq berikutnya.
        if r.status_code not in (404, 429, 500, 502, 503):
            raise LLMError(f"Groq {r.status_code} ({model}): {r.text[:400]}")
        if idx + 1 < len(models):
            reason = {404: "model sudah tidak tersedia", 429: "kuota habis"}.get(r.status_code, "server sibuk")
            report(f"Groq {model}: {reason} → mencoba {models[idx + 1]}…")
    raise LLMError(f"Semua model Groq gagal ({', '.join(errors)}). Detail terakhir: {r.text[:300]}")


def _ollama(system: str, user: str, temperature: float) -> str:
    report(f"Menunggu jawaban AI lokal Ollama {config.OLLAMA_MODEL} — bisa 1–5 menit di CPU…")
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
