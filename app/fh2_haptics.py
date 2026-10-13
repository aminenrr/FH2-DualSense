"""
FH2 DualSense Haptics v4.3 - game-sound haptics for Forza Horizon 2 (Azimuth).
Original code, personal use.

fh2_fmodtap.dll is loaded into the game and listens to the game's OWN car
sound buses inside FMOD (engine, tyre roll per surface, skids, gravel
kick-up, impacts, gear shifts). Radio, voices, music and ambience are never
heard. Those sounds are filtered and sent to the DualSense actuators
(USB, channels 3+4). The old rumble is switched off while this runs.
"""
import ctypes, ctypes.wintypes as W, math, mmap, os, struct, sys, time
import numpy as np
import sounddevice as sd
from scipy.signal import butter, lfilter, lfilter_zi
import azimuth_hook as az

SETTINGS = {
    "master": 1.0,            # overall output (0-1)
    "boost": 2.5,             # extra strength for all sound buses (raise if still weak)
    "auto_level": True,       # lifts quiet buses (game volume sliders, quiet cars)
    "auto_level_max": 12.0,   # most a bus can be lifted
    # per sound bus: gain, low cut Hz (0 = none), high cut Hz, left share, right share
    "engine":        (1.2,  25, 250, 1.0, 0.8),   # engine / exhaust / intake
    "road":          (2.5,  20, 300, 0.8, 1.0),   # tyres rolling (asphalt, gravel, grass...)
    "skid":          (1.6,  50, 400, 0.6, 1.0),   # tyre squeal / slides
    "kickup":        (2.0,  60, 450, 0.8, 1.0),   # stones and dirt hitting the car
    "impact":        (3.0,   0, 300, 1.0, 1.0),   # your crashes
    "world_impact":  (2.0,   0, 300, 1.0, 1.0),   # world hits
    "smash":         (1.5,   0, 300, 1.0, 1.0),   # fences, signs
    "shift":         (2.5,   0, 300, 1.0, 1.0),   # gear change clunk
    # the game's own rumble motors, mixed underneath (thumps, kerbs)
    "rumble_gain":   1.0,     # used while game sound is flowing (ABS, brake lock, kerbs punch)
    "rumble_duck":   0.6,     # how much the sound layer steps back while the rumble punches
    "rumble_gain_fallback": 1.0,  # used if the sound tap isn't working
    "heavy_freq": 55, "light_freq": 170,
    "replace_rumble": True,   # switch the controller's old buzz off
    "latency_ms": 30,         # buffer; raise if it crackles
}
SLOTS = ["engine", "road", "skid", "kickup", "impact", "world_impact", "smash", "shift"]

RATE, BLOCK = 48000, 240
NSLOT, RING = 8, 16384
HDR = 32 + NSLOT * 32            # header + names
WIDX = HDR                       # 8 x uint32
RINGOFF = WIDX + NSLOT * 4
SHM_SIZE = RINGOFF + NSLOT * RING * 4
MAGIC = 0x31544846
HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "fh2_fmodtap2.dll")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = W.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
k32.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.GetModuleHandleW.restype = W.HMODULE
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [W.HMODULE, ctypes.c_char_p]
k32.CreateRemoteThread.restype = W.HANDLE
k32.CreateRemoteThread.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, W.DWORD, ctypes.c_void_p]


def inject(pid):
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        raise OSError("can't open azimuth.exe (run haptics.bat as administrator)")
    try:
        path = (DLL + "\0").encode("utf-16-le")
        mem = k32.VirtualAllocEx(h, None, len(path), 0x3000, 0x04)
        k32.WriteProcessMemory(h, mem, path, len(path), None)
        ll = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        t = k32.CreateRemoteThread(h, None, 0, ll, mem, 0, None)
        if not t:
            raise OSError(f"inject failed ({ctypes.get_last_error()})")
        k32.WaitForSingleObject(t, 10000); k32.CloseHandle(t)
    finally:
        k32.CloseHandle(h)


