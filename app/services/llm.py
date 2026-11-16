"""Satu antarmuka untuk LLM gratis: Groq, Gemini, Cerebras, OpenRouter, Mistral (free tier) + Ollama (lokal).

Tahan banting — tidak bergantung pada satu AI:
  1. Tiap layanan boleh punya BANYAK API key (lihat keys.py). Key yang kena batas didinginkan,
     key berikutnya langsung dipakai.
  2. Tiap layanan punya beberapa model cadangan (404/503/kuota → model berikutnya).
  3. Bila satu layanan habis total → layanan berikutnya di rantai (LLM_PROVIDER → LLM_FALLBACK_PROVIDERS →
     layanan lain yang key-nya terisi). Ollama hanya dipakai bila disebut di .env.
Semua pemanggilan meminta output JSON agar bisa langsung dipakai tahap berikutnya."""
import json
import re
import threading
import time

import httpx

from .. import config
from ..jobs import report
from . import keys, usage

TIMEOUT = httpx.Timeout(180.0, connect=15.0)
PROVIDER_LABEL = {"gemini": "Gemini", "groq": "Groq", "cerebras": "Cerebras", "openrouter": "OpenRouter",
                  "mistral": "Mistral", "cloudflare": "Cloudflare", "ollama": "Ollama (lokal)"}
# Urutan cadangan (Nov 2026): Cerebras kini hanya kredit uji coba $5 & OpenRouter :free 50 request/hari → paling akhir.
CLOUD = ("groq", "gemini", "cloudflare", "mistral", "openrouter", "cerebras")
STATUS_REASON = {429: "kuota gratis habis", 500: "error di server", 503: "server sedang sibuk",
                 404: "model sudah tidak tersedia"}


_last = threading.local()


def last_used() -> dict:
    """{"provider", "model"} jawaban AI terakhir di thread ini (untuk laporan "AI apa yang dipakai")."""
    return dict(getattr(_last, "info", None) or {})


def _mark(provider: str, model: str) -> None:
    _last.info = {"provider": provider, "model": model}
    # laporan produksi (services/usage.py): AI apa yang menjawab + lama tunggunya (termasuk antre kuota)
    t0 = getattr(_last, "t0", None)
    usage.ai(getattr(_last, "kind", None) or "LLM", PROVIDER_LABEL.get(provider, provider), model,
             time.time() - t0 if t0 else None)
    _last.t0 = None  # jangan terbawa ke panggilan berikutnya yang tidak lewat chat_json/vision_json


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    """Semua key model ini kena batas token/menit & harus menunggu > max_wait (pemanggil boleh oper ke model lain)."""

    def __init__(self, model: str, wait: float):
        super().__init__(f"Groq {model}: batas per menit, pulih dalam {wait:.0f} dtk")
        self.model, self.wait = model, wait


def _configured(provider: str) -> bool:
    """Provider cadangan hanya dicoba kalau memang sudah disiapkan (API key terisi & belum ditolak semua)."""
    return True if provider == "ollama" else keys.has(provider)


def chain(primary: str | None = None) -> list[str]:
    """Urutan provider yang dicoba: utama → LLM_FALLBACK_PROVIDERS → layanan cloud lain yang key-nya terisi."""
    primary = (primary or config.LLM_PROVIDER).lower()
    order = [primary, *config.LLM_FALLBACK_PROVIDERS, *CLOUD]
    out = []
    for p in order:
        if p in PROVIDER_LABEL and p not in out and (p == primary or _configured(p)):
            if p == "ollama" and p != primary and p not in config.LLM_FALLBACK_PROVIDERS:
                continue
            out.append(p)
    return out


def strict(props: dict, required: list | None = None) -> dict:
    """Bantu menyusun JSON schema 'strict' (Groq structured outputs): semua field wajib, tanpa field tambahan."""
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


STR = {"type": "string"}
INT = {"type": "integer"}


