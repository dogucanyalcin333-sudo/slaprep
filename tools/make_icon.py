"""slaprep/assets/slaprep.ico üretir: reçine damlası + plate çizgisi."""
from pathlib import Path

from PIL import Image, ImageDraw

out = Path(__file__).resolve().parent.parent / "slaprep" / "assets"
out.mkdir(parents=True, exist_ok=True)
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=48, fill=(32, 92, 160, 255))
# damla
cx, top, r = S // 2, 40, 62
d.polygon([(cx, top), (cx - r + 6, 140), (cx + r - 6, 140)], fill=(250, 196, 40, 255))
d.ellipse([cx - r, 120 - r + 40, cx + r, 120 + r + 40], fill=(250, 196, 40, 255))
d.ellipse([cx - 22, 150, cx + 2, 174], fill=(255, 236, 160, 255))
# plate
d.rounded_rectangle([40, 206, S - 40, 222], radius=6, fill=(230, 236, 245, 255))
img.save(out / "slaprep.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out / "slaprep.png")
print(out / "slaprep.ico")