class Tap:
    def __init__(self):
        self.m = mmap.mmap(-1, SHM_SIZE, tagname="FH2HapticsTap")
        self.rd = [None] * NSLOT

    def hdr(self):
        return struct.unpack_from("<8I", self.m, 0)   # magic,ver,rate,nslot,attached,heartbeat,state,pad

    def widx(self):
        return struct.unpack_from(f"<{NSLOT}I", self.m, WIDX)

    def take(self, slot, w, n, target):
        r = self.rd[slot]
        lag = (w - r) & 0xFFFFFFFF if r is not None else None
        if r is None or lag > RING // 2 or lag > target * 4:
            r = (w - target) & 0xFFFFFFFF          # (re)sync a little behind the writer
            lag = target
        m = min(n, lag)
        out = np.zeros(n, np.float32)
        if m:
            i0 = r % RING
            a = np.frombuffer(self.m, np.float32, RING, RINGOFF + slot * RING * 4)
            if i0 + m <= RING:
                out[:m] = a[i0:i0 + m]
            else:
                k = RING - i0; out[:k] = a[i0:]; out[k:m] = a[:m - k]
        self.rd[slot] = (r + m) & 0xFFFFFFFF
        return out


def find_device():
    apis = sd.query_hostapis(); best = None
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].lower()
        if d["max_output_channels"] >= 2 and ("dualsense" in name or "wireless controller" in name):
            api = apis[d["hostapi"]]["name"].lower()
            score = (d["max_output_channels"] >= 4) * 10 + ("wasapi" in api) * 5
            if best is None or score > best[0]:
                best = (score, i, d)
    return best


