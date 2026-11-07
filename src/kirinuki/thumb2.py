"""第2版のサムネ（DESIGN.md 9.4）：顔の背景を消して大きく切り抜き、左上にゲーム名、下に1行の大きな文字。"""

from __future__ import annotations

import base64
import os
import subprocess
import zlib
from pathlib import Path

from kirinuki import overlay
from kirinuki.fetch import Segment

W, H = 1280, 720
PICK_PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "pick_thumb_frame.md"
PICK_MODEL = "claude-haiku-5-5"     # 6枚から表情の強い1枚を選ぶだけなので安いモデル
PICK_OFFSETS = (-6.0, -4.0, -2.0, 0.0, 2.0, 4.0)
SPARE_OFFSETS = (-12.0, -9.0, 6.0, 9.0)   # 暗い・ぼやけた枚を外したときの予備
DARK = 45            # 明るさの平均（0〜255）がこれ未満は暗転・暗い画面として使わない
BLUR_RATIO = 0.6     # 輪郭の量が、いちばんはっきりした枚のこの割合未満はぼやけとして使わない
PICK_SCHEMA = {"type": "object", "properties": {"index": {"type": "integer"}},
               "required": ["index"], "additionalProperties": False}
FACE_H = 680          # 切り抜いた顔（上半身）の高さ（画面の高さの9割強。2026-11-05 もっと大きく）
BORDER = 9           # 切り抜いた顔の白い縁（切り抜いた時点の画素）
FACE_SCALE = 1.5      # カメラ枠より少し広く切り出して、肩まで入れる（広すぎると顔が小さくなる）
_session = None


# 大きい文字の色（ASS は &HAABBGGRR）。赤か白に絞る（2026-11-03：黄色や水色は読みにくいという指摘）
PALETTE = (("赤", "&H001E1EF0"), ("白", "&H00FFFFFF"))


