"""Render the integration's brand icon (no dependencies).

Draws the icon from signed-distance shapes and writes antialiased RGBA PNGs:

    python3 tools/render_icon.py \
        custom_components/tellstick_local/brand/icon.png \
        custom_components/tellstick_local/brand/icon@2x.png

Home Assistant 2026.3+ picks up custom_components/<domain>/brand/. The
design is original (a 433 MHz pulse train above a gateway box) and must not
use Telldus' or Home Assistant's branding.
"""
import math, struct, sys, zlib

def hexc(h): h = h.lstrip("#"); return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))

def rrect(x0, y0, x1, y1, r):
    cx, cy, hx, hy = (x0+x1)/2, (y0+y1)/2, (x1-x0)/2 - r, (y1-y0)/2 - r
    def d(x, y):
        qx, qy = abs(x-cx) - hx, abs(y-cy) - hy
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return d

def circle(cx, cy, r): return lambda x, y: math.hypot(x-cx, y-cy) - r

def capsule(ax, ay, bx, by, w):
    def d(x, y):
        px, py, vx, vy = x-ax, y-ay, bx-ax, by-ay
        t = max(0, min(1, (px*vx + py*vy) / (vx*vx + vy*vy)))
        return math.hypot(px - vx*t, py - vy*t) - w/2
    return d

def arc(cx, cy, r, a0, a1, w):  # angles in degrees, 0 = right, 90 = up
    a0, a1 = math.radians(a0), math.radians(a1)
    ends = [(cx + r*math.cos(a), cy - r*math.sin(a)) for a in (a0, a1)]
    def d(x, y):
        a = math.atan2(-(y-cy), x-cx)
        if a0 <= a <= a1:
            return abs(math.hypot(x-cx, y-cy) - r) - w/2
        return min(math.hypot(x-ex, y-ey) for ex, ey in ends) - w/2
    return d

BLUE, DARK, LIGHT = hexc("1E88E5"), hexc("1565C0"), hexc("64B5F6")
def polyline(points, w):
    segs = [capsule(*a, *b, w) for a, b in zip(points, points[1:])]
    return lambda x, y: min(s(x, y) for s in segs)

# RF pulse train (on-off keying, as on 433 MHz) rising from the antenna.
W = 0.07                    # stroke width
HI, LO = W / 2, 0.36        # pulse top touches the top edge (trimmed)
PULSE = [(W/2, LO), (0.22, LO), (0.22, HI), (0.40, HI), (0.40, LO), (0.60, LO),
         (0.60, HI), (0.78, HI), (0.78, LO), (1 - W/2, LO)]
SHAPES = [  # painted in order, coordinates in a unit square
    (capsule(0.5, 0.66, 0.5, LO, W), DARK),
    (polyline(PULSE, W), LIGHT),
    (rrect(0.0, 0.62, 1.0, 1.0, 0.11), BLUE),
    (circle(0.17, 0.81, 0.055), hexc("66BB6A")),
    (rrect(0.50, 0.775, 0.84, 0.845, 0.035), hexc("BBDEFB")),
]

def render(size):
    px = 1 / size
    rows = []
    for j in range(size):
        row = bytearray([0])
        y = (j + 0.5) * px
        for i in range(size):
            x = (i + 0.5) * px
            r = g = b = a = 0.0
            for d, (cr, cg, cb) in SHAPES:
                cov = max(0.0, min(1.0, 0.5 - d(x, y) / px))
                if cov:
                    na = cov + a * (1 - cov)
                    r = (cr*cov + r*a*(1-cov)) / na; g = (cg*cov + g*a*(1-cov)) / na
                    b = (cb*cov + b*a*(1-cov)) / na; a = na
            row += bytes([round(r*255), round(g*255), round(b*255), round(a*255)])
        rows.append(bytes(row))
    raw = b"".join(rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

for size, path in ((256, sys.argv[1]), (512, sys.argv[2])):
    open(path, "wb").write(render(size))
    print("wrote", path)
