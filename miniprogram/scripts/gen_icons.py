"""生成湖畔手账风格矢量图标（手绘线条风 PNG，透明底）

用法：python scripts/gen_icons.py（需先 pip install pillow）
产物：src/assets/icons/ 下 tabBar 图标（81×81 两态色）+ 页面点缀图标
设计语言：线条手绘感（原型 .waves 水波纹 / 手账简笔人像 / 新叶）
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "assets" / "icons"

GRAY = (107, 122, 114, 255)  # 晨雾灰 #6B7A72（tabBar 未选中）
LAKE = (58, 107, 92, 255)    # 湖水青碧 #3A6B5C（tabBar 选中）
LINE = 5                     # 线宽（81px 画布下的手绘笔触）


def new_canvas(size: int = 81) -> Image.Image:
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def draw_sine_wave(draw: ImageDraw.ImageDraw, y0: float, amp: float, wavelength: float, phase: float, color) -> None:
    """正弦波浪线（圆头端点）：湖畔水波"""
    pts = []
    for x in range(10, 72):
        y = y0 + amp * math.sin(2 * math.pi * (x - 10) / wavelength + phase)
        pts.append((x, y))
    draw.line(pts, fill=color, width=LINE, joint="curve")
    r = LINE / 2
    for (x, y) in (pts[0], pts[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def draw_walk(color) -> Image.Image:
    """漫步：三道水波（呼应原型 .waves 波浪线）"""
    img = new_canvas()
    d = ImageDraw.Draw(img)
    draw_sine_wave(d, 30, 3, 26, 0, color)
    draw_sine_wave(d, 43, 3, 26, math.pi / 2, color)
    draw_sine_wave(d, 56, 3, 26, math.pi, color)
    return img


def draw_profile(color) -> Image.Image:
    """我的：手账简笔人像（圆头 + 肩弧）"""
    img = new_canvas()
    d = ImageDraw.Draw(img)
    cx, head_y, r = 40.5, 30, 11
    d.ellipse((cx - r, head_y - r, cx + r, head_y + r), outline=color, width=LINE)
    # 肩弧（椭圆下半弧，两端自然收尾）
    d.arc((15, 48, 66, 96), 200, 340, fill=color, width=LINE)
    return img


def draw_leaf(color, size: int = 72) -> Image.Image:
    """新叶（页面点缀）：旋转椭圆叶形 + 中脉 + 叶柄"""
    img = new_canvas(size)
    d = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    a, b = 14, 25  # 半轴
    ang = math.radians(30)  # 叶轴倾角
    pts = []
    for t in range(0, 361, 8):
        x = a * math.cos(math.radians(t))
        y = b * math.sin(math.radians(t))
        pts.append((cx + x * math.cos(ang) - y * math.sin(ang), cy + x * math.sin(ang) + y * math.cos(ang)))
    d.line(pts + [pts[0]], fill=color, width=LINE, joint="curve")
    # 中脉（沿长轴）
    d.line([(cx - 17, cy + 10), (cx + 17, cy - 10)], fill=color, width=3)
    # 叶柄（沿长轴向下延伸）
    d.line([(cx - 19, cy + 12), (cx - 26, cy + 19)], fill=color, width=3)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw_walk(GRAY).save(OUT / "tab-walk.png")
    draw_walk(LAKE).save(OUT / "tab-walk-active.png")
    draw_profile(GRAY).save(OUT / "tab-profile.png")
    draw_profile(LAKE).save(OUT / "tab-profile-active.png")
    draw_leaf(LAKE).save(OUT / "leaf.png")
    print("图标已生成到", OUT)


if __name__ == "__main__":
    main()
