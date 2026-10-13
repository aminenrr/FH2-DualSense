"""Build the installer's pixel data from the raw PNGs. Dev tool only.

Needs: pip install pillow numpy etcpak texture2ddecoder

  python tools/build_payloads.py placements <folder with the ORIGINAL game zips>
      Measures where the button art sits in each original icon (just a bounding box),
      and writes tools/placements.json. Only needed once; the json is committed.

  python tools/build_payloads.py build
      Fits raw/ps_prompts/* and raw/dualsense_diagram/* into each texture size,
      BC7-encodes them and writes build/<zip>.bin + build/manifest.json.
      No game files are needed or included.
"""
import os, sys, json, hashlib, zipfile
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fh2_swatch as S

RAW_P = os.path.join(ROOT, "raw", "ps_prompts")
RAW_D = os.path.join(ROOT, "raw", "dualsense_diagram")
PLACEMENTS = os.path.join(ROOT, "tools", "placements.json")
BUILD = os.path.join(ROOT, "build")

ZIPS = {
    "ControllerIcons.zip": "Mount/media/ui/textures/data_bound/ControllerIcons.zip",
    "Help_Buttons.zip": "Mount/media/ui/textures/data_bound/Help_Buttons.zip",
    "UI_Symbols.zip": "Mount/media/ui/textures/data_bound/UI_Symbols.zip",
}

CI = {"a": "cross", "b": "circle", "x": "square", "y": "triangle", "lb": "l1", "rb": "r1",
      "lt": "l2", "rt": "r2", "view": "touchpad", "menu": "options", "l3": "l3", "r3": "r3",
      "dpadup": "dpad_up", "dpaddown": "dpad_down", "dpadleft": "dpad_left",
      "dpadright": "dpad_right", "dpadleftright": "dpad_leftright", "dpadupdown": "dpad_updown"}
for side, s in (("left", "l"), ("right", "r")):
    CI[side + "thumb"] = s + "stick"
    for d in ("up", "down", "left", "right", "leftright", "updown"):
        CI[f"{side}thumb_{d}"] = f"{s}stick_{d}"

HB = {"button_a": "cross", "button_b": "circle", "button_x": "square", "button_y": "triangle",
      "button_lb": "l1", "button_rb": "r1", "button_lt": "l2", "button_rt": "r2",
      "button_back": "touchpad", "button_start": "options",
      "dpad_u": "dpad_up", "dpad_d": "dpad_down", "dpad_l": "dpad_left", "dpad_r": "dpad_right",
      "dpad_lr": "dpad_leftright", "dpad_ud": "dpad_updown"}

DIAGRAM = {"Help_Controller.swatchbin": "dualsense_settings.png",
           "Anthem_Controller_Initial_Experience.swatchbin": "dualsense_loading.png"}


def target_for(zipname, entry):
    base = os.path.splitext(os.path.basename(entry))[0].lower()
    if zipname == "UI_Symbols.zip":
        return ("diagram", DIAGRAM[entry]) if entry in DIAGRAM else None
    if zipname == "ControllerIcons.zip":
        if not base.startswith("controller_"):
            return None
        g = CI.get(base[len("controller_"):])
        return ("hud", g) if g else None
    style = "help"
    for suf, st in (("_dark", "help_dark"), ("_light", "help_light"), ("_lg", "help")):
        if base.endswith(suf):
            base, style = base[:-len(suf)], st
    g = HB.get(base)
    return (style, g) if g else None


# ---------------------------------------------------------------- decoding (dev only)
def decode_mip0(data):
    import texture2ddecoder as t
    info = S.parse(data); fmt, fn = S.LAYOUTS[S.layout_key(info)]
    w, h = info["w"], info["h"]; p = data[S.HDR:]
    if fmt == "rgba8":
        img = np.zeros((h, w, 4), np.uint8)
        for off, x, y in fn()[0]:
            if x < w and y < h:
                img[y, x] = np.frombuffer(p[off:off + 4], np.uint8)
        return img
    bw, bh = (w + 3) // 4, (h + 3) // 4
    img = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    for off, bx, by in fn()[0]:
        if bx < bw and by < bh:
            px = np.frombuffer(t.decode_bc7(p[off:off + 16], 4, 4), np.uint8).reshape(4, 4, 4)
            img[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4] = px[..., [2, 1, 0, 3]]
    return img[:h, :w]


