"""
Reads Forza Horizon 2's own trigger-rumble ("impulse trigger") levels out of
Azimuth. The game sends 4 levels to the controller every frame (left motor,
right motor, left trigger, right trigger); Azimuth currently drops the two
trigger ones. We place a tiny hook at the start of Azimuth's SetVibration
handler that copies those 4 floats into a buffer we can read.
Nothing is written to disk; the hook disappears when the game closes.
"""
import ctypes, ctypes.wintypes as W, re, struct, time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_ALL = 0x1F0FFF
TH32CS_SNAPPROCESS, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32 = 0x2, 0x8, 0x10
MEM_COMMIT_RESERVE, PAGE_RWX = 0x3000, 0x40

# Azimuth 0.9.x: start of the gamepad SetVibration handler (?? = varies)
SIG = ("48 8B C4 48 89 58 08 57 48 81 EC B0 00 00 00 0F 29 70 E8 0F 29 78 D8 44 0F 29 40 C8 "
       "44 0F 29 48 B8 44 0F 29 50 A8 44 0F 29 58 98 44 0F 29 60 88 48 8B 05 ?? ?? ?? ?? 48 33 "
       "C4 48 89 44 24 38 48 8B F9 F3 44 0F 10 0A F3 44 0F 11 4C 24 28 F3 44 0F 10 52 04 F3 44 "
       "0F 11 54 24 2C F3 44 0F 10 5A 08 F3 44 0F 11 5C 24 30 F3 44 0F 10 62 0C")
STOLEN = 15   # mov rax,rsp / mov [rax+8],rbx / push rdi / sub rsp,0B0h


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD), ("th32ProcessID", W.DWORD),
                ("GlblcntUsage", W.DWORD), ("ProccntUsage", W.DWORD),
                ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", W.DWORD),
                ("hModule", W.HMODULE), ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.OpenProcess.restype = W.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
k32.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualProtectEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, ctypes.POINTER(W.DWORD)]
k32.FlushInstructionCache.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
k32.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]


def find_pid(name="azimuth.exe"):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    try:
        while ok:
            if e.szExeFile.lower() == name:
                return e.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return None


