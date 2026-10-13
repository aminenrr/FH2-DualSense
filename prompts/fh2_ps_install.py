"""FH2 PlayStation prompts installer.

Puts PlayStation button prompts and a DualSense controller diagram (Settings > Controls and
the loading screen) into YOUR copy of Forza Horizon 2 (Azimuth, game build 1.0.0.64).

No game files ship with this mod. The installer opens your own game's UI zips, swaps in our
pixel data (build/*.bin, made from the PNGs in raw/), and writes the zips back. Your original
files are kept next to them as .bak, and "uninstall" puts them back.

Pure Python standard library: nothing to pip install.

  python fh2_ps_install.py install   ["D:\\Games\\Forza Horizon 2"]
  python fh2_ps_install.py uninstall ["D:\\Games\\Forza Horizon 2"]
  python fh2_ps_install.py status    ["D:\\Games\\Forza Horizon 2"]

The folder can be the game folder, the folder with azimuth.exe, or any folder above
"Mount". Without one it uses the folder saved in the FH2 DualSense menu, or asks.
"""
import os, sys, json, hashlib, zipfile, io, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fh2_swatch as S

BUILD = os.path.join(HERE, "build")
SETTINGS = os.path.join(os.path.dirname(HERE), "app", "settings.json")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def log(msg, out=None):
    (out or print)(msg)


# ---------------------------------------------------------------- finding the game
def find_root(start):
    """Return the folder that contains Mount/media/UI.zip (looks up, then a few levels down)."""
    start = os.path.abspath(os.path.expanduser(start.strip().strip('"')))
    if os.path.isfile(start):
        start = os.path.dirname(start)
    hit = lambda d: os.path.isfile(os.path.join(d, "Mount", "media", "UI.zip"))
    p = start
    for _ in range(6):
        if hit(p):
            return p
        p = os.path.dirname(p)
    if not os.path.isdir(start):
        return None
    for base, dirs, _ in os.walk(start):
        if hit(base):
            return base
        if base[len(start):].count(os.sep) >= 3:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() != "mount"]
    return None


# ---------------------------------------------------------------- plan
def targets():
    man = json.load(open(os.path.join(BUILD, "manifest.json"), encoding="utf-8"))
    xp = json.load(open(os.path.join(BUILD, "xaml_patch.json"), encoding="utf-8"))
    return man, xp


def source_bytes(path, vanilla, out):
    """Pick the untouched original: the current file if it is vanilla, else the .bak."""
    bak = path + ".bak"
    cur = open(path, "rb").read()
    if sha(cur) == vanilla:
        return cur, "original"
    if os.path.isfile(bak):
        b = open(bak, "rb").read()
        if sha(b) == vanilla:
            return b, "backup"
        log(f"  ! {os.path.basename(bak)} is not the version this mod was made for; trying it anyway", out)
        return b, "backup (unknown version)"
    log(f"  ! {os.path.basename(path)} is not the version this mod was made for; trying it anyway", out)
    return cur, "current (unknown version)"


def patch_textures(src, zrec, blob):
    z = zipfile.ZipFile(io.BytesIO(src))
    names = set(z.namelist())
    reps = {}
    for entry, e in zrec["entries"].items():
        if entry not in names:
            raise RuntimeError(f"{entry} is missing from the game zip")
        tex = z.read(entry)
        info = S.parse(tex)
        if S.layout_key(info) != e["layout"]:
            raise RuntimeError(f"{entry} has an unexpected size ({S.layout_key(info)})")
        reps[entry] = S.write_payload(tex, blob[e["offset"]:e["offset"] + e["size"]])
    return S.rebuild_zip(src, reps)


def patch_xaml(src, xp):
    z = zipfile.ZipFile(io.BytesIO(src))
    raw = z.read(xp["entry"])
    text = raw.decode("utf-8")
    lines = text.split("\n")
    for num, old_sha1, new in xp["replace"]:
        line = lines[num - 1]
        body = line.strip()
        if hashlib.sha1(body.encode()).hexdigest() != old_sha1:
            raise RuntimeError(f"UI.zip line {num} is not what this mod expects (different game version?)")
        indent = line[:len(line) - len(line.lstrip())]
        tail = line[len(line.rstrip()):]
        lines[num - 1] = indent + new + tail
    return S.rebuild_zip(src, {xp["entry"]: "\n".join(lines).encode("utf-8")})


