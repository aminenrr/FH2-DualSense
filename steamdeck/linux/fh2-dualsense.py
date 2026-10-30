#!/usr/bin/env python3
"""FH2 DualSense helper for the Steam Deck (Linux side of FH2DualSense.dll).

Started by fh2-dualsense-launch.sh (the Steam launch wrapper) next to the game. Reads /dev/shm/fh2-dualsense.shm
(written by the mod inside Azimuth) and:
  * adaptive triggers: writes DualSense output reports to /dev/hidrawN
    (USB or Bluetooth), trigger bytes only, so Steam Input keeps the rumble,
    lightbar and everything else;
  * HD haptics: when the controller is on a USB cable, plays the mod's
    4-channel stream (actuators on channels 3+4) to the controller's audio
    sink with pw-cat, and keeps the default sink where it was (the TV/bar).
Exits by itself when the game is gone; triggers are reset on the way out.
Standard library only. Trigger logic ported from aminenrr/FH2-DualSense (MIT).
"""
import glob, json, mmap, os, signal, struct, subprocess, sys, threading, time, zlib, fcntl

SHM = os.environ.get("FH2DS_SHM", "/dev/shm/fh2-dualsense.shm")
MAGIC = 0x44324846
OUT_FRAMES = 16384
HDR = 256
SHM_SIZE = HDR + OUT_FRAMES * 8
SONY, DS, DS_EDGE = 0x054C, 0x0CE6, 0x0DF2
BT_FORMAT = os.environ.get("FH2DS_BT_FORMAT", "kernel")   # "kernel" (hid-playstation layout) or "pydualsense"

SETTINGS = {  # same defaults as fh2_triggers.py
    "brake_start_strength": 2, "brake_end_strength": 8, "brake_start_zone": 1,
    "game_rumble_gain": 1.0, "brake_rumble_freq": 35, "gas_rumble_freq": 50,
    "gas_rumble_gain": 1.6, "gas_rumble_min": 3, "rumble_deadzone": 0.04,
    "gas_strength": 2, "gas_start_zone": 1,
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ triggers
def feedback(start_zone, s_from, s_to):
    if s_to <= 0:
        return off()
    active = force = 0
    n = max(1, 9 - start_zone)
    for z in range(start_zone, 10):
        s = min(8, max(1, round(s_from + (s_to - s_from) * (z - start_zone) / n)))
        active |= 1 << z
        force |= (s - 1) << (3 * z)
    return [0x21, active & 0xFF, active >> 8 & 0xFF, force & 0xFF, force >> 8 & 0xFF,
            force >> 16 & 0xFF, force >> 24 & 0xFF, 0, 0, 0, 0]


def vibrate(start_zone, strength, freq):
    active = amp = 0
    for z in range(start_zone, 10):
        active |= 1 << z
        amp |= (min(8, max(1, strength)) - 1) << (3 * z)
    return [0x26, active & 0xFF, active >> 8 & 0xFF, amp & 0xFF, amp >> 8 & 0xFF,
            amp >> 16 & 0xFF, amp >> 24 & 0xFF, 0, 0, freq & 0xFF, 0]


def off():
    return [0x05] + [0] * 10


def scale(v, k):
    return min(8, max(1, round(v * k))) if v > 0 else 0


def find_pad():
    """-> (/dev/hidrawN, bus) for the first DualSense / DualSense Edge."""
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(os.path.join(d, "device", "uevent")).read()
        except OSError:
            continue
        for line in ue.splitlines():
            if line.startswith("HID_ID="):
                bus, vid, pid = (int(x, 16) for x in line[7:].split(":"))
                if vid == SONY and pid in (DS, DS_EDGE):
                    return "/dev/" + os.path.basename(d), bus
    return None, None


class Pad:
    def __init__(self, path, bus):
        self.path, self.bt = path, bus == 0x05
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.seq = 0
        if self.bt:  # feature report 0x05 switches a BT pad to full reports (harmless if Steam already did)
            try:
                buf = bytearray(41); buf[0] = 0x05
                fcntl.ioctl(self.fd, 0xC0000000 | (41 << 16) | (ord("H") << 8) | 0x07, buf)
            except OSError:
                pass
        log(f"DualSense on {path} ({'Bluetooth' if self.bt else 'USB'})")

    def triggers(self, right, left):
        p = [0] * 47
        p[0] = 0x0C                       # valid_flag0: right + left trigger effects only
        p[10:21] = right
        p[21:32] = left
        if self.bt:
            if BT_FORMAT == "pydualsense":
                r = [0x31, (self.seq << 4) & 0xF0 | 0x02] + p + [0] * 25
            else:                         # struct dualsense_output_report_bt (hid-playstation)
                r = [0x31, (self.seq << 4) & 0xF0, 0x10] + p + [0] * 24
            self.seq = (self.seq + 1) & 0x0F
            crc = zlib.crc32(bytes([0xA2] + r)) & 0xFFFFFFFF
            r += list(crc.to_bytes(4, "little"))
        else:
            r = [0x02] + p + [0] * 15     # 63 bytes
        os.write(self.fd, bytes(r))

    def close(self):
        try:
            self.triggers(off(), off())
        except OSError:
            pass
        os.close(self.fd)


# ------------------------------------------------------------------ PipeWire
def pw_objects():
    try:
        return json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout or "[]")
    except Exception:
        return []