def chat_json(system: str, user: str, provider: str | None = None, temperature: float = 0.8,
              schema: dict | None = None, groq_model: str | None = None, effort: str | None = None,
              fallback: bool = True, max_wait: float | None = None, skip: tuple = (),
              only_model: bool = False) -> dict:
    """schema (opsional): JSON schema strict → Groq menjamin bentuk jawaban (field & enum) valid.
    groq_model: model Groq yang dicoba pertama (tiap model punya kuota token/menit sendiri → tugas besar
    bisa dibagi ke beberapa model). effort: reasoning_effort gpt-oss (low/medium/high).
    fallback=False: jangan pindah ke provider cadangan — pemanggil menangani gagal.
    skip: provider yang dilewati (mis. pemanggil baru saja gagal di Groq).
    only_model: hanya groq_model (jalur paralel tidak boleh memakai kuota model jalur lain).
    Provider & model yang akhirnya menjawab tersedia di llm.last_used() (per thread)."""
    primary = (provider or config.LLM_PROVIDER).lower()
    if primary not in PROVIDER_LABEL:
        raise LLMError(f"Provider LLM tidak dikenal: {primary}")
    order = chain(primary) if fallback else [primary]
    order = [p for p in order if p not in skip] or order
    errors = []
    for i, name in enumerate(order):
        if errors:
            report(f"{PROVIDER_LABEL[order[i - 1]]} gagal → beralih ke cadangan {PROVIDER_LABEL[name]}…")
        _last.t0, _last.kind = time.time(), "LLM"
        try:
            if name == "groq":
                raw = _groq(system, user, temperature, schema, prefer=groq_model, effort=effort, max_wait=max_wait,
                            only=only_model)
            elif name == "gemini":
                raw = _gemini(system, user, temperature, schema)
            elif name == "ollama":
                raw = _ollama(system, user, temperature, schema)
            else:
                raw = _compat(name, system, user, temperature)
            data = parse_json(raw)
        except RateLimited:
            raise  # pemanggil (mis. antrean paralel) yang memutuskan: oper ke model lain / tunggu
        except (LLMError, httpx.HTTPError) as e:
            errors.append(f"[{name}] {e}")
            continue
        if errors:
            print(f"LLM: {primary} gagal, memakai cadangan '{name}'.")
        report(f"Jawaban AI ({PROVIDER_LABEL[name]}) diterima, membaca hasilnya…")
        return data
    single = [keys.ENV[p] + "_2" for p in ("groq", "gemini") if len(keys.pool(p)) == 1]
    unused = [keys.ENV[p] for p in ("cloudflare", "mistral", "openrouter") if not keys.pool(p)]
    hint = ("" if len(order) > 1 or not (single or unused) else
            "\n\nSolusi: tambah API key cadangan di .env — " + " / ".join(single + unused)
            + " (semua gratis). Aplikasi otomatis pindah saat yang lain habis.")
    raise LLMError("\n\n".join(errors) + hint)