def main():
    S = SETTINGS
    dev = find_device()
    if not dev:
        input("No DualSense audio found. Plug the controller in with a USB cable, then press Enter to quit."); return
    _, idx, info = dev
    if info["max_output_channels"] < 4:
        print("The DualSense speaker is set to stereo, so haptics can't reach the actuators.\n"
              "Fix: Sound settings > More sound settings > Playback > right-click the DualSense\n"
              "(Wireless Controller) > Configure > Quadraphonic > Next > Finish. Then run this again.")
        input("Press Enter to quit."); return
    if not os.path.exists(DLL):
        input("fh2_fmodtap2.dll is missing next to this file. Press Enter to quit."); return

    tap = Tap()
    filt = []
    for name in SLOTS:
        g, lo, hi, _, _ = S[name]
        if lo:
            b, a = butter(2, [lo / (RATE / 2), hi / (RATE / 2)], "bandpass")
        else:
            b, a = butter(2, hi / (RATE / 2), "lowpass")
        filt.append([b, a, lfilter_zi(b, a) * 0])
    gl = np.array([S[n][0] * S[n][3] for n in SLOTS])
    gr = np.array([S[n][0] * S[n][4] for n in SLOTS])

    env = np.zeros(NSLOT)
    decay = 0.5 ** (BLOCK / RATE / 4.0)      # level memory ~4 s
    st = {"lm": 0.0, "rm": 0.0, "flow": False, "rate": 48000}
    ph = np.zeros(2); sm = np.zeros(2)
    target = int(RATE * S["latency_ms"] / 1000)

    def cb(out, frames, t, status):
        L = np.zeros(frames); R = np.zeros(frames)
        flow = False
        if tap.hdr()[0] == MAGIC:
            w = tap.widx(); rate = st["rate"]
            need = frames if rate == RATE else int(round(frames * rate / RATE))
            for s in range(NSLOT):
                x = tap.take(s, w[s], need, int(target * rate / RATE))
                if rate != RATE:
                    x = np.interp(np.linspace(0, need, frames, endpoint=False), np.arange(need), x)
                if not x.any():
                    continue
                flow = True
                b, a, zi = filt[s]
                y, filt[s][2] = lfilter(b, a, x, zi=zi)
                k = S["boost"]
                if S["auto_level"]:
                    env[s] = max(float(np.abs(y).max()), env[s] * decay)
                    if env[s] > 1e-4:
                        k *= min(max(0.6 / env[s], 1.0), S["auto_level_max"])
                L += k * gl[s] * y; R += k * gr[s] * y
        # game rumble motors underneath
        rg = S["rumble_gain"] if flow else S["rumble_gain_fallback"]
        tgt = np.array([st["lm"], st["rm"]]) * rg
        ramp = np.linspace(0, 1, frames, endpoint=False)[:, None]
        amp = sm + (tgt - sm) * ramp; sm[:] = tgt
        f = np.array([S["heavy_freq"], S["light_freq"]])
        tt = np.arange(frames)[:, None] / RATE
        wv = np.sin(2 * math.pi * (ph + f * tt)); ph[:] = (ph + f * frames / RATE) % 1.0
        rL = amp[:, 0] * wv[:, 0] + amp[:, 1] * wv[:, 1] * 0.4
        rR = amp[:, 1] * wv[:, 1] + amp[:, 0] * wv[:, 0] * 0.4
        # sound layer is saturated on its own, then steps back so rumble punches through
        duck = 1.0 - S["rumble_duck"] * np.clip(amp.max(axis=1), 0, 1)
        out[:] = 0
        out[:, 2] = np.clip(np.tanh(L) * duck + rL, -1, 1) * S["master"]
        out[:, 3] = np.clip(np.tanh(R) * duck + rR, -1, 1) * S["master"]

    try:
        stream = sd.OutputStream(device=idx, channels=4, samplerate=RATE, blocksize=BLOCK,
                                 dtype="float32", latency="low", callback=cb)
    except Exception as e:
        print(f"Couldn't open the DualSense audio ({e}).\nCheck it's set to Quadraphonic, 48000 Hz.")
        input("Press Enter to quit."); return

    hook = None; pid_done = None; shown = (None, None); last_hb = (0, time.time())
    with stream:
        print(f"Haptics output: {info['name']}")
        print("Waiting for Forza Horizon 2 (azimuth.exe)...")
        try:
            while True:
                pid = az.find_pid()
                if not pid:
                    if pid_done:
                        print("Game closed. Waiting for it again...")
                        if hook: hook.unhook()
                        hook = None; pid_done = None; st.update(lm=0, rm=0)
                    time.sleep(1); continue
                if pid != pid_done:
                    time.sleep(6)
                    try:
                        inject(pid); print("Sound tap loaded into the game.")
                    except OSError as e:
                        print(f"Sound tap failed: {e}")
                    try:
                        hook = az.AzimuthHook(pid)
                        if S["replace_rumble"] and hook.can_mute:
                            hook.mute_rumble(True)
                    except Exception as e:
                        print(f"Rumble hook failed ({e})."); hook = None
                    pid_done = pid
                h = tap.hdr()
                if h[0] == MAGIC:
                    st["rate"] = h[2] or 48000
                    names = [SLOTS[i] for i in range(NSLOT) if h[4] >> i & 1]
                    state = {1: "found FMOD", 2: "hooked, waiting for the game's audio", 3: "connected"}.get(h[6], "starting")
                    if (state, tuple(names)) != shown:
                        shown = (state, tuple(names))
                        print(f"Sound tap: {state}. Buses: {', '.join(names) if names else 'none yet'}")
                if hook:
                    try:
                        lm, rm, _, _, _ = hook.levels()
                        st.update(lm=min(1, max(0, lm)), rm=min(1, max(0, rm)))
                    except OSError:
                        pass
                time.sleep(0.01)
        except KeyboardInterrupt:
            pass
        finally:
            if hook: hook.unhook()
            print("Haptics off. Bye.")


if __name__ == "__main__":
    main()