def cmd_placements(orig_dir):
    out = {}
    for zipname in ZIPS:
        path = os.path.join(orig_dir, zipname)
        blob = open(path, "rb").read()
        z = zipfile.ZipFile(path)
        ent = {}
        for e in z.namelist():
            t = target_for(zipname, e)
            if not t:
                continue
            data = z.read(e); info = S.parse(data)
            rec = {"layout": S.layout_key(info), "style": t[0], "art": t[1]}
            if t[0] != "diagram":
                a = decode_mip0(data)[..., 3]
                ys, xs = np.nonzero(a > 20)
                rec["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            ent[e] = rec
        out[zipname] = {"path": ZIPS[zipname], "vanilla_sha256": hashlib.sha256(blob).hexdigest(),
                        "entries": ent}
        print(zipname, len(ent), "textures")
    json.dump(out, open(PLACEMENTS, "w"), indent=1)
    print("wrote", PLACEMENTS)


# ---------------------------------------------------------------- building
def premult(im):
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    a[..., :3] *= a[..., 3:4] / 255.0
    return a


def to_img(a):
    a = a.copy(); a[..., :3] = np.minimum(a[..., :3], a[..., 3:4])
    return Image.fromarray(np.clip(np.round(a), 0, 255).astype(np.uint8), "RGBA")


def resize_pm(a, size):
    """Resize a premultiplied float image (per channel, LANCZOS)."""
    chans = [np.asarray(Image.fromarray(a[..., c].astype(np.float32), "F").resize(size, Image.LANCZOS))
             for c in range(4)]
    return np.clip(np.dstack(chans), 0, 255)


def fitted(rec, w, h):
    art = premult(Image.open(os.path.join(RAW_P, rec["style"], rec["art"] + ".png")))
    ys, xs = np.nonzero(art[..., 3] > 20)
    art = art[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    x0, y0, x1, y1 = rec["bbox"]; bw, bh = x1 - x0, y1 - y0
    s = min(bw / art.shape[1], bh / art.shape[0])
    nw, nh = max(1, round(art.shape[1] * s)), max(1, round(art.shape[0] * s))
    r = resize_pm(art, (nw, nh))
    out = np.zeros((h, w, 4), np.float32)
    ox, oy = x0 + (bw - nw) // 2, y0 + (bh - nh) // 2
    out[oy:oy + nh, ox:ox + nw] = r
    return out


def encode_linear(rec, a, w, h, mips):
    import etcpak
    fmt = S.LAYOUTS[rec["layout"]][0]
    if fmt == "rgba8":
        return to_img(a).tobytes()
    out = b""
    nmaps = len(S.LAYOUTS[rec["layout"]][1]())
    for level in range(nmaps):
        lw, lh = max(1, w >> level), max(1, h >> level)
        la = a if level == 0 else resize_pm(a, (lw, lh))
        bw, bh = (lw + 3) // 4, (lh + 3) // 4
        canvas = Image.new("RGBA", (bw * 4, bh * 4), (0, 0, 0, 0))
        canvas.paste(to_img(la), (0, 0))
        out += etcpak.compress_bc7(canvas.tobytes(), bw * 4, bh * 4)
    return out


def cmd_build():
    pl = json.load(open(PLACEMENTS))
    os.makedirs(BUILD, exist_ok=True)
    manifest = {"version": 1, "zips": {}}
    for zipname, zrec in pl.items():
        blob = bytearray(); ents = {}
        for e, rec in zrec["entries"].items():
            w, h = [int(v) for v in rec["layout"].split("_")[0].split("x")]
            mips = int(rec["layout"].split("_")[1][1:])
            if rec["style"] == "diagram":
                a = premult(Image.open(os.path.join(RAW_D, rec["art"])))
            else:
                a = fitted(rec, w, h)
            data = encode_linear(rec, a, w, h, mips)
            ents[e] = {"layout": rec["layout"], "offset": len(blob), "size": len(data)}
            blob += data
        fn = zipname.replace(".zip", ".bin")
        open(os.path.join(BUILD, fn), "wb").write(blob)
        manifest["zips"][zipname] = {"path": zrec["path"], "vanilla_sha256": zrec["vanilla_sha256"],
                                     "data": fn, "data_sha256": hashlib.sha256(blob).hexdigest(),
                                     "entries": ents}
        print(zipname, len(ents), "textures,", len(blob), "bytes")
    json.dump(manifest, open(os.path.join(BUILD, "manifest.json"), "w"), indent=1)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "placements":
        cmd_placements(a[1])
    elif a == ["build"]:
        cmd_build()
    else:
        print(__doc__)
