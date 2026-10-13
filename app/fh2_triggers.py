"""
FH2 DualSense Triggers v2 - adaptive triggers for Forza Horizon 2 (Azimuth).
Original code. Uses the game's OWN trigger rumble (brake lock-up / ABS on L2,
wheelspin on R2), read live from Azimuth, on top of a resting brake/gas feel.
If the game isn't running or can't be hooked, falls back to the basic mode.
Only trigger bytes are written to the controller; rumble is left alone.

Run:  python fh2_triggers.py      (Ctrl+C to quit, triggers reset on exit)
Needs: pip install hidapi
"""
import sys, time, zlib

# ------------------------------------------------------------------ SETTINGS
SETTINGS = {
    # Brake (L2): resistance that builds the deeper you press (1 = light, 8 = max)
    "brake_start_strength": 2,
    "brake_end_strength":   8,
    "brake_start_zone":     1,      # 0-9, where along the pull resistance begins

    # Game trigger rumble (from FH2 itself)
    "game_rumble":          True,
    "game_rumble_gain":     1.0,    # >1 = stronger
    "brake_rumble_freq":    35,     # Hz of the L2 buzz
    "gas_rumble_freq":      50,     # Hz of the R2 buzz
    "gas_rumble_gain":      1.6,    # extra boost for the R2 wheelspin buzz
    "gas_rumble_min":       3,      # weakest R2 buzz strength (1-8)
    "rumble_deadzone":      0.04,   # ignore tiny levels

    # Basic-mode lock-up buzz (only used when the game can't be hooked)
    # brake buzzes when pressed past this point (0-255, 0 = off)
    "brake_buzz_threshold": 235,
    "brake_buzz_freq":      28,     # Hz
    "brake_buzz_strength":  5,      # 1-8

    # Gas (R2): light, even resistance
    "gas_strength":         2,      # 0 = off, 1-8
    "gas_start_zone":       1,

    # Rev buzz: gas buzzes after being held flat out this many seconds (0 = off)
    "gas_buzz_after_sec":   0,
    "gas_buzz_freq":        45,
    "gas_buzz_strength":    3,
}
# ---------------------------------------------------------------------------

SONY, DS, DS_EDGE = 0x054C, 0x0CE6, 0x0DF2

try:
    import hid
except ImportError:
    sys.exit("Missing library. Run:  pip install hidapi")


def feedback(start_zone, s_from, s_to):
    """Mode 0x21: per-zone resistance, ramping from s_from to s_to."""
    if s_to <= 0:
        return off()
    active = force = 0
    n = max(1, 9 - start_zone)
    for z in range(start_zone, 10):
        s = round(s_from + (s_to - s_from) * (z - start_zone) / n)
        s = min(8, max(1, s))
        active |= 1 << z
        force |= (s - 1) << (3 * z)
    return [0x21, active & 0xFF, active >> 8 & 0xFF,
            force & 0xFF, force >> 8 & 0xFF, force >> 16 & 0xFF, force >> 24 & 0xFF,
            0, 0, 0, 0]


def vibrate(start_zone, strength, freq):
    """Mode 0x26: trigger buzzes from start_zone to the end of the pull."""
    active = amp = 0
    for z in range(start_zone, 10):
        active |= 1 << z
        amp |= (min(8, max(1, strength)) - 1) << (3 * z)
    return [0x26, active & 0xFF, active >> 8 & 0xFF,
            amp & 0xFF, amp >> 8 & 0xFF, amp >> 16 & 0xFF, amp >> 24 & 0xFF,
            0, 0, freq & 0xFF, 0]


def off():
    return [0x05] + [0] * 10


