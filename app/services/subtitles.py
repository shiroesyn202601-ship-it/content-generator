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


def _boundary(a: dict, b: dict) -> bool:
    """Kata a dan b berasal dari kalimat/scene berbeda → jangan satu baris."""
    for k in ("sent", "scene_id"):
        if a.get(k) is not None and b.get(k) is not None and a[k] != b[k]:
            return True
    return False


def _last_of_sentence(words: list[dict], i: int) -> bool:
    return bool(SENT_END.search(words[i]["text"])) or i + 1 >= len(words) or _boundary(words[i], words[i + 1])


def group_lines(words: list[dict], per_line: int, max_gap: float = 0.6) -> list[dict]:
    lines, cur = [], []
    for i, w in enumerate(words):
        # Baris penuh → pindah baris, KECUALI kata ini penutup kalimat (hindari 1 kata "yatim" sendirian).
        full = len(cur) >= per_line and not (len(cur) == per_line and _last_of_sentence(words, i))
        if cur and (full or w["start"] - words[cur[-1]]["end"] > max_gap
                    or _boundary(words[cur[-1]], w)):
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
    from . import enhance
    if p["mode"] == "scratch":
        words = [dict(w) for w in p["audio"]["words"]]
    elif enhance.valid(p):  # klip + komentar kreator: timeline baru dari tahap nilai tambah
        words = [dict(w) for w in p["enhance"]["words"]]
    else:
        words = _clip_words(p)
    if not words:
        raise RuntimeError("Tidak ada kata bertimestamp. Render VO (scratch) / transkrip (clipper) dulu.")
    style = p["subtitles"].get("style") or store.default_sub_style(p["format"])
    lines = group_lines(words, per_event_words(style))

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
        per = per_event_words(p["subtitles"]["style"])
        p["subtitles"]["lines"] = group_lines(p["subtitles"]["words"], per)
        store.touch_stage(p, "subtitles")
    store.update(pid, apply)
    return {}


# ---------------- ekspor ----------------

# ---------------- gaya (ala CapCut) ----------------
# Semua field gaya subtitle. Template (store.SUB_TEMPLATES) hanya mengisi sebagian; sisanya default di sini.
STYLE_DEFAULTS = {
    "font": "Montserrat ExtraBold", "size": 80, "case": "upper", "bold": False,
    "primary": "#FFFFFF", "highlight": "#FFE600", "emphasis": "#FF3B6B", "emphasis_scale": 120,
    "highlight_mode": "color",   # color | pill | box | karaoke | none
    "highlight_bg": "#7B2FF7", "highlight_text": "#FFFFFF", "highlight_scale": 108,
    "stroke": "#000000", "stroke_w": 5,
    "shadow": "#000000", "shadow_alpha": 60, "shadow_x": 0, "shadow_y": 3, "blur": 0,
    "glow": "", "glow_w": 10,
    "box": "none", "box_color": "#000000", "box_alpha": 60, "box_pad": 14,
    "letter_spacing": 0, "words_per_line": 3, "max_lines": 1,
    "pos_x": 50, "pos_y": 60, "rotation": 0,
    "anim_in": "pop", "anim_out": "none", "active_anim": "pop",
    "hook_text": True,
}
CHOICES = {
    "case": ["upper", "lower", "none"],
    "highlight_mode": ["color", "pill", "box", "karaoke", "none"],
    "box": ["none", "box"],
    "anim_in": ["none", "pop", "bounce", "fade", "slide_up", "typewriter"],
    "anim_out": ["none", "fade"],
    "active_anim": ["none", "pop", "bounce"],
}