def parse_json(raw: str) -> dict:
    """Toleran terhadap ```json fences```, blok <think>, atau teks pembuka dari model kecil."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMError(f"Output LLM bukan JSON valid:\n{raw[:500]}")


def _tag(service: str, key: str) -> str:
    lb = keys.label(service, key)
    return f" ({lb})" if lb else ""


# ---------------------------------------------------------------- Gemini
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = httpx.Timeout(90.0, connect=15.0)  # lebih pendek: model yang menggantung → pindah model


def gemini_models() -> list[str]:
    return list(dict.fromkeys([config.GEMINI_MODEL, *config.GEMINI_FALLBACK_MODELS]))


def gemini_post(model: str, body: dict, what: str = "Gemini", timeout=GEMINI_TIMEOUT) -> dict:
    """POST generateContent dengan rotasi key. Mengembalikan JSON respons (status 200).
    429 → key didinginkan (harian ±1 jam) & key lain dicoba; key ditolak → dinonaktifkan;
    503/500 → coba ulang singkat. Lempar LLMError (dengan .status) bila model ini tidak bisa dipakai."""
    tried, busy = 0, 0
    while True:
        key = keys.take("gemini", model)
        if key is None:
            w = keys.wait_time("gemini", model)
            err = LLMError(f"{model} → semua key kena batas" + (f" (pulih ±{w / 60:.0f} mnt)" if w < 1e9 else ""))
            err.status = 429
            raise err
        tried += 1
        report(f"Menunggu {what} {model}{_tag('gemini', key)} — biasanya 10–30 dtk…")
        try:
            r = httpx.post(GEMINI_URL.format(model=model), params={"key": key}, json=body, timeout=timeout)
        except httpx.TimeoutException:
            err = LLMError(f"{model} → timeout")
            err.status = 504
            raise err
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            w = keys.gemini_429_wait(r.text)
            keys.cool("gemini", key, model, w)
            report(f"Gemini {model}{_tag('gemini', key)}: kuota habis → mencoba key lain…")
            continue
        if r.status_code in (401, 403) or (r.status_code == 400 and re.search(r"API_KEY_INVALID|API key not valid", r.text)):
            keys.kill("gemini", key, f"{r.status_code} {r.text[:80]}")
            continue
        if r.status_code in (500, 503) and busy < 2:
            busy += 1
            report(f"Gemini {model}: {STATUS_REASON[r.status_code]} — mencoba lagi dalam {2 * busy} dtk…")
            time.sleep(2 * busy)
            continue
        err = LLMError(f"{model} → {r.status_code}: {r.text[:300]}")
        err.status = r.status_code
        raise err


def _gemini_run(body: dict, what: str = "Gemini") -> str:
    if not keys.has("gemini"):
        raise LLMError("GEMINI_API_KEY belum diisi di .env (gratis di aistudio.google.com)")
    errors = []
    models = gemini_models()
    for idx, model in enumerate(models):
        try:
            j = gemini_post(model, body, what)
        except LLMError as e:
            errors.append(str(e).split(":")[0])
            st = getattr(e, "status", 0)
            if st not in (404, 429, 500, 503, 504):
                raise
            if idx + 1 < len(models):
                report(f"Gemini {model}: {STATUS_REASON.get(st, 'gagal')} → mencoba {models[idx + 1]}…")
            continue
        try:
            _mark("gemini", model)
            return j["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise LLMError(f"Respons Gemini ({model}) tak terduga: {json.dumps(j)[:400]}")
    if any("batas" in e for e in errors):
        why = (f"Kuota gratis semua key Gemini ({len(keys.pool('gemini'))}) habis. Tambahkan GEMINI_API_KEY_2 dari "
               "akun Google lain, atau tunggu kuota pulih (harian: sekitar pukul 14.00–15.00 WIB).")
    else:
        why = "Server Google sibuk / model tidak tersedia. Coba lagi nanti, atau ganti GEMINI_MODEL di .env."
    raise LLMError(f"Semua model Gemini gagal ({'; '.join(errors)}). {why}")


def _gemini(system: str, user: str, temperature: float, schema: dict | None = None) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    return _gemini_run(body)


# ---------------------------------------------------------------- Groq
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
STRICT_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")


def _dur(s: str) -> float:
    """'7.66s' / '1m2.5s' / '450ms' → detik."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", (s or "").strip())
    if not m or not any(m.groups()):
        return 0.0
    h, mi, sec, ms = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(sec or 0) + float(ms or 0) / 1000


def _retry_after(r: httpx.Response) -> float:
    """Lama tunggu dari header Groq: retry-after, atau waktu reset batas token/request per menit."""
    try:
        if r.headers.get("retry-after"):
            return float(r.headers["retry-after"])
    except ValueError:
        pass
    if r.status_code == 429:
        waits = [_dur(r.headers.get(h, "")) for h in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests")]
        w = max(waits or [0])
        return w if w > 0 else 20.0  # tanpa petunjuk: batas per menit biasanya pulih ±20 dtk
    return 0.0


def _groq_post(key: str, model: str, messages: list, temperature: float, schema: dict | None,
               effort: str | None = None) -> httpx.Response:
    rf = {"type": "json_object"}
    if schema and model in STRICT_MODELS:
        rf = {"type": "json_schema", "json_schema": {"name": "jawaban", "strict": True, "schema": schema}}
    body = {"model": model, "temperature": temperature, "response_format": rf, "messages": messages}
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = effort or "medium"
    return httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)


def groq_models() -> list[str]:
    return list(dict.fromkeys([config.GROQ_MODEL, *config.GROQ_FALLBACK_MODELS]))


