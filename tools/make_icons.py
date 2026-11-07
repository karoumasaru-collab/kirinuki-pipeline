"""チャンネルアイコンの見本を作る（800x800。YouTubeでは丸く切り抜かれるので、中身は中央の円に収める）。

GitHub Actions（Noto Sans CJK JP Black が入っている環境）で動かす：python tools/make_icons.py out/
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 800
BLACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=0)   # index 0 = JP


def ctext(d: ImageDraw.ImageDraw, cy: int, text: str, f, fill) -> None:
    """文字の見た目の中心が (中央, cy) に来るように描く。"""
    d.text((S / 2, cy), text, font=f, fill=fill, anchor="mm")


def a_mono(out: Path) -> None:
    """黒地に白の「最速」、細い黄色の線と小さな文字。"""
    im = Image.new("RGB", (S, S), "#121212")
    d = ImageDraw.Draw(im)
    ctext(d, 350, "最速", font(BLACK, 250), "#FFFFFF")
    d.rectangle((270, 492, 530, 500), fill="#FFD400")
    ctext(d, 555, "加藤純一 切り抜き", font(REG, 46), "#BDBDBD")
    im.save(out / "A_モノクロ.png")


def b_navy(out: Path) -> None:
    """濃紺に黄色の大きな「速」1文字。"""
    im = Image.new("RGB", (S, S), "#0D1B2E")
    d = ImageDraw.Draw(im)
    ctext(d, 350, "速", font(BLACK, 360), "#FFD400")
    ctext(d, 590, "SAISOKU KIRINUKI", font(REG, 40), "#E8EEF5")
    im.save(out / "B_ネイビー.png")


def c_white(out: Path) -> None:
    """白地に黒の「最速」、赤い丸を1つ。"""
    im = Image.new("RGB", (S, S), "#F7F5F0")
    d = ImageDraw.Draw(im)
    d.ellipse((530, 170, 600, 240), fill="#E53935")
    ctext(d, 375, "最速", font(BLACK, 250), "#141414")
    ctext(d, 560, "加藤純一 切り抜き", font(REG, 44), "#555555")
    im.save(out / "C_白.png")


def d_split(out: Path) -> None:
    """斜めに黒と黄色で分けた地に「最速」（黄色にかかる部分の文字は黒）。"""
    top = [(0, 0), (S, 0), (S, S * 0.40), (0, S * 0.60)]
    im = Image.new("RGB", (S, S), "#141414")
    d = ImageDraw.Draw(im)
    ctext(d, 370, "最速", font(BLACK, 250), "#FFFFFF")
    low = Image.new("RGB", (S, S), "#FFD400")
    ld = ImageDraw.Draw(low)
    ctext(ld, 370, "最速", font(BLACK, 250), "#141414")
    ctext(ld, 570, "加藤純一 切り抜き", font(REG, 42), "#141414")
    mask = Image.new("L", (S, S), 255)
    ImageDraw.Draw(mask).polygon(top, fill=0)
    im.paste(low, (0, 0), mask)
    im.save(out / "D_黒黄.png")


def e_sign(out: Path) -> None:
    """道路の「最高速度」の標識をもじる：赤い輪の中に青い「最速」。"""
    im = Image.new("RGB", (S, S), "#F2F2F2")
    d = ImageDraw.Draw(im)
    d.ellipse((60, 60, S - 60, S - 60), fill="#C8102E")
    d.ellipse((150, 150, S - 150, S - 150), fill="#FFFFFF")
    ctext(d, 385, "最速", font(BLACK, 200), "#1D3C8F")
    ctext(d, 520, "加藤純一", font(REG, 46), "#1D3C8F")
    im.save(out / "E_標識.png")


def f_station(out: Path) -> None:
    """駅名標をもじる：「さいそく」駅、前後の駅は「はいしん」「きりぬき」。"""
    im = Image.new("RGB", (S, S), "#FFFFFF")
    d = ImageDraw.Draw(im)
    ctext(d, 265, "最速", font(BLACK, 180), "#141414")      # 文字はおよそ 175〜355
    ctext(d, 405, "さいそく", font(REG, 50), "#141414")      # ふりがなは 380〜430 で、上の文字と離す
    d.rectangle((0, 455, S, 525), fill="#008F4C")
    d.polygon([(S - 125, 455), (S - 65, 490), (S - 125, 525)], fill="#FFFFFF")
    ctext(d, 490, "SAISOKU", font(REG, 40), "#FFFFFF")
    d.text((125, 570), "はいしん", font=font(REG, 40), fill="#333333", anchor="lm")
    d.text((S - 125, 570), "きりぬき", font=font(REG, 40), fill="#333333", anchor="rm")
    im.save(out / "F_駅名標.png")


def g_flag(out: Path) -> None:
    """レースのチェッカーフラッグ：白黒の市松の上に黄色い帯と「最速」。"""
    im = Image.new("RGB", (S, S), "#FFFFFF")
    d = ImageDraw.Draw(im)
    n = 8
    c = S // n
    for i in range(n):
        for j in range(n):
            if (i + j) % 2:
                d.rectangle((i * c, j * c, (i + 1) * c, (j + 1) * c), fill="#141414")
    d.rectangle((0, 250, S, 550), fill="#FFD400")
    ctext(d, 390, "最速", font(BLACK, 230), "#141414")
    im.save(out / "G_チェッカー.png")


def preview(out: Path) -> None:
    """4案を丸く切り抜いて横に並べた見本（YouTubeでの見え方）。"""
    files = sorted(p for p in out.glob("*.png") if not p.name.startswith("_"))
    cols = 4
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 420 + 20, rows * 420 + 20), "#FFFFFF")
    m = Image.new("L", (400, 400), 0)
    ImageDraw.Draw(m).ellipse((0, 0, 399, 399), fill=255)
    for k, p in enumerate(files):
        sheet.paste(Image.open(p).resize((400, 400)), (20 + (k % cols) * 420, 20 + (k // cols) * 420), m)
    sheet.save(out / "_見本.png")


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for fn in (a_mono, b_navy, c_white, d_split, e_sign, f_station, g_flag):
        fn(out)
    preview(out)
