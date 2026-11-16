"""Render the six fortune templates into full-size demo screenshots.

All demo data is synthetic: QQ ``00000000``, a generated SVG avatar and a
generated abstract background. No real user avatar, QQ number or photo is used
or read.

Run on a normal desktop session. In the DSH sandbox Chromium cannot be started
directly, so start a headless shell with a CDP port and set
``FORTUNE_PREVIEW_CDP_URL=http://127.0.0.1:9222``.
"""

from __future__ import annotations

import importlib.util
import io
import os
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nonebot_plugin_yrr_fortune"
TEMPLATES = PACKAGE / "templates"
OUT = PACKAGE
DOCS = ROOT / "docs"

CHROMIUM = Path(
    os.environ.get(
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
        r"E:\bot3\runtime\playwright\chromium-1187\chrome-win\chrome.exe",
    )
)
FONT = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
KANA_FONT = Path(r"C:\Windows\Fonts\YuGothM.ttc")

NAMES = {1: "经典", 2: "杂志", 3: "写真", 4: "水墨", 5: "星际", 6: "漫画"}
ACCENTS = {1: "#d4af37", 2: "#ff4d9d", 3: "#ff9f43", 4: "#8fa3b8", 5: "#00d2ff", 6: "#ffd93d"}
VIEWPORTS = {
    1: (1000, 1000),
    2: (1080, 1920),
    3: (1080, 1920),
    4: (1080, 1920),
    5: (1080, 1920),
    6: (1080, 1920),
}

# Entirely synthetic demo data.  The QQ number is deliberately invalid.
DEMO_QQ = "00000000"
SAMPLE_USER = {
    "qq_number": DEMO_QQ,
    "fortune": "大吉|★★★★★★☆",
    "fortune_date": "2026-11-16",
    "fortune_text": "今天适合把想了很久的事情动手做起来，越主动越顺利。",
    "radar_data": [
        {"axis": "财运", "value": 0.92},
        {"axis": "事业", "value": 0.78},
        {"axis": "健康", "value": 0.64},
        {"axis": "人缘", "value": 0.86},
        {"axis": "爱情", "value": 0.72},
        {"axis": "智慧", "value": 0.81},
    ],
    "lucky_items": {
        "color": "紫色",
        "number": 7,
        "direction": "东南",
        "good": "搞钱、出门放风",
        "bad": "熬夜修仙、冲动消费",
    },
    "template_choice": "1",
}


def load_core():
    spec = importlib.util.spec_from_file_location("yrr_preview_core", PACKAGE / "core.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def font_rule() -> str:
    if not FONT.is_file():
        return ""
    uri = FONT.resolve().as_uri()
    return f"@font-face {{ font-family: 'MsyhCustom'; src: url('{uri}') format('truetype'); }}"


def make_demo_background(path: Path) -> None:
    """Abstract aurora background, generated locally; contains no photo/person."""

    width, height = 1080, 1920
    canvas = Image.new("RGB", (width, height), "#0b1020")
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        t = y / (height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(int(11 + 22 * t), int(16 + 8 * t), int(32 + 44 * t)),
        )

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-220, 120, 520, 900], fill=(165, 94, 234, 80))
    gd.ellipse([620, 380, 1320, 1180], fill=(0, 210, 255, 55))
    gd.ellipse([-120, 1220, 620, 2020], fill=(255, 77, 157, 45))
    gd.ellipse([700, 1420, 1320, 2040], fill=(255, 217, 61, 32))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")

    draw = ImageDraw.Draw(canvas, "RGBA")
    rng = random.Random(7)
    for _ in range(170):
        x, y = rng.randrange(width), rng.randrange(height)
        r = rng.choice([1, 1, 1, 2])
        draw.ellipse([x, y, x + r, y + r], fill=(255, 255, 255, rng.randrange(18, 70)))

    noise = Image.effect_noise((width, height), 6).convert("L")
    canvas = Image.composite(canvas, Image.new("RGB", (width, height), "#0b1020"), noise.point(lambda v: 235))
    canvas.save(path, "JPEG", quality=92, optimize=True)