def pick_colors(text: str) -> tuple[str, str]:
    """(大きい文字の色, 添える言葉の色)。同じ文字なら同じ色、違えば色がばらける。"""
    k = zlib.crc32(text.encode("utf-8"))
    main = PALETTE[k % len(PALETTE)][1]
    sub = PALETTE[(k // len(PALETTE) + 1 + k % len(PALETTE)) % len(PALETTE)][1]
    return main, (sub if sub != main else "&H00FFFFFF")


NO_HEAD = set("ゃゅょっぁぃぅぇぉャュョッァィゥェォー、。！？!?）)」』〜…")


PARTICLES = set("のにをがはでともてへやかよ")
COMPOUNDS = {"のに", "とも", "ても", "では", "には", "でも", "とか", "かも", "ので", "のは", "って"}


def _kind(c: str) -> str:
    if "\u3040" <= c <= "\u309f":
        return "ひら"
    if "\u30a0" <= c <= "\u30ff":
        return "カタ"
    return "漢" if "\u4e00" <= c <= "\u9fff" else "他"


def split_lines(text: str) -> list[str]:
    """7字を超えたら2段に分ける。助詞の後や字の種類が変わる所で、なるべく真ん中に近い所を選ぶ。"""
    if overlay.display_len(text) <= 7:
        return [text]
    best, score = 1, 1e9
    for i in range(1, len(text)):
        if text[i] in NO_HEAD:
            continue
        d = abs(overlay.display_len(text[:i]) - overlay.display_len(text[i:]))
        nice = text[i - 1] in PARTICLES and text[i - 1:i + 1] not in COMPOUNDS or (_kind(text[i - 1]) != _kind(text[i]) and _kind(text[i]) != "ひら")
        # カタカナや漢字の続きの途中（「寿司ドー｜ム」など）は言葉が割れるので強く避ける
        inside_word = _kind(text[i - 1]) == _kind(text[i]) and _kind(text[i]) in ("カタ", "漢")
        d += 0 if nice else (6 if inside_word else 2.5)
        if d < score:
            best, score = i, d
    return [text[:best], text[best:]]


ONE_LINE_MIN = 150   # 1行でこの大きさを下回るなら2行に分ける
GLYPH = 0.7          # この書体の1文字の幅は、文字の大きさのおよそ0.7倍（見本で測った値）
EMPH = 1.5           # 大事な一語の大きさ（ほかの文字の何倍か。2026-11-05 オーナー判断）


def emph_span(text: str, emph: str) -> tuple[int, int] | None:
    """大きくする語の位置。text に含まれない・全体と同じ・空なら None（全部同じ大きさ）。"""
    emph = (emph or "").strip()
    i = text.find(emph) if emph else -1
    return (i, i + len(emph)) if i >= 0 and emph != text else None


def _units(text: str, span: tuple[int, int] | None, offset: int = 0) -> float:
    """大きくする語の分を足した、文字の幅の合計（全角1）。"""
    n = overlay.display_len(text)
    if span:
        a, b = max(span[0] - offset, 0), min(span[1] - offset, len(text))
        if a < b:
            n += (EMPH - 1) * overlay.display_len(text[a:b])
    return n


def _fit(units: float, max_size: int) -> int:
    return int(min(max_size, (W - 60) / GLYPH / max(units, 1.0)))


def _markup(line: str, span: tuple[int, int] | None, offset: int, size: int) -> str:
    if span:
        a, b = max(span[0] - offset, 0), min(span[1] - offset, len(line))
        if a < b:
            return (overlay.ass_escape(line[:a]) + f"{{\\fs{round(size * EMPH)}}}" + overlay.ass_escape(line[a:b])
                    + f"{{\\fs{size}}}" + overlay.ass_escape(line[b:]))
    return overlay.ass_escape(line)


def caption_ass(text: str, game: str, bubbles: tuple[str, ...] = (), emph: str = "", badge: str = "") -> str:
    """下に1行の大きな言葉（赤か白、白フチ＋黒フチの二重縁）と、左上に小さめのゲーム名。
    emph（大事な一語）だけ EMPH 倍に大きくする。
    チャットの言葉（bubbles）は入れない（2026-11-03 オーナー判断。引数は互換のため残す）。
    badge（「日本語字幕付き」など）は右上に、黄色の帯＋黒い文字で入れる。"""
    main, _ = pick_colors(text)
    inner = "&H00000000" if main == "&H00FFFFFF" else "&H00FFFFFF"   # 白い文字は内側の縁を黒に
    game = "" if game in ("Just Chatting", "雑談") else game        # 雑談は出さない
    span = emph_span(text, emph)
    lines = [text] if text else []
    size = _fit(_units(text, span), 230) if text else 0
    if text and size < ONE_LINE_MIN:
        lines = split_lines(text)
        if span and span[0] < len(lines[0]) < span[1]:   # 大事な一語の途中では割らない
            cut = min((span[0], span[1]), key=lambda c: abs(overlay.display_len(text[:c]) - overlay.display_len(text[c:]))
                      if 0 < c < len(text) else 1e9)
            lines = [text[:cut], text[cut:]] if 0 < cut < len(text) else [text]
        offs = [0, len(lines[0])]
        size = _fit(max(_units(x, span, o) for x, o in zip(lines, offs)), 200)
    styles = [
        # 大きい文字は2枚重ね：下に太い黒縁、上に白縁＋色文字（白縁の外を黒で囲む二重の縁）
        overlay.style("CapOut", overlay.TITLE_FONT, 160, overlay.BLACK, 30, alignment=2),
        "Style: Cap," + overlay.TITLE_FONT + f",160,{main},{main},{inner},&H00000000,"
        "-1,0,0,0,100,100,0,0,1,13,0,2,0,0,0,1",
        # ゲーム名は左上に小さめ：黒い帯＋白い文字
        "Style: Game," + overlay.TITLE_FONT + ",52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,3,10,0,7,0,0,0,1",
        # 右上の札（日本語字幕付き など）：黄色の帯＋黒い文字
        "Style: Badge," + overlay.TITLE_FONT + ",64,&H00000000,&H00000000,&H0000FFFF,&H00000000,"
        "-1,0,0,0,100,100,0,0,3,12,0,9,0,0,0,1",
    ]
    ev = []
    if text:
        body = "\\N".join(_markup(x, span, o, size) for x, o in zip(lines, [0, len(lines[0])]))
        pos = f"{{\\an2\\pos({W // 2},{H - 24})\\fs{size}}}{body}"
        ev.append(f"Dialogue: 1,0:00:00.00,0:00:10.00,CapOut,,0,0,0,,{pos}")
        ev.append(f"Dialogue: 2,0:00:00.00,0:00:10.00,Cap,,0,0,0,,{pos}")
    if game:
        ev.append(f"Dialogue: 2,0:00:00.00,0:00:10.00,Game,,0,0,0,,{{\\an7\\pos(24,20)}}{overlay.ass_escape(game)}")
    if badge:
        ev.append(f"Dialogue: 2,0:00:00.00,0:00:10.00,Badge,,0,0,0,,{{\\an9\\pos({W - 24},20)}}{overlay.ass_escape(badge)}")
    return overlay.ass_document(W, H, styles, ev)


def extract_frame(seg: Segment, at_sec: float, out_jpg: Path, width: int = 0) -> None:
    scale = ["-vf", f"scale={width}:-2"] if width else []
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{max(0.0, at_sec - seg.start_sec):.3f}", "-i", str(seg.path.resolve()),
                    "-frames:v", "1", *scale, "-q:v", "2", str(out_jpg.resolve())], check=True)