def groq_call(model: str, messages: list, temperature: float, schema: dict | None = None,
              effort: str | None = None, max_wait: float | None = None, first: bool = True,
              max_sleeps: int = 3) -> httpx.Response | None:
    """Satu model Groq, berputar di semua key. 429 → key didinginkan, key lain langsung dicoba; bila semua
    key menunggu ≤60 dtk → tidur lalu ulang. Mengembalikan respons terakhir (bukan 429) atau None bila
    semua key kena batas lebih lama."""
    use_schema, sleeps = schema, 0
    while True:
        key = keys.take("groq", model)
        if key is None:
            w = keys.wait_time("groq", model)
            if w == float("inf"):
                raise LLMError("Semua GROQ_API_KEY ditolak (tidak valid). Cek key di console.groq.com/keys.")
            if max_wait is not None and first and w > max_wait:
                raise RateLimited(model, min(w, 60.0))
            if w <= 60 and sleeps < max_sleeps:
                sleeps += 1
                report(f"Groq {model}: semua key kena batas per menit — menunggu {w:.0f} dtk…")
                time.sleep(max(w, 3.0) + 0.5)
                continue
            return None
        report(f"Menunggu jawaban AI dari Groq {model}{_tag('groq', key)} — biasanya 1–10 dtk…")
        r = _groq_post(key, model, messages, temperature, use_schema, effort)
        if r.status_code == 429:
            # Groq gratis: batas token/permintaan PER MENIT (pulih < 60 dtk) atau per hari (lama).
            keys.cool("groq", key, model, max(_retry_after(r), 3.0))
            if len(keys.pool("groq")) > 1:
                report(f"Groq {model}{_tag('groq', key)}: batas tercapai → pindah ke key lain…")
            continue
        if r.status_code in (401, 403):
            keys.kill("groq", key, f"{r.status_code} {r.text[:80]}")
            continue
        # Skema strict kadang gagal divalidasi (jawaban panjang) → ulangi dalam mode JSON biasa.
        if r.status_code == 400 and "json_validate_failed" in r.text and use_schema:
            report(f"Groq {model}: format terkunci gagal — mengulang dengan format JSON biasa…")
            use_schema = None
            continue
        return r


def _groq(system: str, user: str, temperature: float, schema: dict | None = None,
          prefer: str | None = None, effort: str | None = None, max_wait: float | None = None,
          only: bool = False) -> str:
    if not keys.has("groq"):
        raise LLMError("GROQ_API_KEY belum diisi di .env (gratis di console.groq.com)")
    models = groq_models()
    if prefer:
        models = [prefer] if only else list(dict.fromkeys([prefer, *models]))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors, last = [], ""
    for idx, model in enumerate(models):
        r = groq_call(model, messages, temperature, schema, effort, max_wait, first=idx == 0)
        if r is None:
            errors.append(f"{model} → 429 (semua key)")
            if idx + 1 < len(models):
                report(f"Groq {model}: kuota semua key habis → mencoba {models[idx + 1]}…")
            continue
        if r.status_code == 200:
            _mark("groq", model)
            return r.json()["choices"][0]["message"]["content"]
        errors.append(f"{model} → {r.status_code}")
        last = r.text[:300]
        json_fail = r.status_code == 400 and "json_validate_failed" in r.text
        # Model dipensiunkan (404), JSON gagal divalidasi (400), terlalu besar (413), server error → model berikutnya.
        if r.status_code not in (404, 413, 500, 502, 503) and not json_fail:
            raise LLMError(f"Groq {r.status_code} ({model}): {last}")
        if idx + 1 < len(models):
            reason = {404: "model sudah tidak tersedia", 413: "permintaan terlalu besar"}.get(
                r.status_code, "JSON tidak valid" if json_fail else "server sibuk")
            report(f"Groq {model}: {reason} → mencoba {models[idx + 1]}…")
    raise LLMError(f"Semua model Groq gagal ({', '.join(errors)}). {last}")


# ---------------------------------------------------------------- layanan kompatibel OpenAI (free tier)
COMPAT = {
    "cerebras": ("https://api.cerebras.ai/v1/chat/completions", lambda: config.CEREBRAS_MODELS),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", lambda: config.OPENROUTER_MODELS),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", lambda: config.MISTRAL_MODELS),
    # URL & token Cloudflare tergantung akun: key = "<account_id>:<token>"
    "cloudflare": (lambda acct: f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions",
                   lambda: config.CLOUDFLARE_MODELS),
}


def _endpoint(service: str, key: str) -> tuple[str, str]:
    """(url, bearer token) untuk satu key layanan kompatibel OpenAI."""
    url = COMPAT[service][0]
    if callable(url):
        acct, _, tok = key.partition(":")
        return url(acct), tok
    return url, key