class DualSense:
    def __init__(self):
        info = next((d for d in hid.enumerate(SONY)
                     if d["product_id"] in (DS, DS_EDGE)), None)
        if not info:
            raise RuntimeError("No DualSense found. Plug it in (USB or Bluetooth) "
                               "and check HidHide / DS4Windows isn't hiding it.")
        self.dev = hid.device()
        self.dev.open_path(info["path"])
        self.dev.set_nonblocking(True)
        # USB = interface 3 / bus 1; Bluetooth paths usually contain 'BTH' or bus 2
        path = info["path"].decode(errors="ignore").upper()
        self.bt = info.get("bus_type") == 2 or "BTH" in path or "{00001124" in path
        try:  # asks a BT pad to switch to full reports (harmless over USB)
            self.dev.get_feature_report(0x05, 41)
        except Exception:
            pass
        self.seq = 0
        self.l2 = self.r2 = 0

    def poll(self):
        while True:
            d = self.dev.read(78)
            if not d:
                return
            if d[0] == 0x01 and len(d) >= 10 and self.bt:   # BT simple report
                self.l2, self.r2 = d[8], d[9]
            elif d[0] == 0x01:                               # USB
                self.l2, self.r2 = d[5], d[6]
            elif d[0] == 0x31:                               # BT full report
                self.l2, self.r2 = d[6], d[7]

    def triggers(self, right, left):
        p = [0] * 47
        p[0] = 0x0C                    # flag: update right + left trigger only
        p[10:21] = right
        p[21:32] = left
        if self.bt:
            r = [0x31, (self.seq << 4) & 0xF0 | 0x02] + p + [0] * (74 - 2 - 47)
            self.seq = (self.seq + 1) & 0x0F
            crc = zlib.crc32(bytes([0xA2] + r)) & 0xFFFFFFFF
            r += list(crc.to_bytes(4, "little"))
        else:
            r = [0x02] + p
        self.dev.write(r)

    def close(self):
        try:
            self.triggers(off(), off())
        finally:
            self.dev.close()


def try_hook():
    try:
        import azimuth_hook as az
    except Exception:
        return None
    pid = az.find_pid()
    if not pid:
        return None
    time.sleep(5)
    try:
        hk = az.AzimuthHook(pid)
        print("Hooked into Forza Horizon 2: game trigger rumble ON.")
        return hk
    except Exception as e:
        print(f"Couldn't hook the game ({e}). Using basic mode.")
        return False


def main():
    S = SETTINGS
    pad = DualSense()
    print(f"DualSense connected ({'Bluetooth' if pad.bt else 'USB'}). "
          "Leave this window open. Ctrl+C to quit.")
    last, last_sent, flat_since = None, 0, None
    hook, next_try, last_n, last_n_t = None, 0, None, 0
    try:
        while True:
            pad.poll()
            now = time.time()

            # (re)attach to the game
            if S["game_rumble"] and hook is None and now >= next_try:
                hook = try_hook()
                next_try = now + (3 if hook is None else 1e12)
            lt = rt = 0.0
            if hook:
                if not hook.alive():
                    print("Game closed. Waiting for it again...")
                    hook.unhook(); hook, next_try = None, now + 3
                else:
                    try:
                        _, _, lt, rt, n = hook.levels()
                    except OSError:
                        lt = rt = 0.0; n = last_n
                    if n != last_n:
                        last_n, last_n_t = n, now
                    elif now - last_n_t > 1.5:     # game paused / menu
                        lt = rt = 0.0
            g, dz = S["game_rumble_gain"], S["rumble_deadzone"]
            lt = min(1.0, max(0.0, lt * g)); rt = min(1.0, max(0.0, rt * g))

            # ---- L2 (brake)
            if hook and lt > dz:
                left = vibrate(2, 1 + round(lt * 7), S["brake_rumble_freq"])
            elif not hook and S["brake_buzz_threshold"] and pad.l2 >= S["brake_buzz_threshold"]:
                left = vibrate(7, S["brake_buzz_strength"], S["brake_buzz_freq"])
            else:
                left = feedback(S["brake_start_zone"],
                                S["brake_start_strength"], S["brake_end_strength"])

            # ---- R2 (gas)
            flat_since = (flat_since or now) if pad.r2 >= 250 else None
            if hook and rt > dz:
                rs = min(8, max(S["gas_rumble_min"], round(min(1.0, rt * S["gas_rumble_gain"]) * 8)))
                right = vibrate(2, rs, S["gas_rumble_freq"])
            elif S["gas_buzz_after_sec"] and flat_since and now - flat_since >= S["gas_buzz_after_sec"]:
                right = vibrate(8, S["gas_buzz_strength"], S["gas_buzz_freq"])
            else:
                right = feedback(S["gas_start_zone"], S["gas_strength"], S["gas_strength"])

            state = (tuple(right), tuple(left))
            if state != last or now - last_sent > 1.0:
                pad.triggers(right, left)
                last, last_sent = state, now
            time.sleep(1 / 120)
    except KeyboardInterrupt:
        pass
    finally:
        if hook:
            hook.unhook()
        pad.close()
        print("Triggers reset. Bye.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(e)
        input("Press Enter to close.")