def frame_stats(jpg: Path, face: tuple[float, float, float] | None = None) -> tuple[float, float]:
    """(明るさの平均, 輪郭の量)。輪郭が少ないほどぼやけている。
    カメラ枠が分かれば、輪郭はその中で測る（顔だけ動いてぶれた枚を外すため）。"""
    from PIL import Image, ImageFilter, ImageStat
    g = Image.open(jpg).convert("L")
    part = g
    if face:
        w, h = g.size
        side = face[2] * h
        part = g.crop((max(0, int(face[0] * w - side / 2)), max(0, int(face[1] * h - side / 2)),
                       min(w, int(face[0] * w + side / 2)), min(h, int(face[1] * h + side / 2))))
    return ImageStat.Stat(g).mean[0], ImageStat.Stat(part.filter(ImageFilter.FIND_EDGES)).mean[0]


def usable(stats: list[tuple[float, float]]) -> list[bool]:
    """暗い枚・ぼやけた枚を外す（2026-11-05 オーナー判断：暗い・ぼやけたサムネは使わない）。全部だめなら全部使う。"""
    top = max((e for _, e in stats), default=0)
    ok = [b >= DARK and e >= top * BLUR_RATIO for b, e in stats]
    return ok if any(ok) else [True] * len(stats)


def pick_moment(seg: Segment, at_sec: float, work: Path, face: tuple[float, float, float] | None = None) -> float:
    """前後数秒の枚から、暗い・ぼやけた枚を外し、加藤さんの表情が一番強い瞬間を Claude に選ばせる。"""
    offs = PICK_OFFSETS + SPARE_OFFSETS
    secs = [s for s in (at_sec + o for o in offs) if seg.start_sec <= s < seg.end_sec - 0.5]
    if len(secs) < 2:
        return at_sec
    files = [work / f"pick_{k}.jpg" for k in range(len(secs))]
    try:
        for s, f in zip(secs, files):
            extract_frame(seg, s, f, width=640)
        ok = usable([frame_stats(f, face) for f in files])
        # 予備の枚は、ふだんの6枚で使えるものが足りないときだけ使う
        main = [k for k in range(len(secs)) if ok[k] and secs[k] - at_sec in PICK_OFFSETS]
        keep = main if len(main) >= 2 else [k for k in range(len(secs)) if ok[k]]
        keep = sorted(keep, key=lambda k: abs(secs[k] - at_sec))[: len(PICK_OFFSETS)]
        keep.sort(key=lambda k: secs[k])
        if len(keep) < 2 or not os.environ.get("ANTHROPIC_API_KEY") or not PICK_PROMPT.exists():
            return secs[min(keep, key=lambda k: abs(secs[k] - at_sec))] if keep else at_sec   # いちばん近い使える枚
        from kirinuki import select
        content = [{"type": "text", "text": PICK_PROMPT.read_text(encoding="utf-8").replace("{n}", str(len(keep)))}]
        for k, i in enumerate(keep):
            f = files[i]
            content += [{"type": "text", "text": f"{k}："},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.standard_b64encode(f.read_bytes()).decode()}}]
        raw, _ = select.call_claude(PICK_MODEL, content, PICK_SCHEMA)
        i = int(raw.get("index", -1))
        return secs[keep[i]] if 0 <= i < len(keep) else secs[keep[0]]
    except Exception as e:  # noqa: BLE001 — 選べなくてもサムネは作る
        print(f"[kirinuki] サムネの瞬間選びに失敗（{e}）", flush=True)
        return at_sec
    finally:
        for f in files:
            f.unlink(missing_ok=True)


