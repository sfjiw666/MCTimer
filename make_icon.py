# -*- coding: utf-8 -*-
"""生成应用图标 assets/icon.ico（蓝色圆角方块 + 白色时钟 + ¥ 符号）"""
import os
from PIL import Image, ImageDraw, ImageFont

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# iOS 风格圆角方块（系统蓝，垂直方向轻微渐变）
r = 58
for y in range(S):
    t = y / S
    color = (
        int(0 + (10 - 0) * t),
        int(122 + (132 - 122) * t),
        int(255 + (252 - 255) * t),
        255,
    )
    d.line([(0, y), (S, y)], fill=color)
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
img.putalpha(mask)

# 白色时钟表盘
cx, cy, rr = S // 2, S // 2, 86
d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(255, 255, 255, 255), width=10)

# 时针指 10 点、分针指 10 分（约 10:10，经典钟表美学）
import math
for angle_deg, length, width in ((300, 44, 12), (60, 66, 12)):
    a = math.radians(angle_deg)
    d.line([(cx, cy), (cx + math.cos(a) * length, cy - math.sin(a) * length)],
           fill=(255, 255, 255, 255), width=width)
d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=(255, 255, 255, 255))

# 中央 ¥ 符号
try:
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 54)
except Exception:
    font = ImageFont.load_default()
d.text((cx, cy + 2), "¥", font=font, fill=(255, 255, 255, 255), anchor="mm")

os.makedirs("assets", exist_ok=True)
img.save("assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save("assets/icon.png")
print("icon written to assets/icon.ico")
