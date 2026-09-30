"""Renders the Foxi mascot icon: assets/foxi.ico (16-256 px) + assets/foxi.png (512 px, for the README).
Same fox as the sidebar logo (ui/index.html). Run: .venv\\Scripts\\python tools\\make_icon.py"""
import os
from PIL import Image, ImageDraw

S = 1024                      # draw big, downscale = clean edges at every size
BG = (28, 24, 21, 255)        # --bg, warm near-black
ORANGE = (240, 124, 58, 255)  # --acc
EAR = (196, 88, 36, 255)      # darker inner ear
CREAM = (246, 234, 220, 255)
EYE = (40, 26, 18, 255)


def p(*pts):  # 32-unit design grid -> pixels, fox centred with margin
    k, off = S / 32 * 0.86, S * 0.07
    return [(x * k + off, y * k + off + S * 0.02) for x, y in pts]


def render():
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.22), fill=BG)
    d.polygon(p((4, 3.5), (12, 10.5), (20, 10.5), (28, 3.5), (27, 17), (16, 29), (5, 17)), fill=ORANGE)   # head
    d.polygon(p((6.2, 7), (10.5, 10.8), (7, 12.5)), fill=EAR)                                           # ears
    d.polygon(p((25.8, 7), (21.5, 10.8), (25, 12.5)), fill=EAR)
    d.polygon(p((9, 18), (16, 26), (23, 18), (16, 20.5)), fill=CREAM)                                   # muzzle
    d.polygon(p((14.6, 24.4), (17.4, 24.4), (16, 26)), fill=EYE)                                        # nose
    for cx in (11.6, 20.4):                                                                             # eyes
        (x0, y0), (x1, y1) = p((cx - 1.3, 14), (cx + 1.3, 16.6))
        d.ellipse((x0, y0, x1, y1), fill=EYE)
    return im


if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, 'assets')
    os.makedirs(out, exist_ok=True)
    big = render()
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(out, 'foxi.png'))
    sizes = [(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(out, 'foxi.ico'), sizes=sizes)
    print('wrote assets/foxi.ico and assets/foxi.png')