def main_module(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    m = MODULEENTRY32W(); m.dwSize = ctypes.sizeof(m)
    try:
        if not k32.Module32FirstW(snap, ctypes.byref(m)):
            return None, 0
        return m.modBaseAddr, m.modBaseSize
    finally:
        k32.CloseHandle(snap)


class AzimuthHook:
    def __init__(self, pid):
        self.h = k32.OpenProcess(PROCESS_ALL, False, pid)
        if not self.h:
            raise RuntimeError("Can't open azimuth.exe (try running this as administrator).")
        base, size = main_module(pid)
        if not base:
            raise RuntimeError("Can't read azimuth.exe modules.")
        img = self.read(base, size, partial=True)
        rx = re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)]))
                                 for t in SIG.split()), re.S)
        m = rx.search(img)
        if not m:
            # already hooked (e.g. start.bat is running)? then just share its buffer
            rest = SIG.split()[STOLEN:]
            rx2 = re.compile(re.escape(b"\xFF\x25\x00\x00\x00\x00") + b"(.{8})\x90" +
                             b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in rest), re.S)
            m2 = rx2.search(img)
            if not m2:
                raise RuntimeError("This Azimuth version isn't supported yet (hook pattern not found).")
            self.buf = struct.unpack("<Q", m2.group(1))[0] - 64
            self.target, self.hooked = base + m2.start(), False
            self.can_mute = self.read(self.buf + 64 + 0x1B, 4) == b"\x80\x78\x14\x00"
            return
        self.target = base + m.start()
        self.orig = img[m.start():m.start() + STOLEN]

        # memory: [buffer 64 bytes][stub]
        mem = k32.VirtualAllocEx(self.h, None, 0x1000, MEM_COMMIT_RESERVE, PAGE_RWX)
        if not mem:
            raise RuntimeError("VirtualAllocEx failed.")
        self.buf = mem
        stub = (b"\x4C\x8B\x12"                       # mov r10,[rdx]
                b"\x4C\x8B\x5A\x08"                   # mov r11,[rdx+8]
                b"\x48\xB8" + struct.pack("<Q", mem) + # mov rax,buf
                b"\x4C\x89\x10"                       # mov [rax],r10
                b"\x4C\x89\x58\x08"                   # mov [rax+8],r11
                b"\xFF\x40\x10"                       # inc dword [rax+10h]
                b"\x80\x78\x14\x00"                   # cmp byte [rax+14h],0  (mute flag)
                b"\x74\x07"                           # je +7
                b"\x48\xC7\x02\x00\x00\x00\x00"       # mov qword [rdx],0 (mute old rumble)
                + self.orig +                         # original prologue
                b"\xFF\x25\x00\x00\x00\x00" + struct.pack("<Q", self.target + STOLEN))
        self.write(mem + 64, stub)
        jmp = b"\xFF\x25\x00\x00\x00\x00" + struct.pack("<Q", mem + 64) + b"\x90"
        self.patch(jmp)
        self.hooked, self.can_mute = True, True

    def read(self, addr, n, partial=False):
        out = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
        if partial:  # read page by page so unreadable pages don't fail everything
            data = bytearray(n)
            for off in range(0, n, 0x1000):
                c = min(0x1000, n - off)
                b = ctypes.create_string_buffer(c)
                if k32.ReadProcessMemory(self.h, addr + off, b, c, ctypes.byref(got)):
                    data[off:off + c] = b.raw
            return bytes(data)
        if not k32.ReadProcessMemory(self.h, addr, out, n, ctypes.byref(got)):
            raise OSError("read failed")
        return out.raw

    def write(self, addr, data):
        got = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.h, addr, data, len(data), ctypes.byref(got)):
            raise OSError("write failed")

    def patch(self, data):
        old = W.DWORD()
        k32.VirtualProtectEx(self.h, self.target, len(data), PAGE_RWX, ctypes.byref(old))
        self.write(self.target, data)
        k32.VirtualProtectEx(self.h, self.target, len(data), old.value, ctypes.byref(old))
        k32.FlushInstructionCache(self.h, self.target, len(data))

    def alive(self):
        code = W.DWORD()
        return k32.GetExitCodeProcess(self.h, ctypes.byref(code)) and code.value == 259

    def levels(self):
        """-> (left_motor, right_motor, left_trigger, right_trigger, counter)"""
        return struct.unpack("<4fI", self.read(self.buf, 20))

    def mute_rumble(self, on):
        """Silence the game's normal rumble (haptics replace it)."""
        if self.can_mute and self.alive():
            try:
                self.write(self.buf + 0x14, b"\x01" if on else b"\x00")
                self.muted = on
            except OSError:
                pass

    def unhook(self):
        # The hook is left in place (other tools may share it); it is harmless
        # and disappears when the game closes. We just give rumble back.
        if getattr(self, "muted", False):
            self.mute_rumble(False)
        k32.CloseHandle(self.h)


def wait_for_game(poll=2.0):
    print("Waiting for Forza Horizon 2 (azimuth.exe)...")
    while True:
        pid = find_pid()
        if pid:
            time.sleep(8)          # let it finish starting up
            return AzimuthHook(pid)
        time.sleep(poll)


if __name__ == "__main__":   # monitor mode: python azimuth_hook.py
    hk = wait_for_game()
    print("Hooked. Showing the game's vibration levels (Ctrl+C to stop):")
    try:
        while hk.alive():
            lm, rm, lt, rt, n = hk.levels()
            print(f"\rmotors L {lm:4.2f} R {rm:4.2f} | triggers L2 {lt:4.2f} R2 {rt:4.2f} | calls {n:8d}", end="")
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        hk.unhook()