def is_ds(props):
    t = " ".join(str(props.get(k, "")) for k in ("node.name", "node.description", "device.product.name",
                                                   "api.alsa.card.name", "device.description")).lower()
    return "dualsense" in t or "wireless controller" in t


def pw_state():
    """-> (4ch dualsense sink or None, its device id, surround-40 profile index or None,
           default sink name, {name: id}, {every dualsense sink name})"""
    objs = pw_objects()
    sinks, ds_sink, ds_dev, prof, default = {}, None, None, None, None
    ds_any = set()
    for o in objs:
        info = o.get("info") or {}
        props = info.get("props") or {}
        if o.get("type") == "PipeWire:Interface:Node" and props.get("media.class") == "Audio/Sink":
            sinks[props.get("node.name")] = o["id"]
            # SteamOS (UCM HiFi) splits the pad into a mono "Speaker" sink and a
            # 4ch "SpeakerHaptic" sink; only a 4-channel one carries the actuators
            if is_ds(props):
                ds_any.add(props.get("node.name"))
                try:
                    ch = int(props.get("audio.channels") or 0)
                except (TypeError, ValueError):
                    ch = 0
                if ch >= 4 and not str(props.get("node.name", "")).endswith(".split"):
                    ds_sink = props.get("node.name")
        if o.get("type") == "PipeWire:Interface:Device" and is_ds(props) and props.get("media.class") == "Audio/Device":
            ds_dev = o["id"]
            for p in (info.get("params") or {}).get("EnumProfile", []):
                if "surround-40" in str(p.get("name", "")):
                    prof = p.get("index")
        if o.get("type") == "PipeWire:Interface:Metadata" and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") == "default.audio.sink":
                    v = m.get("value")
                    default = v.get("name") if isinstance(v, dict) else None
    return ds_sink, ds_dev, prof, default, sinks, ds_any


class Haptics:
    def __init__(self, shm):
        self.m, self.proc, self.sink, self.home = shm, None, None, None
        self.rd = None
        self.profiled, self.fails, self.retry_at, self.started = False, 0, 0.0, 0.0

    def poll(self, want):
        ds_sink, ds_dev, prof, default, sinks, ds_any = pw_state()
        if default and default not in ds_any:
            self.home = default                       # the TV / bar / speakers / AirPods
        if default in ds_any and self.home in sinks:
            subprocess.run(["wpctl", "set-default", str(sinks[self.home])])
            log(f"default sink went to the DualSense; put back {self.home}")
        if want and not ds_sink and ds_any and ds_dev is not None and prof is not None and not self.profiled:
            # no 4ch sink yet (stereo profile): switch once, pick it up on the next poll
            subprocess.run(["wpctl", "set-profile", str(ds_dev), str(prof)])
            self.profiled = True
        if want and ds_sink and not self.proc and time.time() >= self.retry_at:
            self.start(ds_sink)
        elif self.proc and (not want or not ds_sink or self.proc.poll() is not None):
            self.stop()

    def start(self, sink):
        cmd = ["pw-cat", "--playback", "--target", sink, "--rate", "48000", "--channels", "4",
               "--channel-map", "FL,FR,RL,RR", "--format", "s16", "--latency", "30ms",
               "--media-role", "Game", "--raw", "-"]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        except OSError as e:
            log("pw-cat failed:", e); return
        self.sink, self.rd, self.started = sink, None, time.time()
        struct.pack_into("<I", self.m, 64, 1)
        threading.Thread(target=self.pump, daemon=True).start()
        log(f"HD haptics on -> {sink}")

    def stop(self):
        struct.pack_into("<I", self.m, 64, 0)
        died = self.proc is not None and self.proc.poll() is not None
        if self.proc:
            try:
                self.proc.stdin.close(); self.proc.terminate(); self.proc.wait(2)
            except Exception:
                pass
        if died and time.time() - self.started < 5:
            self.fails += 1                           # pw-cat died right away: back off
            self.retry_at = time.time() + min(60, 5 * 2 ** self.fails)
        else:
            self.fails = 0
        self.proc = None
        log("HD haptics off")

    def pump(self):
        p = self.proc
        while p is self.proc and p.poll() is None:
            w = struct.unpack_from("<I", self.m, 44)[0]
            if self.rd is None or (w - self.rd) & 0xFFFFFFFF > 4800:
                self.rd = (w - 1440) & 0xFFFFFFFF      # 30 ms behind the mixer
            n = (w - self.rd) & 0xFFFFFFFF
            if n:
                i = self.rd % OUT_FRAMES
                k = min(n, OUT_FRAMES - i)
                data = self.m[HDR + i * 8:HDR + (i + k) * 8]
                try:
                    p.stdin.write(data); p.stdin.flush()
                except (BrokenPipeError, OSError):
                    return
                self.rd = (self.rd + k) & 0xFFFFFFFF
            else:
                time.sleep(0.004)