def rounded_card(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.resize(size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=20, fill=255)
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    card.paste(image, (0, 0), mask)
    return card


def paste_card(canvas: Image.Image, image: Image.Image, x: int, y: int, size: tuple[int, int]) -> None:
    card = rounded_card(image, size)
    shadow = Image.new("RGBA", (size[0] + 48, size[1] + 48), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [18, 20, size[0] + 20, size[1] + 22], radius=22, fill=(0, 0, 0, 150)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas.paste(shadow, (x - 18, y - 18), shadow)
    canvas.paste(card, (x, y), card)
    ImageDraw.Draw(canvas, "RGBA").rounded_rectangle(
        [x, y, x + size[0] - 1, y + size[1] - 1], radius=20,
        outline=(255, 255, 255, 40), width=1,
    )


def make_help(previews: dict[int, Image.Image]) -> Image.Image:
    """Full-card contact sheet; each card keeps its native aspect ratio."""

    card_h = 1100
    gap = 24
    margin = 40
    label_h = 80
    row_gap = 40

    def scaled(index: int) -> Image.Image:
        image = previews[index]
        width = round(image.width * card_h / image.height)
        return image.resize((width, card_h), Image.Resampling.LANCZOS)

    row1 = [scaled(i) for i in (1, 2, 3)]
    row2 = [scaled(i) for i in (4, 5, 6)]
    width1 = sum(i.width for i in row1) + gap * 2
    width2 = sum(i.width for i in row2) + gap * 2
    canvas_w = max(width1, width2) + margin * 2
    canvas_h = margin * 2 + card_h * 2 + label_h * 2 + row_gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), "#0d1117")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.truetype(str(FONT_BOLD), 46)

    for row_index, row in enumerate((row1, row2)):
        total = sum(i.width for i in row) + gap * 2
        x = (canvas_w - total) // 2
        y = margin + row_index * (card_h + label_h + row_gap)
        for offset, card in enumerate(row):
            index = row_index * 3 + offset + 1
            paste_card(canvas, card, x, y, (card.width, card_h))
            label = f"{index}  ·  {NAMES[index]}"
            draw.text(
                (x + card.width // 2, y + card_h + 12), label,
                font=font, fill=ACCENTS[index], anchor="ma",
            )
            x += card.width + gap
    return canvas


def make_cover(previews: dict[int, Image.Image]) -> Image.Image:
    """1920x1080 hero image using three full 9:16 cards, never cropped."""

    width, height = 1920, 1080
    canvas = Image.new("RGB", (width, height), "#0b1020")
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        t = y / (height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(int(11 + 15 * t), int(16 + 0 * t), int(32 + 26 * t)),
        )

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([1240, -260, 2100, 560], fill=(165, 94, 234, 75))
    gd.ellipse([-320, 560, 560, 1380], fill=(0, 210, 255, 30))
    gd.ellipse([900, 620, 1800, 1320], fill=(255, 71, 87, 24))
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")

    f_label = ImageFont.truetype(str(FONT), 24)
    f_title = ImageFont.truetype(str(FONT_BOLD), 128)
    f_sub = ImageFont.truetype(str(FONT), 38)
    f_chip = ImageFont.truetype(str(FONT), 24)
    f_small = ImageFont.truetype(str(FONT), 21)
    f_mark = ImageFont.truetype(str(KANA_FONT), 26)
    f_templates = ImageFont.truetype(str(FONT), 22)
    white = (245, 247, 252)
    accent = (165, 94, 234)
    muted = (139, 147, 167)

    draw.rounded_rectangle([110, 128, 160, 135], radius=3, fill=accent)
    draw.text((110, 158), "NONEBOT  ·  ONEBOT V11", font=f_label, fill=muted)

    title_y = 215
    draw.text((110, title_y), "YRR_", font=f_title, fill=white)
    offset = draw.textlength("YRR_", font=f_title)
    draw.text((110 + offset, title_y), "运势", font=f_title, fill=accent)
    draw.text((112, 395), "每天一张运势卡 · 六维雷达 · 幸运指南", font=f_sub, fill=(201, 209, 224))

    chips = ["七档运势", "六套模板", "互斥宜忌", "离线兜底"]
    x, chip_y = 110, 485
    for text in chips:
        text_w = draw.textlength(text, font=f_chip)
        box_w = text_w + 40
        draw.rounded_rectangle(
            [x, chip_y, x + box_w, chip_y + 46], radius=23,
            fill=(255, 255, 255, 10), outline=(255, 255, 255, 38), width=1,
        )
        draw.text((x + 20, chip_y + 9), text, font=f_chip, fill=(215, 220, 232))
        x += box_w + 14

    card_h = 560
    gap = 18
    cards = []
    for index in (2, 4, 6):
        image = previews[index]
        card_w = round(image.width * card_h / image.height)
        cards.append((index, image, card_w))
    total_w = sum(c[2] for c in cards) + gap * 2
    x = width - 90 - total_w
    y = (height - card_h) // 2 + 20
    draw.text((x, y - 42), "6  TEMPLATES", font=f_templates, fill=muted)

    for index, image, card_w in cards:
        paste_card(canvas, image, x, y, (card_w, card_h))
        x += card_w + gap

    mark = "プリン bot"
    mark_w = draw.textlength(mark, font=f_mark)
    draw.rounded_rectangle(
        [110, 872, 110 + mark_w + 30, 872 + 42], radius=21,
        fill=(0, 0, 0, 90), outline=(255, 255, 255, 40), width=1,
    )
    draw.text((125, 879), mark, font=f_mark, fill=(235, 238, 245))
    draw.text(
        (110, 948), "github.com/YRRLYB/nonebot-plugin-yrr-fortune",
        font=f_small, fill=(125, 134, 154),
    )
    return canvas


def main() -> int:
    if not CHROMIUM.is_file():
        print(f"Chromium not found: {CHROMIUM}")
        return 2

    DOCS.mkdir(exist_ok=True)
    demo_bg = ROOT / "scripts" / "_demo_bg.jpg"
    make_demo_background(demo_bg)

    core = load_core()
    context = core.build_render_context(
        user=SAMPLE_USER,
        level="大吉",
        stars="★★★★★★☆",
        background_image=demo_bg.resolve().as_uri(),
        avatar_url=core.avatar_data_uri(DEMO_QQ),
        font_face_rule=font_rule(),
    )

    previews: dict[int, Image.Image] = {}
    cdp_url = os.environ.get("FORTUNE_PREVIEW_CDP_URL")
    with sync_playwright() as playwright:
        if cdp_url:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            own_browser = False
        else:
            browser = playwright.chromium.launch(
                executable_path=str(CHROMIUM),
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-crash-reporter"],
            )
            own_browser = True

        try:
            for index in range(1, 7):
                template = (TEMPLATES / f"{index}.html").read_text(encoding="utf-8")
                html = template.format(**context)
                viewport = {"width": VIEWPORTS[index][0], "height": VIEWPORTS[index][1]}
                page_context = browser.new_context(viewport=viewport, device_scale_factor=2)
                page = page_context.new_page()
                try:
                    page.goto(ROOT.as_uri())
                    page.set_content(html, wait_until="networkidle")
                    page.evaluate("() => document.fonts.ready.then(() => true)")
                    page.wait_for_timeout(250)
                    raw = page.screenshot(full_page=True, type="jpeg", quality=95)
                finally:
                    page.close()
                    page_context.close()

                image = Image.open(io.BytesIO(raw)).convert("RGB")
                # Full card, native aspect ratio: no crop, no squash.
                image.save(OUT / f"preview-{index}.jpg", quality=93, optimize=True)
                previews[index] = image
                print(f"rendered template {index}: {image.size}")
        finally:
            if own_browser:
                browser.close()

    make_help(previews).save(OUT / "help.jpg", quality=90, optimize=True)
    make_cover(previews).save(DOCS / "cover.jpg", quality=92, optimize=True)
    demo_bg.unlink(missing_ok=True)
    print(f"wrote {OUT / 'help.jpg'} and {DOCS / 'cover.jpg'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
