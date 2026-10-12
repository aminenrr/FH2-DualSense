"""Runs fh2_triggers.py or fh2_haptics.py for the FH2 DualSense menu.
Stops cleanly (triggers reset, rumble restored) when the menu says stop or closes."""
import sys, os, json, threading, _thread, builtins, importlib.util, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
name = sys.argv[1]                       # "triggers" or "haptics"
print(f"loading {name}...", flush=True)


STOP = os.path.join(HERE, f".stop_{name}")
PARENT = os.getppid()


def parent_alive():
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, PARENT)   # SYNCHRONIZE
        if not h:
            return False
        r = k.WaitForSingleObject(h, 0); k.CloseHandle(h)
        return r == 0x102                                # still running
    except Exception:
        return True


def watch():
    # no blocking reads on stdin: on Windows that can freeze numpy's DLL loading
    while True:
        time.sleep(0.25)
        if os.path.exists(STOP) or not parent_alive():
            break
    _thread.interrupt_main()


threading.Thread(target=watch, daemon=True).start()
builtins.input = lambda prompt="": print(prompt, flush=True)   # no console here

spec = importlib.util.spec_from_file_location("fh2_" + name, os.path.join(HERE, f"fh2_{name}.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# menu settings (haptics only; the trigger tuning stays exactly as shipped)
try:
    with open(os.path.join(HERE, "settings.json")) as f:
        cfg = json.load(f).get(name, {})
except (OSError, ValueError):
    cfg = {}
S = mod.SETTINGS
for k, v in cfg.items():
    if k in S:
        if isinstance(S[k], tuple):      # per-bus tuple: only the gain is changed
            S[k] = (float(v),) + S[k][1:]
        elif isinstance(S[k], bool):
            S[k] = bool(v)
        elif isinstance(S[k], (int, float)):
            S[k] = type(S[k])(v)

print(f"{name} ready", flush=True)
try:
    mod.main()
except KeyboardInterrupt:
    pass
except RuntimeError as e:
    print(e, flush=True)
time.sleep(0.2)