def face_cutout(frame_jpg: Path, face: tuple[float, float, float], out_png: Path) -> bool:
    """カメラ枠の周りを切り出し、人物以外を透明にした PNG を作る。失敗したら False。"""
    global _session
    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError:
        return False
    img = Image.open(frame_jpg).convert("RGB")
    iw, ih = img.size
    fx, fy, fs = face
    side = fs * ih * FACE_SCALE
    cx, cy = fx * iw, fy * ih
    box = (max(0, int(cx - side / 2)), max(0, int(cy - side / 2)),
           min(iw, int(cx + side / 2)), min(ih, int(cy + side / 2)))
    if _session is None:
        _session = new_session("u2net_human_seg")
    cut = remove(img.crop(box), session=_session)
    bbox = cut.getbbox()
    if not bbox or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 0.05 * cut.size[0] * cut.size[1]:
        return False   # 人物がほとんど見つからない
    # 白い縁を付けて背景から浮かせる（参考チャンネルのサムネのように）
    from PIL import ImageFilter
    pad = BORDER + 2
    big = Image.new("RGBA", (cut.size[0] + 2 * pad, cut.size[1] + 2 * pad), (0, 0, 0, 0))
    big.paste(cut, (pad, pad))
    edge = (big.split()[3].point(lambda a: 255 if a > 128 else 0).filter(ImageFilter.MaxFilter(BORDER * 2 + 1))
            .filter(ImageFilter.GaussianBlur(2)))   # 縁のギザギザをならす
    framed = Image.new("RGBA", big.size, (255, 255, 255, 0))
    framed.putalpha(edge)
    framed.alpha_composite(big)
    framed.crop(framed.getbbox()).save(out_png)
    return True


def render(seg: Segment, at_sec: float, text: str, game: str, face: tuple[float, float, float] | None,
           bubbles: tuple[str, ...], out_jpg: Path, emph: str = "", badge: str = "") -> bool:
    """サムネを作る。顔の切り抜きに成功すれば True（失敗時は顔なしで作る）。"""
    work = out_jpg.parent
    work.mkdir(parents=True, exist_ok=True)
    frame = work / f"{out_jpg.stem}_frame.jpg"
    png = work / f"{out_jpg.stem}_face.png"
    ass = work / f"{out_jpg.stem}.ass"
    extract_frame(seg, pick_moment(seg, at_sec, work, face), frame)
    ok = bool(face) and face_cutout(frame, face, png)
    ass.write_text(caption_ass(text, game, emph=emph, badge=badge), encoding="utf-8")
    bg = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=saturation=1.3:contrast=1.08"
    if ok:
        graph = (f"[0:v]{bg}[bg];[1:v]scale=-1:{FACE_H}[f];"
                 f"[bg][f]overlay=W-w-10:H-h,ass=filename={ass.name}[v]")
        inputs = ["-i", str(frame.resolve()), "-i", str(png.resolve())]
    else:
        graph = f"[0:v]{bg},ass=filename={ass.name}[v]"
        inputs = ["-i", str(frame.resolve())]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", *inputs,
                    "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-q:v", "3",
                    str(out_jpg.resolve())], check=True, cwd=work)
    for p in (frame, png, ass):
        p.unlink(missing_ok=True)
    return ok
