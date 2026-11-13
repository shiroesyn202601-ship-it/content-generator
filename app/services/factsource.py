"""Bahan fakta dari Wikipedia (gratis, tanpa API key) untuk menekan halusinasi naskah.

Alur: AI mengusulkan kata kunci pencarian → cari di Wikipedia id & en → ambil teks artikel teratas →
potong jadi sumber bernomor [S1], [S2], … yang dikirim ke prompt naskah dan dipakai cek fakta."""
import httpx

from . import llm

# Wikimedia mewajibkan User-Agent deskriptif (tanpa itu: 403). Sengaja generik — tidak memuat data user.
UA = {"User-Agent": "ContentStudioLocal/1.0 (desktop app; https://www.mediawiki.org/wiki/API:Etiquette) httpx"}
HTTP = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), headers=UA, follow_redirects=True)
MAX_SOURCES = 4
CHARS_PER_SOURCE = 2500  # total ±10 rb karakter: cukup padat & muat di batas token/menit Groq gratis


def _api(lang: str, params: dict) -> dict:
    r = HTTP.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", **params})
    r.raise_for_status()
    return r.json()


def search(lang: str, query: str, limit: int = 2) -> list[str]:
    data = _api(lang, {"action": "query", "list": "search", "srsearch": query, "srlimit": limit})
    return [x["title"] for x in data.get("query", {}).get("search", [])]


def extract(lang: str, title: str) -> dict | None:
    data = _api(lang, {"action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain",
                       "inprop": "url", "titles": title, "redirects": 1})
    page = next(iter((data.get("query", {}).get("pages") or {}).values()), {})
    text = (page.get("extract") or "").strip()
    if len(text) < 200:
        return None
    return {"lang": lang, "title": page.get("title", title), "url": page.get("fullurl")
            or f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}", "text": text}


def _queries(topic: str, idea: dict) -> dict:
    """AI mengusulkan judul/kata kunci pencarian Wikipedia (id & en). Fallback: topik apa adanya."""
    try:
        res = llm.chat_json(
            "Kamu peneliti yang mencari artikel Wikipedia paling relevan.",
            f"""Topik video: "{topic}". Ide: {idea.get('title', '')} — {idea.get('angle', '')}
Beri 2 kata kunci pencarian Wikipedia bahasa Indonesia dan 2 bahasa Inggris (nama tempat/tokoh/peristiwa/
istilah inti, BUKAN kalimat). Balas JSON: {{"id": ["...", "..."], "en": ["...", "..."]}}""", temperature=0.2)
        return {"id": [q for q in res.get("id", []) if isinstance(q, str)][:2],
                "en": [q for q in res.get("en", []) if isinstance(q, str)][:2]}
    except llm.LLMError:
        return {"id": [topic], "en": [topic]}


def gather(topic: str, idea: dict, progress=lambda m: None) -> list[dict]:
    """Kumpulkan maks 5 sumber Wikipedia: [{id: 'S1', lang, title, url, text}]."""
    qs = _queries(topic, idea)
    seen, out = set(), []
    for lang in ("id", "en"):
        for q in qs.get(lang) or [topic]:
            progress(f"Riset fakta: mencari '{q}' di Wikipedia {lang.upper()}…")
            try:
                titles = search(lang, q)
            except httpx.HTTPError:
                continue
            for t in titles:
                if (lang, t) in seen or len(out) >= MAX_SOURCES:
                    continue
                seen.add((lang, t))
                try:
                    src = extract(lang, t)
                except httpx.HTTPError:
                    src = None
                if src:
                    src["text"] = src["text"][:CHARS_PER_SOURCE]
                    src["id"] = f"S{len(out) + 1}"
                    out.append(src)
    return out


def as_prompt(sources: list[dict]) -> str:
    return "\n\n".join(f"[{s['id']}] {s['title']} (Wikipedia {s['lang'].upper()})\n{s['text']}" for s in sources)