# ---------------------------------------------------------------- commands
def install(root, out=None):
    man, xp = targets()
    plan = []
    for zipname, zrec in man["zips"].items():
        path = os.path.join(root, *zrec["path"].split("/"))
        log(f"- {zipname}", out)
        blob = open(os.path.join(BUILD, zrec["data"]), "rb").read()
        if sha(blob) != zrec["data_sha256"]:
            raise RuntimeError(f"{zrec['data']} is damaged; download the mod again")
        src, kind = source_bytes(path, zrec["vanilla_sha256"], out)
        plan.append((path, src, patch_textures(src, zrec, blob), zrec["vanilla_sha256"]))
        log(f"  built from {kind}, {len(zrec['entries'])} textures", out)
    path = os.path.join(root, *xp["path"].split("/"))
    log("- UI.zip (Settings > Controls pointer lines)", out)
    src, kind = source_bytes(path, xp["vanilla_sha256"], out)
    plan.append((path, src, patch_xaml(src, xp), xp["vanilla_sha256"]))
    log(f"  built from {kind}", out)
    # everything built fine: now write
    for path, src, new, vanilla in plan:
        bak = path + ".bak"
        if not os.path.isfile(bak):
            open(bak, "wb").write(src)
        tmp = path + ".tmp"
        open(tmp, "wb").write(new)
        zipfile.ZipFile(tmp).testzip()
        os.replace(tmp, path)
    log("Done. PlayStation prompts and the DualSense diagram are installed.", out)
    log("Originals are saved next to each file as .bak (run uninstall to put them back).", out)
    return True


def uninstall(root, out=None):
    man, xp = targets()
    paths = [os.path.join(root, *z["path"].split("/")) for z in man["zips"].values()]
    paths.append(os.path.join(root, *xp["path"].split("/")))
    n = 0
    for p in paths:
        bak = p + ".bak"
        if os.path.isfile(bak):
            shutil.copyfile(bak, p)
            os.remove(bak)
            n += 1
            log(f"- restored {os.path.basename(p)}", out)
        else:
            log(f"- {os.path.basename(p)}: no backup, left as is", out)
    log("Done. The original Xbox prompts are back." if n else "Nothing to restore.", out)
    return True


def status(root, out=None):
    man, xp = targets()
    items = [(z["path"], z["vanilla_sha256"]) for z in man["zips"].values()]
    items.append((xp["path"], xp["vanilla_sha256"]))
    installed = 0
    for rel, van in items:
        p = os.path.join(root, *rel.split("/"))
        cur = sha(open(p, "rb").read())
        state = "original" if cur == van else ("PlayStation (backup kept)" if os.path.isfile(p + ".bak") else "modified by something else")
        installed += state.startswith("PlayStation")
        log(f"- {os.path.basename(p)}: {state}", out)
    return installed == len(items)


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ("install", "uninstall", "status"):
        print(__doc__); return 1
    folder = " ".join(a[1:]) if len(a) > 1 else ""
    root = find_root(folder) if folder else None
    if folder and not root:
        print("  Couldn't find Mount\\media\\UI.zip under", folder)
    while not root:
        try:
            folder = input("Paste your game folder (Anthem_1.0.0.64_x64__8wekyb3d8bbwe, or the Forza Horizon 2 folder above it): ")
        except EOFError:
            return 1
        if not folder.strip():
            return 1
        root = find_root(folder)
        if not root:
            print("  Couldn't find Mount\\media\\UI.zip under that folder.")
    print("Game:", root)
    try:
        {"install": install, "uninstall": uninstall, "status": status}[a[0]](root)
    except Exception as e:
        print("FAILED:", e, "\nNothing was changed.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
