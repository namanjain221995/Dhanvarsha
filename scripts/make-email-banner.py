"""Animated email banner for the gold (Dhanvarsha) look: night-blue sky, gold arcs, the ticket
emblem, falling gold coins that turn as they fall, twinkling four-point stars and gold dust.

Email apps do not run CSS animation (Gmail strips it), but they play animated GIFs, so the
motion lives in the image. The first frame is a complete picture on its own, for Outlook on
Windows, which shows only that frame.

usage: python3 make_banner.py OUT.gif [WIDTH] [HEIGHT]
"""

import math
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = sys.argv[1]
OW = int(sys.argv[2]) if len(sys.argv) > 2 else 600
OH = int(sys.argv[3]) if len(sys.argv) > 3 else 220
S = 3  # drawn at 3x and scaled down, for smooth edges
W, H = OW * S, OH * S
FRAMES = 24
MS = 85  # per frame: a 2 s loop

TOP, MID, BOTTOM = (19, 40, 112), (12, 27, 79), (11, 24, 73)
GOLD_LIGHT, GOLD, GOLD_DEEP = (255, 233, 168), (244, 203, 82), (168, 116, 20)
RUPEE_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

rng = random.Random(20261028)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def background() -> Image.Image:
    img = Image.new("RGBA", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        c = lerp(TOP, MID, t / 0.55) if t < 0.55 else lerp(MID, BOTTOM, (t - 0.55) / 0.45)
        for x in range(W):
            px[x, y] = (*c, 255)
    # A soft blue glow behind the emblem.
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.18, -H * 0.55, W * 0.82, H * 0.95), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(70 * S))
    img = Image.composite(Image.new("RGBA", (W, H), (62, 104, 214, 255)), img, glow.point(lambda v: v * 0.55))
    # Gold arcs from the top corners, like the sign-in page's curtains of light.
    arcs = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(arcs)
    for side in (0, 1):
        for width, alpha, bend in ((5, 230, 0.0), (2, 140, 0.18), (2, 90, -0.16)):
            pts = []
            for i in range(61):
                t = i / 60
                # Quadratic curve from the upper corner down the side.
                x0, y0 = (0.20 + bend * 0.3) * W, -0.05 * H
                x1, y1 = (0.02 + bend * 0.1) * W, 0.25 * H
                x2, y2 = (-0.02) * W, (1.15 + bend) * H
                x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t**2 * x2
                y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t**2 * y2
                pts.append((W - x if side else x, y))
            fade = [(int(255 * (1 - i / 60)) * alpha) // 255 for i in range(61)]
            for i in range(60):
                d.line([pts[i], pts[i + 1]], fill=(246, 210, 107, fade[i]), width=width * S)
    halo = arcs.filter(ImageFilter.GaussianBlur(5 * S))
    img = Image.alpha_composite(img, halo)
    img = Image.alpha_composite(img, arcs)
    return img


def emblem(r: int) -> Image.Image:
    """The ticket emblem: a navy disc ringed in gold with a gold ticket, as on the sign-in page."""
    size = r * 2 + 8 * S
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size / 2
    fill = Image.new("RGBA", (size, size))
    fp = fill.load()
    for y in range(size):
        for x in range(size):
            dist = math.hypot(x - c, y - c * 0.8) / r
            fp[x, y] = (*lerp((22, 48, 127), (6, 15, 54), min(dist, 1)), 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
    img.paste(fill, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.ellipse((c - r, c - r, c + r, c + r), outline=(*GOLD, 255), width=int(2.4 * S))
    d.ellipse((c - r * 0.9, c - r * 0.9, c + r * 0.9, c + r * 0.9), outline=(242, 193, 78, 60), width=S)
    # The ticket: a rectangle with round notches on both ends, tilted.
    tw, th = r * 1.15, r * 0.62
    pts = []
    for i in range(40):  # left end with notch
        a = math.pi / 2 + math.pi * i / 39
        pts.append((-tw / 2 + 0.0 - math.cos(a) * th * 0.18 * -1, -math.sin(a) * th * 0.18 * -1))
    shape = [(-tw / 2, -th / 2), (tw / 2, -th / 2)]
    for i in range(21):
        a = -math.pi / 2 + math.pi * i / 20
        shape.append((tw / 2 - math.cos(a) * th * 0.2, math.sin(a) * th * 0.2))
    shape += [(tw / 2, th / 2), (-tw / 2, th / 2)]
    for i in range(21):
        a = math.pi / 2 - math.pi * i / 20
        shape.append((-tw / 2 + math.cos(a) * th * 0.2, math.sin(a) * th * 0.2))
    ang = math.radians(-35)
    rot = [(c + x * math.cos(ang) - y * math.sin(ang), c + x * math.sin(ang) + y * math.cos(ang)) for x, y in shape]
    tmask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(tmask).polygon(rot, fill=255)
    grad = Image.new("RGBA", (size, size))
    gp = grad.load()
    for y in range(size):
        for x in range(size):
            t = ((x - c) + (y - c)) / (2 * r) + 0.5
            gp[x, y] = (*(lerp(GOLD_LIGHT, GOLD, t / 0.4) if t < 0.4 else lerp(GOLD, GOLD_DEEP, (t - 0.4) / 0.6)), 255)
    img.paste(grad, (0, 0), tmask)
    # Inner outline and the perforation on the ticket.
    inner = [(c + (x * 0.62) * math.cos(ang) - (y * 0.62) * math.sin(ang), c + (x * 0.62) * math.sin(ang) + (y * 0.62) * math.cos(ang)) for x, y in [(-tw / 2, -th / 2), (tw / 2, -th / 2), (tw / 2, th / 2), (-tw / 2, th / 2), (-tw / 2, -th / 2)]]
    d.line(inner, fill=(138, 93, 12, 190), width=int(1.6 * S))
    return img


def coin_face(d: int) -> Image.Image:
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    px = img.load()
    c = d / 2
    for y in range(d):
        for x in range(d):
            dist = math.hypot(x - c, y - c) / c
            if dist > 1:
                continue
            # Metallic banding with a highlight towards the upper left.
            band = 0.5 + 0.5 * math.sin((x + y) / d * 5.2)
            light = max(0.0, 1 - math.hypot(x - c * 0.62, y - c * 0.55) / (c * 1.1))
            base = lerp((211, 147, 24), (255, 220, 110), band * 0.75)
            col = lerp(base, (255, 246, 214), light * 0.65)
            if dist > 0.86:  # rim
                col = lerp((154, 107, 18), (240, 196, 80), 0.5 + 0.5 * math.sin(math.atan2(y - c, x - c) * 2))
            px[x, y] = (*col, 255)
    dr = ImageDraw.Draw(img)
    dr.ellipse((d * 0.17, d * 0.17, d * 0.83, d * 0.83), outline=(255, 242, 191, 170), width=max(1, d // 40))
    font = ImageFont.truetype(RUPEE_FONT, int(d * 0.5))
    dr.text((c + d * 0.012, c + d * 0.012), "₹", font=font, anchor="mm", fill=(255, 244, 200, 170))
    dr.text((c, c), "₹", font=font, anchor="mm", fill=(140, 92, 10, 255))
    return img


def star(r: int) -> Image.Image:
    size = r * 4
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size / 2
    glow = Image.new("L", (size, size), 0)
    ImageDraw.Draw(glow).ellipse((c - r * 0.75, c - r * 0.75, c + r * 0.75, c + r * 0.75), fill=140)
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.45))
    img.paste((255, 214, 120, 255), (0, 0), glow)
    rays = Image.new("L", (size, size), 0)
    k = 0.07
    ImageDraw.Draw(rays).polygon(
        [(c, c - r), (c + r * k, c - r * k), (c + r, c), (c + r * k, c + r * k), (c, c + r), (c - r * k, c + r * k), (c - r, c), (c - r * k, c - r * k)],
        fill=255,
    )
    rays = rays.filter(ImageFilter.GaussianBlur(max(1, r * 0.02)))
    core = Image.new("RGBA", (size, size), (255, 250, 228, 255))
    img = Image.composite(core, img, rays)
    dot = Image.new("L", (size, size), 0)
    ImageDraw.Draw(dot).ellipse((c - r * 0.12, c - r * 0.12, c + r * 0.12, c + r * 0.12), fill=255)
    img = Image.composite(Image.new("RGBA", (size, size), (255, 255, 255, 255)), img, dot.filter(ImageFilter.GaussianBlur(r * 0.05)))
    return img


def with_alpha(img: Image.Image, a: float) -> Image.Image:
    r, g, b, al = img.split()
    return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))


bg = background()
EMB_R = int(46 * S)
emb = emblem(EMB_R)
ecx, ecy = W // 2, int(H * 0.47)
halo_mask = Image.new("L", (W, H), 0)
ImageDraw.Draw(halo_mask).ellipse((ecx - EMB_R * 1.6, ecy - EMB_R * 1.6, ecx + EMB_R * 1.6, ecy + EMB_R * 1.6), fill=255)
halo_mask = halo_mask.filter(ImageFilter.GaussianBlur(EMB_R * 0.45))

# Everything that does not move is drawn once: dust, the halo and the emblem. Only the
# coins and stars change between frames, which keeps the GIF small.
STATIC = Image.new("RGBA", (W, H), (0, 0, 0, 0))
_dd = ImageDraw.Draw(STATIC)
STATIC = Image.composite(Image.new("RGBA", (W, H), (242, 193, 78, 255)), STATIC, halo_mask.point(lambda v: int(v * 0.36)))
COIN_SPRITE = coin_face(60 * S)
STAR_SPRITE = star(26 * S)

coins = []
for i in range(9):
    depth = rng.random()
    coins.append(
        {
            "x": (i + 0.2 + rng.random() * 0.6) / 9 * W,
            "size": int((16 + depth * 20) * S),
            "phase": rng.random(),
            "spin": rng.choice([1, 1, 2]),
            "spin_phase": rng.random(),
            "sway": (4 + rng.random() * 8) * S,
            "alpha": 0.75 + depth * 0.25,
        }
    )
# Keep coins clear of the emblem in the middle.
for c in coins:
    if abs(c["x"] - W / 2) < EMB_R * 1.4:
        c["x"] += EMB_R * 1.6 * (1 if c["x"] >= W / 2 else -1)

stars = []
for i in range(11):
    stars.append(
        {
            "x": rng.uniform(0.05, 0.95) * W,
            "y": rng.uniform(0.08, 0.92) * H,
            "r": int(rng.uniform(10, 22) * S),
            "phase": rng.random(),
            "blinks": rng.choice([1, 1, 2]),
        }
    )
stars = [s for s in stars if math.hypot(s["x"] - ecx, s["y"] - ecy) > EMB_R * 1.5]

dust = [(rng.uniform(0, W), rng.uniform(0, H), rng.choice([1, 1, 2]) * S, rng.uniform(0.3, 0.85)) for _ in range(46)]
_dl = ImageDraw.Draw(STATIC)
for x, y, r, a in dust:
    _dl.ellipse((x - r, y - r, x + r, y + r), fill=(255, 222, 140, int(255 * a)))
STATIC.alpha_composite(emb, (ecx - emb.width // 2, ecy - emb.height // 2))

frames = []
for f in range(FRAMES):
    t = f / FRAMES
    frame = bg.copy()
    frame = Image.alpha_composite(frame, STATIC)
    # Stars flare and fade.
    for s in stars:
        u = (t * s["blinks"] + s["phase"]) % 1
        k = math.sin(math.pi * u / 0.45) if u < 0.45 else 0.0
        if k <= 0.02:
            continue
        size = max(4, int(STAR_SPRITE.width * (0.25 + 0.75 * k) * s["r"] / (26 * S)))
        spr = with_alpha(STAR_SPRITE.resize((size, size), Image.LANCZOS).rotate(30 * u, resample=Image.BICUBIC), k)
        frame.alpha_composite(spr, (int(s["x"] - size / 2), int(s["y"] - size / 2)))
    # Coins fall the whole height once per loop and turn as they fall.
    for c in coins:
        y = ((t + c["phase"]) % 1) * (H + c["size"] * 2) - c["size"]
        x = c["x"] + c["sway"] * math.sin(2 * math.pi * (t + c["phase"]))
        turn = math.cos(2 * math.pi * (t * c["spin"] + c["spin_phase"]))
        wface = max(2, int(c["size"] * abs(turn)))
        thick = max(2, int(c["size"] * 0.13))
        edge = Image.new("RGBA", (wface + thick + 2, c["size"]), (0, 0, 0, 0))
        ImageDraw.Draw(edge).ellipse((0, 0, wface + thick, c["size"] - 1), fill=(150, 98, 14, 255))
        face = COIN_SPRITE.resize((wface, c["size"]), Image.LANCZOS)
        if turn < 0:
            face = face.transpose(Image.FLIP_LEFT_RIGHT)
        layer = Image.new("RGBA", edge.size, (0, 0, 0, 0))
        layer.alpha_composite(edge)
        layer.alpha_composite(face, (0 if turn > 0 else thick, 0))
        layer = with_alpha(layer, c["alpha"])
        frame.alpha_composite(layer, (int(x - layer.width / 2), int(y)))
    frames.append(frame.convert("RGB").resize((OW, OH), Image.LANCZOS))

# One shared palette for every frame keeps colours steady between frames.
sample = Image.new("RGB", (OW, OH * 3))
for i, fi in enumerate((0, FRAMES // 3, 2 * FRAMES // 3)):
    sample.paste(frames[fi], (0, OH * i))
palette = sample.quantize(colors=192, method=Image.Quantize.MEDIANCUT)
# No dithering: the unchanged background then maps to the same pixels in every frame, so each
# frame only stores what moved.
out = [fr.quantize(palette=palette, dither=Image.Dither.NONE) for fr in frames]
out[0].save(OUT, save_all=True, append_images=out[1:], duration=MS, loop=0, optimize=True, disposal=1)
frames[0].save(OUT.replace(".gif", "-first.png"))
frames[FRAMES // 2].save(OUT.replace(".gif", "-mid.png"))
print(OUT, "frames", FRAMES)
