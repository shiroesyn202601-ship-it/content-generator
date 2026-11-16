"""Ketukan visual (beat): potongan narasi di dalam satu scene yang mendapat gambar/footage SENDIRI.

Masalah yang diselesaikan: video panjang punya scene 10-20 dtk. Dengan 1 visual per scene, kata-kata di tengah/akhir
kalimat sering membicarakan hal lain tapi gambarnya tetap sama. Scene kini dipecah per kalimat (kalimat panjang
dipecah lagi di koma / kata sambung, target ±BEAT_WORDS kata ≈ 5 dtk suara), dan tiap potongan diberi visual
yang menggambarkan isi potongan itu. Render memotong tepat saat kata pertama potongan diucapkan (timestamp VO).

Data (beat 0 = visual utama scene, `slot.selected`; yang disimpan hanya beat ke-1 dst.):
  assets.scenes[sid].beats    = [{w, text, selected: {..., path}, pick?}]
  assets.scenes[sid].beats_of = teks scene saat beat dibuat (teks diubah → beat basi, tidak dipakai render)
  w = indeks kata (token tts.tokenize atas tts.split_sentences) di dalam scene → cocok dengan audio.words.
"""
from . import tts

BEAT_WORDS = 11   # ±4-5 dtk untuk suara Indonesia ±2.3 kata/dtk
MIN_WORDS = 5     # potongan lebih pendek digabung ke sebelumnya (gambar < 2 dtk terasa berkedip)
# kata sambung antar-klausa ("yang", "di", "dengan" sengaja tidak: memutus frasa benda — "otot | yang kuat")
CONJ = {"dan", "tetapi", "tapi", "namun", "karena", "sehingga", "lalu", "kemudian", "saat", "ketika",
        "hingga", "sampai", "agar", "supaya", "meski", "meskipun", "walau", "walaupun", "padahal", "sedangkan",
        "atau", "bahkan", "setelah", "sebelum", "sejak", "selama", "sambil", "serta", "untuk"}
# kata yang mengikat kata sesudahnya → tidak boleh jadi kata terakhir potongan ("karnivora yang | telah …")
BIND = CONJ | {"yang", "di", "ke", "dari", "dengan", "pada", "dalam", "oleh", "bagi", "para", "sang", "si",
               "seperti", "sebagai", "setinggi", "sebesar", "sejauh", "hampir", "lebih", "paling", "sangat", "tidak",
               "bukan", "akan", "telah", "sudah", "sedang", "masih", "juga"}


def enabled(p: dict) -> bool:
    """Video panjang saja: scene Shorts sudah pendek (1-2 kalimat) dan potongan dinamis 4 dtk sudah cukup."""
    return p.get("format") == "long"


def _clean(w: str) -> str:
    return w.lower().strip(".,;:!?…\"'()[]—–-")


def split(text: str, per: int = BEAT_WORDS) -> list[dict]:
    """Teks scene → [{w, text}] (termasuk beat 0)."""
    beats, base = [], 0
    for sent in tts.split_sentences(text):
        toks = [t["text"] for t in tts.tokenize(sent)]
        n = len(toks)
        if not n:
            continue
        parts = 1 if n <= per * 1.5 else max(2, round(n / per))
        cuts = [0]
        for j in range(1, parts):
            ideal = round(n * j / parts)
            lo, hi = max(cuts[-1] + MIN_WORDS, ideal - 4), min(n - MIN_WORDS, ideal + 4)
            if lo > hi:
                continue

            def score(i):  # dekat titik ideal, lebih suka setelah koma / sebelum kata sambung
                s = abs(i - ideal)
                if toks[i - 1].rstrip("\"')").endswith((",", ";", ":", "—", "–")):
                    s -= 3
                elif _clean(toks[i - 1]) in BIND:
                    s += 4
                elif _clean(toks[i]) in CONJ:
                    s -= 2
                return s
            cuts.append(min(range(lo, hi + 1), key=score))
        for a, b in zip(cuts, cuts[1:] + [n]):
            beats.append({"w": base + a, "text": " ".join(toks[a:b])})
        base += n
    merged = []
    for b in beats:
        if merged and len(b["text"].split()) < MIN_WORDS:
            merged[-1]["text"] += " " + b["text"]
        else:
            merged.append(b)
    if len(merged) > 1 and len(merged[0]["text"].split()) < MIN_WORDS:  # pembuka sangat pendek → gabung ke berikutnya
        merged[1] = {"w": 0, "text": merged[0]["text"] + " " + merged[1]["text"]}
        merged.pop(0)
    return merged


def valid(slot: dict, scene: dict) -> bool:
    return bool(slot.get("beats")) and slot.get("beats_of") == scene.get("text")