def _compat(service: str, system: str, user: str, temperature: float) -> str:
    models = COMPAT[service][1]
    if not keys.has(service):
        raise LLMError(f"{service.upper()}_API_KEY belum diisi.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors = []
    for model in models():
        rf = True
        while True:
            key = keys.take(service, model)
            if key is None:
                errors.append(f"{model} → semua key kena batas")
                break
            report(f"Menunggu jawaban AI dari {PROVIDER_LABEL[service]} {model}{_tag(service, key)}…")
            body = {"model": model, "temperature": temperature, "messages": messages}
            if rf:
                body["response_format"] = {"type": "json_object"}
            try:
                ep, tok = _endpoint(service, key)
                r = httpx.post(ep, headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=TIMEOUT)
            except httpx.TimeoutException:
                errors.append(f"{model} → timeout")
                break
            if r.status_code == 200:
                try:
                    _mark(service, model)
                    return r.json()["choices"][0]["message"]["content"] or ""
                except (KeyError, IndexError, TypeError):
                    errors.append(f"{model} → respons kosong")
                    break
            if r.status_code == 429:
                keys.cool(service, key, model, max(_retry_after(r), 30.0))
                continue
            if r.status_code in (401, 403):
                keys.kill(service, key, f"{r.status_code} {r.text[:80]}")
                continue
            if r.status_code == 400 and rf and "response_format" in r.text:
                rf = False  # model tidak mendukung mode JSON → minta JSON lewat prompt saja
                continue
            errors.append(f"{model} → {r.status_code}")
            break
    raise LLMError(f"{PROVIDER_LABEL[service]} gagal ({'; '.join(errors)})")


# ---------------------------------------------------------------- vision (Groq → cadangan Gemini)
VISION_MODEL = "qwen/qwen3.8-27b"


def vision_available() -> bool:
    return keys.has("groq") or keys.has("gemini")


def vision_json(prompt: str, image_jpeg: bytes, schema: dict | None = None) -> dict:
    """Tanya model vision tentang satu gambar (mis. lembar kontak thumbnail). JSON wajib.
    Groq (semua key) dulu; bila habis/gagal → Gemini (semua key)."""
    import base64
    b64 = base64.b64encode(image_jpeg).decode()
    errors = []
    _last.t0, _last.kind = time.time(), "Visi (gambar)"
    try:
        return _vision(prompt, b64, schema, errors)
    finally:
        _last.kind = None


def _vision(prompt: str, b64: str, schema: dict | None, errors: list) -> dict:
    if keys.has("groq"):
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                 {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]
        report(f"AI melihat gambar kandidat ({VISION_MODEL})…")
        try:
            r = groq_call(VISION_MODEL, messages, 0.1, schema, max_sleeps=2)
            if r is not None and r.status_code == 400 and "JSON" in r.text and schema:
                r = groq_call(VISION_MODEL, messages, 0.1, None, max_sleeps=2)
            if r is not None and r.status_code == 200:
                _mark("groq", VISION_MODEL)
                return parse_json(r.json()["choices"][0]["message"]["content"])
            errors.append(f"Groq vision {r.status_code if r is not None else '429'}")
        except LLMError as e:
            errors.append(str(e))
    if keys.has("gemini"):
        report("Vision Groq tidak tersedia → memakai Gemini untuk melihat gambar…")
        body = {"contents": [{"role": "user", "parts": [{"text": prompt},
                                                        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
        try:
            return parse_json(_gemini_run(body, "Gemini vision"))
        except LLMError as e:
            errors.append(str(e))
    raise LLMError("Penilaian visual gagal: " + (" | ".join(errors) or "butuh GROQ_API_KEY atau GEMINI_API_KEY."))


# ---------------------------------------------------------------- Ollama (lokal)
def _ollama(system: str, user: str, temperature: float, schema: dict | None = None) -> str:
    report(f"Menunggu jawaban AI lokal Ollama {config.OLLAMA_MODEL} — bisa 1–5 menit di CPU…")
    try:
        r = httpx.post(
            f"{config.OLLAMA_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "stream": False,
                "format": schema or "json",
                "options": {"temperature": temperature},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
    except httpx.ConnectError:
        raise LLMError("Ollama tidak berjalan. Jalankan `ollama serve` dan `ollama pull <model>`.")
    if r.status_code != 200:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:400]}")
    _mark("ollama", config.OLLAMA_MODEL)
    return r.json()["message"]["content"]
