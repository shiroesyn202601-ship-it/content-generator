"""
Tahap 5: subtitle word-level.

Sumber kata:  scratch -> audio.words (dari Edge-TTS)   clipper -> transcript Whisper dalam rentang klip
Penyimpanan:  subtitles.words = [Word]   subtitles.lines = [{start, end, text, i0, i1}]
              text baris memakai sintaks *kata* untuk penekanan (sama seperti di naskah).
Output:       subs.ass (burn-in, karaoke + penekanan warna/ukuran) dan subs.srt
"""
import re

from .. import config, store
from .tts import PUNCT, tokenize

SENT_END = re.compile(r"[.!?…]$")


def _clip_words(p: dict) -> list[dict]:
    clip = p["script"]["clips"][p["script"]["selected_clip"]]
    cs, ce = p["assets"].get("clip_range") or [clip["start"], clip["end"]]
    keys = {k.lower() for kw in clip.get("keywords", []) for k in kw.split()}
    out = []
    for seg in p["script"]["transcript"]["segments"]:
        for w in seg.get("words", []):
            if w["start"] >= cs - 0.05 and w["end"] <= ce + 0.05:
                core = PUNCT.match(w["text"]).group(2).lower()
                out.append({"text": w["text"], "start": round(max(0, w["start"] - cs), 3),
                            "end": round(w["end"] - cs, 3), "emphasis": core in keys})
    return out


def group_lines(words: list[dict], per_line: int, max_gap: float = 0.6) -> list[dict]:
    lines, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) >= per_line or w["start"] - words[cur[-1]]["end"] > max_gap):
            lines.append(cur)
            cur = []
        cur.append(i)
        if SENT_END.search(w["text"]):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [_line(words, ix[0], ix[-1]) for ix in lines]


def _line(words: list[dict], i0: int, i1: int) -> dict:
    ws = words[i0:i1 + 1]
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "i0": i0, "i1": i1,
            "text": " ".join(f"*{w['text']}*" if w.get("emphasis") else w["text"] for w in ws)}


def build(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    words = [dict(w) for w in p["audio"]["words"]] if p["mode"] == "scratch" else _clip_words(p)
    if not words:
        raise RuntimeError("Tidak ada kata bertimestamp. Render VO (scratch) / transkrip (clipper) dulu.")
    style = p["subtitles"].get("style") or store.default_sub_style(p["format"])
    lines = group_lines(words, int(style.get("words_per_line", 3)))

    def apply(p):
        p["subtitles"].update({"words": words, "lines": lines, "style": style})
        store.touch_stage(p, "subtitles")
        store.log(p, f"Subtitle: {len(words)} kata, {len(lines)} baris")
    store.update(pid, apply)
    return {"lines": len(lines)}


def apply_lines(pid: str, params: dict, progress) -> dict:
    """Simpan hasil editor subtitle. Setiap baris: {start, end, text, i0?, i1?}.
    Jumlah kata sama -> timing kata asli dipertahankan (diskalakan ke start/end baru);
    beda -> timing dibagi proporsional panjang kata."""
    p = store.load(pid)
    old = p["subtitles"]["words"]
    new_words, new_lines = [], []
    for ln in params["lines"]:
        toks = tokenize(ln["text"])
        if not toks:
            continue
        st, en = float(ln["start"]), float(ln["end"])
        en = max(en, st + 0.2)
        orig = old[ln["i0"]:ln["i1"] + 1] if ln.get("i0") is not None else []
        if orig and len(orig) == len(toks):
            os_, oe = orig[0]["start"], orig[-1]["end"]
            k = (en - st) / max(oe - os_, 1e-3)
            ws = [{**t, "start": round(st + (o["start"] - os_) * k, 3), "end": round(st + (o["end"] - os_) * k, 3)}
                  for t, o in zip(toks, orig)]
        else:
            lens = [max(len(t["text"]), 1) for t in toks]
            acc, ws = st, []
            for t, ln_ in zip(toks, lens):
                d = (en - st) * ln_ / sum(lens)
                ws.append({**t, "start": round(acc, 3), "end": round(acc + d, 3)})
                acc += d
        i0 = len(new_words)
        new_words += ws
        new_lines.append(_line(new_words, i0, len(new_words) - 1))

    def apply(p):
        p["subtitles"]["words"] = new_words
        p["subtitles"]["lines"] = new_lines
        if params.get("style"):
            p["subtitles"]["style"] = params["style"]
        store.touch_stage(p, "subtitles")
        store.log(p, "Edit manual subtitle")
    store.update(pid, apply)
    return {"lines": len(new_lines)}


def regroup(pid: str, params: dict, progress) -> dict:
    def apply(p):
        if params.get("style"):
            p["subtitles"]["style"] = params["style"]
        per = int(p["subtitles"]["style"].get("words_per_line", 3))
        p["subtitles"]["lines"] = group_lines(p["subtitles"]["words"], per)
        store.touch_stage(p, "subtitles")
    store.update(pid, apply)
    return {}


# ---------------- ekspor ----------------

def _ass_color(hex_: str) -> str:
    h = hex_.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _t(sec: float) -> str:
    cs = int(round(max(sec, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(words: list[dict], lines: list[dict], style: dict, fmt: str) -> str:
    W, H = config.RESOLUTIONS[fmt]
    pos = style.get("position", "bottom")
    align, mv = {"middle": (5, 0), "lower": (2, int(H * 0.22))}.get(pos, (2, int(H * 0.08)))
    prim, hi, em = _ass_color(style["primary"]), _ass_color(style["highlight"]), _ass_color(style["emphasis"])
    sc = int(style.get("emphasis_scale", 125))
    up = style.get("uppercase", False)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{style['font']},{style['size']},{prim},{prim},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{style.get('outline', 4)},2,{align},60,60,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for ln in lines:
        ws = words[ln["i0"]:ln["i1"] + 1]
        if not ws:
            continue
        texts = [_esc(w["text"].upper() if up else w["text"]) for w in ws]

        def render(active: int | None) -> str:
            parts = []
            for j, (w, t) in enumerate(zip(ws, texts)):
                c, s = (em if w.get("emphasis") else prim), 100
                if j == active:
                    c, s = (em, sc) if w.get("emphasis") else (hi, 100)
                elif w.get("emphasis") and active is None:
                    s = sc
                parts.append(f"{{\\c{c}&\\fscx{s}\\fscy{s}}}{t}")
            return " ".join(parts)

        if style.get("karaoke", True):
            for j, w in enumerate(ws):
                st = ln["start"] if j == 0 else w["start"]
                en = ws[j + 1]["start"] if j + 1 < len(ws) else ln["end"]
                if en > st:
                    ev.append(f"Dialogue: 0,{_t(st)},{_t(en)},Main,,0,0,0,,{render(j)}")
        else:
            ev.append(f"Dialogue: 0,{_t(ln['start'])},{_t(ln['end'])},Main,,0,0,0,,{render(None)}")
    return head + "\n".join(ev) + "\n"


def to_srt(lines: list[dict]) -> str:
    def t(sec):
        ms = int(round(sec * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i + 1}\n{t(l['start'])} --> {t(l['end'])}\n{l['text'].replace('*', '')}\n"
                     for i, l in enumerate(lines))


def export(p: dict) -> None:
    d = store.project_dir(p["id"])
    s = p["subtitles"]
    (d / "subs.ass").write_text(to_ass(s["words"], s["lines"], s["style"], p["format"]), encoding="utf-8")
    (d / "subs.srt").write_text(to_srt(s["lines"]), encoding="utf-8")