# ------------------------------------------------------------------ main
def game_running():
    for c in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            if b"azimuth.exe" in open(c, "rb").read():
                return True
        except OSError:
            pass
    return False


def open_shm(timeout):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if os.path.getsize(SHM) >= SHM_SIZE:
                f = open(SHM, "r+b")
                m = mmap.mmap(f.fileno(), SHM_SIZE)
                if struct.unpack_from("<I", m, 0)[0] == MAGIC:
                    return m
                m.close(); f.close()
        except OSError:
            pass
        time.sleep(1)
    return None


def main():
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    log("waiting for the game")
    m = open_shm(600)
    if not m:
        log("the mod never started (no shared memory after 10 min); bye"); return
    log("mod found")
    S = SETTINGS
    pad, hap = None, Haptics(m)
    last, last_sent, next_pad, next_pw = None, 0, 0, 0
    last_hb, hb_t, last_n, last_n_t, gone_since = None, time.time(), None, 0, None
    alive, t_start = False, time.time()
    try:
        while not stop:
            now = time.time()
            hb, n = struct.unpack_from("<II", m, 8)
            lm, rm, lt, rt = struct.unpack_from("<4f", m, 16)
            set_trig, set_hap = struct.unpack_from("<II", m, 48)
            k_trig = struct.unpack_from("<f", m, 56)[0] or 1.0
            struct.pack_into("<I", m, 68, int(now * 10) & 0xFFFFFFFF)
            if hb != last_hb:
                alive = alive or last_hb is not None
                last_hb, hb_t = hb, now
            if not alive:                                            # mod not ticking yet: game still starting
                if now - t_start > 180 and not game_running():
                    log("game never started; bye"); break
            elif now - hb_t > 5:                                     # mod stopped: game closed?
                gone_since = gone_since or now
                if not game_running() or now - gone_since > 30:
                    log("game closed"); break
            else:
                gone_since = None

            if pad is None and now >= next_pad:
                path, bus = find_pad()
                next_pad = now + 3
                if path:
                    try:
                        pad = Pad(path, bus); last = None
                    except OSError as e:
                        log(f"can't open {path}: {e}")
            if now >= next_pw:
                hap.poll(bool(set_hap)); next_pw = now + 2

            if pad:
                if n != last_n:
                    last_n, last_n_t = n, now
                elif now - last_n_t > 1.5:                             # paused / menus
                    lt = rt = 0.0
                g, dz = S["game_rumble_gain"], S["rumble_deadzone"]
                lt = min(1.0, max(0.0, lt * g)); rt = min(1.0, max(0.0, rt * g))
                if not set_trig:
                    right = left = off()
                else:
                    if lt > dz:
                        left = vibrate(2, scale(1 + round(lt * 7), k_trig), S["brake_rumble_freq"])
                    else:
                        left = feedback(S["brake_start_zone"], scale(S["brake_start_strength"], k_trig),
                                        scale(S["brake_end_strength"], k_trig))
                    if rt > dz:
                        rs = min(8, max(S["gas_rumble_min"], round(min(1.0, rt * S["gas_rumble_gain"]) * 8)))
                        right = vibrate(2, scale(rs, k_trig), S["gas_rumble_freq"])
                    else:
                        right = feedback(S["gas_start_zone"], scale(S["gas_strength"], k_trig),
                                         scale(S["gas_strength"], k_trig))
                state = (tuple(right), tuple(left))
                if state != last or now - last_sent > 0.5:            # refresh: Steam Input writes too
                    try:
                        pad.triggers(right, left)
                        last, last_sent = state, now
                    except OSError as e:
                        log(f"controller gone ({e})")
                        try: os.close(pad.fd)
                        except OSError: pass
                        pad = None
            time.sleep(1 / 120)
    finally:
        hap.stop()
        if pad:
            pad.close()
        log("triggers reset, bye")


if __name__ == "__main__":
    main()