def normalize_style(style: dict | None, fmt: str) -> dict:
    """Isi field yang kosong & terjemahkan gaya lama (uppercase/outline/position/animation/karaoke)."""
    s = dict(style or {})
    st = dict(STYLE_DEFAULTS)
    if fmt != "short":
        st.update({"size": 56, "case": "none", "words_per_line": 7, "pos_y": 88, "anim_in": "fade",
                   "active_anim": "none", "highlight_scale": 100, "stroke_w": 3})
    # --- terjemahan gaya lama
    if "uppercase" in s and "case" not in s:
        s["case"] = "upper" if s["uppercase"] else "none"
    if "outline" in s and "stroke_w" not in s:
        s["stroke_w"] = s["outline"]
    if "active_scale" in s and "highlight_scale" not in s:
        s["highlight_scale"] = s["active_scale"]
    if "animation" in s and "active_anim" not in s:
        s["active_anim"] = "pop" if s["animation"] == "pop" else "none"
        s.setdefault("anim_in", "none")
    if "karaoke" in s and "highlight_mode" not in s:
        s["highlight_mode"] = "color" if s["karaoke"] else "none"
    if "position" in s and "pos_y" not in s:
        s["pos_y"] = ({"middle": 50, "lower": 53, "bottom": 60} if fmt == "short"
                      else {"middle": 50, "lower": 75, "bottom": 88}).get(s["position"], st["pos_y"])
    st.update({k: v for k, v in s.items() if v is not None})
    for k, opts in CHOICES.items():
        if st[k] not in opts:
            st[k] = STYLE_DEFAULTS[k]
    for k in ("size", "stroke_w", "shadow_x", "shadow_y", "blur", "glow_w", "box_pad", "letter_spacing",
              "words_per_line", "max_lines", "rotation", "highlight_scale", "emphasis_scale", "shadow_alpha",
              "box_alpha"):
        try:
            st[k] = float(st[k]) if k in ("pos_x", "pos_y") else int(round(float(st[k])))
        except (TypeError, ValueError):
            st[k] = STYLE_DEFAULTS[k]
    st["pos_x"] = max(5.0, min(95.0, float(st["pos_x"])))
    st["pos_y"] = max(5.0, min(95.0, float(st["pos_y"])))
    st["words_per_line"] = max(1, min(12, st["words_per_line"]))
    st["max_lines"] = 1 if st["highlight_mode"] in ("pill", "box") else max(1, min(2, st["max_lines"]))
    if st["box"] == "box":  # kotak latar per baris: kata yang membesar membuat kotak "bertangga"
        st["highlight_scale"] = st["emphasis_scale"] = 100
    return st


def per_event_words(style: dict) -> int:
    s = normalize_style(style, "short")
    return s["words_per_line"] * s["max_lines"]


def _ass_color(hex_: str) -> str:
    h = (hex_ or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _style_color(hex_: str, alpha_pct: float = 100) -> str:
    """Warna untuk baris Style: &HAABBGGRR (AA 00 = penuh)."""
    a = int(round(255 * (1 - max(0, min(100, alpha_pct)) / 100)))
    return f"&H{a:02X}{_ass_color(hex_)[2:-1]}"


def _alpha(pct: float) -> str:
    return f"&H{int(round(255 * (1 - max(0, min(100, pct)) / 100))):02X}&"


def _t(sec: float) -> str:
    cs = int(round(max(sec, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _case(t: str, case: str) -> str:
    return t.upper() if case == "upper" else t.lower() if case == "lower" else t


class _Measure:
    """Lebar kata dalam piksel ASS (untuk latar pill/box di belakang kata aktif).
    libass: ukuran font = tinggi (ascent+descent) → ukuran em Pillow = size × 1000 / (asc+desc @1000)."""

    def __init__(self, style: dict):
        from PIL import ImageFont

        from . import fonts
        fp = fonts.path(style["font"])
        self.ok = fp is not None
        if not self.ok:
            return
        f0 = ImageFont.truetype(str(fp), 1000)
        a, d = f0.getmetrics()
        self.font = ImageFont.truetype(str(fp), max(4, int(round(style["size"] * 1000 / (a + d)))))
        self.sp = style["letter_spacing"]

    def width(self, text: str) -> float:
        return self.font.getlength(text) + self.sp * len(text)


def _rrect(w: float, h: float, r: float) -> str:
    """Path ASS \\p1 persegi panjang sudut membulat (r = radius)."""
    r = max(0.0, min(r, w / 2, h / 2))
    k = r * 0.5523
    f = lambda v: f"{v:.1f}"  # noqa: E731
    return (f"m {f(r)} 0 l {f(w - r)} 0 b {f(w - r + k)} 0 {f(w)} {f(r - k)} {f(w)} {f(r)} "
            f"l {f(w)} {f(h - r)} b {f(w)} {f(h - r + k)} {f(w - r + k)} {f(h)} {f(w - r)} {f(h)} "
            f"l {f(r)} {f(h)} b {f(r - k)} {f(h)} 0 {f(h - r + k)} 0 {f(h - r)} "
            f"l 0 {f(r)} b 0 {f(r - k)} {f(r - k)} 0 {f(r)} 0")


def _hook_events(hook: str, style: dict, fmt: str) -> str:
    """Teks hook besar di bagian atas selama 0–2.5 dtk pertama (alasan penonton tidak swipe)."""
    if not hook or not style.get("hook_text", True):
        return ""
    W, H = config.RESOLUTIONS[fmt]
    y = int(H * 0.2) if fmt == "short" else int(H * 0.12)  # Shorts: di bawah zona atas (±290 px)
    txt = _esc(hook.replace("*", "").strip().upper())
    return (f"Dialogue: 3,{_t(0.05)},{_t(2.5)},Hook,,0,0,0,,"
            f"{{\\an8\\pos({W // 2},{y})\\fad(120,250)\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)}}{txt}\n")


def to_ass(words: list[dict], lines: list[dict], style: dict, fmt: str, hook: str = "",
           extra: tuple[str, str] = ("", "")) -> str:
    W, H = config.RESOLUTIONS[fmt]
    s = normalize_style(style, fmt)
    X, Y = int(W * s["pos_x"] / 100), int(H * s["pos_y"] / 100)
    prim, hi, em = _ass_color(s["primary"]), _ass_color(s["highlight"]), _ass_color(s["emphasis"])
    mode = s["highlight_mode"]
    boxed = s["box"] == "box"
    bold = -1 if s["bold"] else 0
    sh_a = _style_color(s["shadow"], s["shadow_alpha"])
    if boxed:  # BorderStyle 3: outline → kotak latar per baris (padding = Outline)
        main_style = (f"Style: Main,{s['font']},{s['size']},{_style_color(s['primary'])},{_style_color(s['primary'])},"
                      f"{_style_color(s['box_color'], s['box_alpha'])},{_style_color(s['box_color'], s['box_alpha'])},"
                      f"{bold},0,0,0,100,100,{s['letter_spacing']},0,3,{s['box_pad']},0,5,40,40,0,1")
    else:
        main_style = (f"Style: Main,{s['font']},{s['size']},{_style_color(s['primary'])},{_style_color(s['highlight'])},"
                      f"{_style_color(s['stroke'])},{sh_a},{bold},0,0,0,100,100,{s['letter_spacing']},0,1,"
                      f"{s['stroke_w']},0,5,40,40,0,1")
    hook_style = (f"Style: Hook,{s['font']},{int(s['size'] * 0.95)},&H00FFFFFF,&H00FFFFFF,&H30000000,&H00000000,"
                  f"{bold},0,0,0,100,100,0,0,3,16,0,8,40,40,0,1")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{main_style}
{hook_style}
{extra[0]}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rot = f"\\org({X},{Y})\\frz{-s['rotation']}" if s["rotation"] else ""
    shadow = "" if boxed else (f"\\xshad{s['shadow_x']}\\yshad{s['shadow_y']}" if s["shadow_alpha"] > 0
                               else "\\shad0")
    blur = f"\\blur{s['blur']}" if s["blur"] else ""
    measure = _Measure(s) if mode in ("pill", "box") else None
    if measure is not None and not measure.ok:
        mode = "color"
    ev = []
    for ln in lines:
        ws = words[ln["i0"]:ln["i1"] + 1]
        if not ws:
            continue
        texts = [_esc(_case(w["text"], s["case"])) for w in ws]
        brk = len(ws) // 2 if s["max_lines"] == 2 and len(ws) >= 4 else None  # pecah 2 baris
        per_word = mode in ("color", "pill", "box") or s["anim_in"] == "typewriter"

        def body(active: int | None, first: bool) -> str:
            parts = []
            for j, (w, t) in enumerate(zip(ws, texts)):
                c, sc = (em if w.get("emphasis") else prim), 100
                if j == active and mode in ("color", "karaoke"):
                    c, sc = (em, s["emphasis_scale"]) if w.get("emphasis") else (hi, s["highlight_scale"])
                elif j == active and mode in ("pill", "box"):
                    c = _ass_color(s["highlight_text"])
                elif w.get("emphasis") and active is None and mode != "none":
                    sc = s["emphasis_scale"]
                tag = f"\\1c{c}"
                hidden = s["anim_in"] == "typewriter" and active is not None and j > active
                if hidden:
                    tag += "\\alpha&HFF&"
                elif j == active and s["active_anim"] != "none":
                    if s["active_anim"] == "pop":
                        a, b = int(sc * 0.8), int(sc * 1.12)
                        tag += f"\\fscx{a}\\fscy{a}\\t(0,80,\\fscx{b}\\fscy{b})\\t(80,150,\\fscx{sc}\\fscy{sc})"
                    else:  # bounce: turun-naik lebih kenyal
                        a, b, c2 = int(sc * 0.7), int(sc * 1.25), int(sc * 0.95)
                        tag += (f"\\fscx{a}\\fscy{a}\\t(0,90,\\fscx{b}\\fscy{b})\\t(90,170,\\fscx{c2}\\fscy{c2})"
                                f"\\t(170,230,\\fscx{sc}\\fscy{sc})")
                elif first and s["anim_in"] in ("pop", "bounce"):
                    a = int(sc * (0.8 if s["anim_in"] == "pop" else 0.6))
                    tag += f"\\fscx{a}\\fscy{a}\\t(0,{120 if s['anim_in'] == 'pop' else 180},\\fscx{sc}\\fscy{sc})"
                else:
                    tag += f"\\fscx{sc}\\fscy{sc}"
                if s["anim_in"] != "typewriter" or not hidden:
                    tag += "\\alpha&H00&"
                parts.append(f"{{{tag}}}{t}")
                if j + 1 < len(ws):
                    parts.append("\\N" if brk is not None and j + 1 == brk else " ")
            return "".join(parts)

        def prefix(first: bool, last: bool, t0: float, t1: float) -> str:
            pos = (f"\\move({X},{Y + 40},{X},{Y},0,160)" if first and s["anim_in"] == "slide_up"
                   else f"\\pos({X},{Y})")
            fade_in = 150 if first and s["anim_in"] == "fade" else 0
            fade_out = 120 if last and s["anim_out"] == "fade" else 0
            fad = f"\\fad({fade_in},{fade_out})" if fade_in or fade_out else ""
            return f"\\an5{pos}{rot}{shadow}{blur}{fad}"

        def pill(j: int) -> str:
            """Latar pill/box di belakang kata aktif j (dihitung dari lebar kata)."""
            wd = [measure.width(_case(w["text"], s["case"])) for w in ws]
            space = measure.width(" ")
            total = sum(wd) + space * (len(ws) - 1)
            x0 = X - total / 2 + sum(wd[:j]) + space * j
            pad = s["box_pad"]
            hgt = s["size"] * 0.82 + pad
            r = hgt / 2 if mode == "pill" else s["size"] * 0.12
            return (f"{{\\an7\\pos({x0 - pad:.0f},{Y - hgt / 2:.0f}){rot}\\p1\\bord0\\shad0"
                    f"\\1c{_ass_color(s['highlight_bg'])}}}{_rrect(wd[j] + 2 * pad, hgt, r)}")

        if mode == "karaoke":  # sapuan warna per kata (\kf) dalam satu event
            parts = []
            for j, (w, t) in enumerate(zip(ws, texts)):
                nxt = ws[j + 1]["start"] if j + 1 < len(ws) else ln["end"]
                dur = max(1, int(round((nxt - (ln["start"] if j == 0 else w["start"])) * 100)))
                after = em if w.get("emphasis") else hi  # \kf: warna Secondary (belum) → Primary (sudah)
                parts.append(f"{{\\kf{dur}\\1c{after}\\2c{prim}}}{t}")
                if j + 1 < len(ws):
                    parts.append("\\N" if brk is not None and j + 1 == brk else " ")
            txt = "".join(parts)
            ev.append((ln["start"], ln["end"], f"{{{prefix(True, True, ln['start'], ln['end'])}}}{txt}", None))
        elif per_word:
            for j, w in enumerate(ws):
                st = ln["start"] if j == 0 else w["start"]
                en = ws[j + 1]["start"] if j + 1 < len(ws) else ln["end"]
                if en <= st:
                    continue
                pre = prefix(j == 0, j + 1 == len(ws), st, en)
                back = pill(j) if mode in ("pill", "box") else None
                ev.append((st, en, f"{{{pre}}}{body(j, j == 0)}", back))
        else:
            ev.append((ln["start"], ln["end"], f"{{{prefix(True, True, ln['start'], ln['end'])}}}{body(None, True)}", None))

    out = []
    glow = s["glow"] and not boxed
    for st, en, txt, back in ev:
        if back:
            out.append(f"Dialogue: 0,{_t(st)},{_t(en)},Main,,0,0,0,,{back}")
        if glow:  # lapisan glow: garis tepi tebal + blur, isi transparan, di bawah teks utama
            out.append(f"Dialogue: 1,{_t(st)},{_t(en)},Main,,0,0,0,,"
                       f"{{\\bord{s['glow_w']}\\blur8\\3c{_ass_color(s['glow'])}\\shad0}}"
                       + txt.replace("\\alpha&H00&", "\\1a&HFF&\\3a&H40&"))
        out.append(f"Dialogue: 2,{_t(st)},{_t(en)},Main,,0,0,0,,{txt}")
    return head + _hook_events(hook, s, fmt) + extra[1] + "\n".join(out) + "\n"


def hook_text(p: dict) -> str:
    """Teks hook di layar: scratch → on_screen_text scene pertama; clipper → hook klip terpilih."""
    if p["mode"] == "scratch":
        sc = (p["script"].get("scenes") or [{}])[0]
        return sc.get("on_screen_text") or ""
    clips, sel = p["script"].get("clips") or [], p["script"].get("selected_clip")
    return clips[sel].get("hook", "") if sel is not None and sel < len(clips) else ""


def to_srt(lines: list[dict]) -> str:
    def t(sec):
        ms = int(round(sec * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i + 1}\n{t(l['start'])} --> {t(l['end'])}\n{l['text'].replace('*', '')}\n"
                     for i, l in enumerate(lines))


def export(p: dict) -> None:
    d = store.project_dir(p["id"])
    s = p["subtitles"]
    from . import enhance
    extra = enhance.ass_parts(p, normalize_style(s["style"], p["format"]))
    (d / "subs.ass").write_text(to_ass(s["words"], s["lines"], s["style"], p["format"], hook_text(p), extra),
                                encoding="utf-8")
    (d / "subs.srt").write_text(to_srt(s["lines"]), encoding="utf-8")


def preview_frame(p: dict, style: dict, t: float) -> bytes:
    """PNG satu frame pada detik t: latar (klip Clipper / aset scene) + subtitle dirender libass —
    identik dengan hasil render (font, posisi, animasi yang sudah selesai pada detik itu)."""
    import subprocess

    from . import fonts
    d = store.project_dir(p["id"])
    (d / "render").mkdir(exist_ok=True)
    W, H = config.RESOLUTIONS[p["format"]]
    s = p["subtitles"]
    words, lines = s.get("words") or [], s.get("lines") or []
    fonts.ensure(normalize_style(style, p["format"])["font"])
    from . import enhance
    extra = enhance.ass_parts(p, normalize_style(style, p["format"]))
    (d / "render" / "preview.ass").write_text(to_ass(words, lines, style, p["format"], hook_text(p), extra), encoding="utf-8")
    # latar
    bg, bg_t = None, 0.0
    vp = enhance.video_path(p) if p["mode"] == "clipper" else None
    if vp and (d / vp).is_file():
        bg, bg_t = vp, t
    else:
        sid = None
        for tm in p["audio"].get("scene_timings") or []:
            if tm["start"] <= t:
                sid = tm["scene_id"]
        sel = ((p["assets"].get("scenes") or {}).get(sid or "") or {}).get("selected") or {}
        if sel.get("path") and (d / sel["path"]).is_file():
            bg = sel["path"]
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if bg is None:
        src = ["-f", "lavfi", "-i", f"color=c=0x2a2f3a:s={W}x{H}"]
    elif bg.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp"):
        src = ["-i", bg]
    else:
        src = ["-ss", f"{max(0.0, bg_t):.3f}", "-i", bg]
    cmd = [config.FFMPEG, "-v", "error", "-y", *src, "-frames:v", "1",
           "-vf", f"{cover},setpts=PTS-STARTPTS+{t:.3f}/TB,ass=render/preview.ass:fontsdir={store.fonts_rel(d)}",
           "-f", "image2pipe", "-vcodec", "png", "-"]
    r = subprocess.run(cmd, cwd=d, capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(r.stderr.decode(errors="replace")[-300:])

    return r.stdout
