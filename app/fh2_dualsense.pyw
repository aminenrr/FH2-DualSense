"""FH2 DualSense - mod menu for Forza Horizon 2 (Azimuth).
Adaptive triggers + game-sound haptics in one window. Original code. MIT licensed."""
import os, sys, json, queue, subprocess, threading, time
import tkinter as tk
from tkinter import filedialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "settings.json")
PY = os.path.join(os.path.dirname(sys.executable), "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
NOWIN = 0x08000000  # CREATE_NO_WINDOW

try:
    sys.path.insert(0, HERE)
    import azimuth_hook as az
except Exception:
    az = None

DEFAULTS = {
    "azimuth_path": "",
    "launch_game": True,
    "launch_mode": "azimuth",      # azimuth | dlc | none
    "triggers_on": True,
    "haptics_on": True,
    "haptics": {"master": 1.0, "boost": 2.5, "rumble_gain": 1.0, "engine": 1.2, "road": 2.5,
                "replace_rumble": True},
}

BG, CARD, CARD2, FG, DIM = "#0d1017", "#161b26", "#1d2433", "#e9edf5", "#8a93a6"
BLUE, GREEN, AMBER, RED = "#2f6bff", "#3fd68a", "#f5b942", "#ff5c6c"
FONT = "Segoe UI"


def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CFG) as f:
            u = json.load(f)
        for k, v in u.items():
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    except (OSError, ValueError):
        pass
    return c


class Part:
    """one background program (triggers or haptics)"""
    def __init__(self, name, q):
        self.name, self.q, self.p = name, q, None

    def running(self):
        return self.p is not None and self.p.poll() is None

    def start(self):
        if self.running():
            return
        try:
            os.remove(os.path.join(HERE, f".stop_{self.name}"))
        except OSError:
            pass
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.p = subprocess.Popen([PY, "-u", os.path.join(HERE, "runner.py"), self.name], cwd=HERE,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  creationflags=0x10, startupinfo=self._hidden(), env=env, text=True, encoding="utf-8", errors="replace")
        p = self.p
        threading.Thread(target=self._read, args=(p,), daemon=True).start()

    @staticmethod
    def _hidden():
        si = subprocess.STARTUPINFO()          # own console, kept hidden (audio libs like having one)
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return si

    def _read(self, p):
        for line in p.stdout:
            line = line.rstrip()
            if line:
                self.q.put((self.name, line))
        self.q.put((self.name, "__exit__"))

    def stop(self, wait=4.0):
        p = self.p
        if not p or p.poll() is not None:
            return
        try:
            open(os.path.join(HERE, f".stop_{self.name}"), "w").close()
        except OSError:
            pass
        t = time.time() + wait
        while p.poll() is None and time.time() < t:
            time.sleep(0.05)
        if p.poll() is None:
            p.kill()


class App:
    def __init__(self, root):
        self.root, self.cfg = root, load_cfg()
        self.q = queue.Queue()
        self.parts = {"triggers": Part("triggers", self.q), "haptics": Part("haptics", self.q)}
        self.game_pid = -1
        self.playing = False
        root.title("FH2 DualSense")
        try:
            root.iconbitmap(default=os.path.join(HERE, "icon.ico"))
        except tk.TclError:
            pass
        try:
            self._icon = tk.PhotoImage(file=os.path.join(HERE, "icon.png"))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        root.configure(bg=BG)
        root.geometry("560x860")
        root.minsize(480, 420)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.build()
        self.root.after(100, self.pump)
        threading.Thread(target=self.watch_game, daemon=True).start()

    # ---------------------------------------------------------------- UI
    def card(self, parent, title):
        f = tk.Frame(parent, bg=CARD, padx=16, pady=12)
        f.pack(fill="x", padx=18, pady=(0, 12))
        tk.Label(f, text=title.upper(), bg=CARD, fg=DIM, font=(FONT, 9, "bold")).pack(anchor="w")
        return f

    def build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=18, pady=(18, 10))
        tk.Label(top, text="FH2 DualSense", bg=BG, fg=FG, font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(top, text="Adaptive triggers + haptics for Forza Horizon 2", bg=BG, fg=DIM,
                 font=(FONT, 10)).pack(anchor="w")

        self.play = tk.Button(self.root, text="PLAY", command=self.toggle, bg=BLUE, fg="white",
                              activebackground="#4a80ff", activeforeground="white", relief="flat",
                              font=(FONT, 18, "bold"), cursor="hand2", bd=0, pady=12)
        self.play.pack(fill="x", padx=18, pady=(4, 14))

        # ---- scrollable area for everything below PLAY
        sty = ttk.Style(self.root)
        try:
            sty.theme_use("clam")
        except tk.TclError:
            pass
        sty.configure("Dark.Vertical.TScrollbar", background=CARD2, troughcolor=BG, bordercolor=BG,
                      arrowcolor=DIM, lightcolor=CARD2, darkcolor=CARD2, gripcount=0)
        sty.map("Dark.Vertical.TScrollbar", background=[("active", "#273046")])
        wrap = tk.Frame(self.root, bg=BG); wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(self.canvas, bg=BG)
        win = self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))

        def wheel(e):
            if str(e.widget).startswith(str(self.log)):
                return  # let the log scroll itself
            if body.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        self.root.bind_all("<MouseWheel>", wheel)

        st = self.card(body, "Status")
        self.dots = {}
        for key, label in [("game", "Game"), ("pad", "Controller"), ("triggers", "Adaptive triggers"),
                           ("haptics", "Haptics")]:
            row = tk.Frame(st, bg=CARD); row.pack(fill="x", pady=3)
            dot = tk.Label(row, text="\u25CF", bg=CARD, fg=DIM, font=(FONT, 12))
            dot.pack(side="left")
            tk.Label(row, text=label, bg=CARD, fg=FG, font=(FONT, 11), width=16, anchor="w").pack(side="left", padx=6)
            txt = tk.Label(row, text="off", bg=CARD, fg=DIM, font=(FONT, 10), anchor="w")
            txt.pack(side="left", fill="x", expand=True)
            self.dots[key] = (dot, txt)

        fx = self.card(body, "Features")
        self.v_trig = tk.BooleanVar(value=self.cfg["triggers_on"])
        self.v_hap = tk.BooleanVar(value=self.cfg["haptics_on"])
        self.v_rep = tk.BooleanVar(value=self.cfg["haptics"]["replace_rumble"])
        mode = self.cfg.get("launch_mode", "azimuth")
        if not self.cfg.get("launch_game", True):
            mode = "none"
        self.v_mode = tk.StringVar(value=mode)
        for var, text in [(self.v_trig, "Adaptive triggers (your tuned v2.2 feel)"),
                          (self.v_hap, "HD haptics (needs USB cable)"),
                          (self.v_rep, "Turn off the old controller buzz")]:
            tk.Checkbutton(fx, text=text, variable=var, command=self.changed, bg=CARD, fg=FG,
                           selectcolor=CARD2, activebackground=CARD, activeforeground=FG,
                           font=(FONT, 10), anchor="w", highlightthickness=0, bd=0).pack(fill="x", pady=1)

        hp = self.card(body, "Haptics tuning")
        h = self.cfg["haptics"]
        self.sliders = {}
        for key, label, lo, hi in [("master", "Overall strength", 0.2, 1.0),
                                   ("boost", "Sound detail", 0.5, 5.0),
                                   ("engine", "Engine", 0.0, 3.0),
                                   ("road", "Road surface", 0.0, 5.0),
                                   ("rumble_gain", "Punch (ABS, crashes, kerbs)", 0.0, 2.0)]:
            row = tk.Frame(hp, bg=CARD); row.pack(fill="x")
            tk.Label(row, text=label, bg=CARD, fg=FG, font=(FONT, 10), width=24, anchor="w").pack(side="left")
            var = tk.DoubleVar(value=float(h[key]))
            s = tk.Scale(row, variable=var, from_=lo, to=hi, resolution=0.1, orient="horizontal",
                         bg=CARD, fg=FG, troughcolor=CARD2, highlightthickness=0, bd=0,
                         activebackground=BLUE, sliderrelief="flat", length=230, font=(FONT, 8))
            s.pack(side="left", fill="x", expand=True)
            s.bind("<ButtonRelease-1>", lambda e: self.changed(retune=True))
            self.sliders[key] = var
        br = tk.Frame(hp, bg=CARD); br.pack(fill="x", pady=(6, 0))
        tk.Button(br, text="Reset to default", command=self.reset, bg=CARD2, fg=FG, relief="flat",
                  activebackground="#273046", activeforeground=FG, font=(FONT, 9), padx=10).pack(side="left")
        self.tune_note = tk.Label(br, text="", bg=CARD, fg=DIM, font=(FONT, 9))
        self.tune_note.pack(side="left", padx=10)

        gm = self.card(body, "When I press PLAY")
        for val, text in [("azimuth", "Start the game with azimuth.exe"),
                          ("dlc", "Start the game with FH2 DLC Launcher.exe"),
                          ("none", "Only start the mod, I'll start the game myself")]:
            tk.Radiobutton(gm, text=text, value=val, variable=self.v_mode, command=self.save, bg=CARD, fg=FG,
                           selectcolor=CARD2, activebackground=CARD, activeforeground=FG, font=(FONT, 10),
                           anchor="w", highlightthickness=0, bd=0).pack(fill="x", pady=1)
        row = tk.Frame(gm, bg=CARD); row.pack(fill="x", pady=(6, 0))
        self.path_lbl = tk.Label(row, text=self.short(self.game_dir()) or "game folder not set",
                                 bg=CARD, fg=FG if self.cfg["azimuth_path"] else DIM, font=(FONT, 10), anchor="w")
        self.path_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse", command=self.browse, bg=CARD2, fg=FG, relief="flat",
                  activebackground="#273046", activeforeground=FG, font=(FONT, 9), padx=10).pack(side="right")

        lg = tk.Frame(body, bg=BG); lg.pack(fill="x", padx=18, pady=(0, 14))
        self.log = tk.Text(lg, bg="#0a0d13", fg=DIM, font=("Consolas", 9), height=8, relief="flat",
                           wrap="word", state="disabled", padx=8, pady=6)
        self.log.pack(fill="both", expand=True)

    def short(self, p):
        return ("..." + p[-48:]) if len(p) > 51 else p

    def set(self, key, color, text):
        dot, txt = self.dots[key]
        dot.config(fg=color); txt.config(text=text, fg=FG if color != DIM else DIM)

    def say(self, line):
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------------------------------------------------------------- settings
    def save(self):
        c = self.cfg
        c["triggers_on"], c["haptics_on"] = self.v_trig.get(), self.v_hap.get()
        c["launch_mode"] = self.v_mode.get()
        c["launch_game"] = c["launch_mode"] != "none"
        c["haptics"]["replace_rumble"] = self.v_rep.get()
        for k, v in self.sliders.items():
            c["haptics"][k] = round(v.get(), 2)
        try:
            with open(CFG, "w") as f:
                json.dump(c, f, indent=2)
        except OSError as e:
            self.say(f"Couldn't save settings: {e}")

    def changed(self, retune=False):
        self.save()
        if not self.playing:
            return
        # turn parts on/off live; haptics restarts to pick up new tuning
        for name, on in [("triggers", self.v_trig.get()), ("haptics", self.v_hap.get())]:
            part = self.parts[name]
            if on and not part.running():
                part.start()
            elif not on and part.running():
                threading.Thread(target=part.stop, daemon=True).start()
                self.set(name, DIM, "off")
        if retune and self.v_hap.get():
            self.tune_note.config(text="applying...")
            threading.Thread(target=self.restart_haptics, daemon=True).start()

    def restart_haptics(self):
        part = self.parts["haptics"]
        part.stop()
        part.start()
        self.root.after(0, lambda: self.tune_note.config(text="applied"))

    def reset(self):
        for k, v in self.sliders.items():
            v.set(DEFAULTS["haptics"][k])
        self.changed(retune=True)

    def game_dir(self):
        p = self.cfg.get("azimuth_path", "")
        return os.path.dirname(p) if p else ""

    def game_exe(self):
        d = self.game_dir()
        if not d:
            return ""
        return os.path.join(d, "FH2 DLC Launcher.exe" if self.v_mode.get() == "dlc" else "azimuth.exe")

    def browse(self):
        p = filedialog.askopenfilename(title="Find azimuth.exe or FH2 DLC Launcher.exe",
                                       filetypes=[("Programs", "*.exe")])
        if p:
            p = os.path.normpath(p)
            self.cfg["azimuth_path"] = os.path.join(os.path.dirname(p), "azimuth.exe")
            if "dlc" in os.path.basename(p).lower():
                self.v_mode.set("dlc")
            self.path_lbl.config(text=self.short(self.game_dir()), fg=FG)
            self.save()

    # ---------------------------------------------------------------- play / stop
    def toggle(self):
        if self.playing:
            self.stop_all()
        else:
            self.start_all()

    def start_all(self):
        self.save()
        running = bool(self.game_pid and self.game_pid > 0)
        if self.v_mode.get() != "none" and not running:
            exe = self.game_exe()
            if not exe or not os.path.exists(exe):
                self.browse(); exe = self.game_exe()
            if exe and os.path.exists(exe):
                try:
                    subprocess.Popen([exe], cwd=os.path.dirname(exe))
                    self.say(f"Starting Forza Horizon 2 ({os.path.basename(exe)})...")
                except OSError as e:
                    self.say(f"Couldn't start the game: {e}")
            elif exe:
                self.say(f"{os.path.basename(exe)} isn't in the game folder.")
        elif not running:
            self.say("Mod on. Start Forza Horizon 2 whenever you like.")
        self.playing = True
        self.play.config(text="STOP", bg="#2a3142", activebackground="#353e52")
        if self.v_trig.get():
            self.parts["triggers"].start(); self.set("triggers", AMBER, "starting")
        if self.v_hap.get():
            self.parts["haptics"].start(); self.set("haptics", AMBER, "starting")

    def stop_all(self):
        self.playing = False
        self.play.config(text="PLAY", bg=BLUE, activebackground="#4a80ff")
        for name, part in self.parts.items():
            threading.Thread(target=part.stop, daemon=True).start()
            self.set(name, DIM, "off")
        self.set("pad", DIM, "off")

    def quit(self):
        for part in self.parts.values():
            part.stop(wait=3.0)
        self.root.destroy()

    # ---------------------------------------------------------------- live status
    def watch_game(self):
        while True:
            pid = None
            if az:
                try:
                    pid = az.find_pid()
                except Exception:
                    pid = None
            if pid != self.game_pid:
                self.game_pid = pid
                self.q.put(("game", "running" if pid else "not running"))
            time.sleep(2)

    def pump(self):
        try:
            while True:
                src, line = self.q.get_nowait()
                if src == "game":
                    self.set("game", GREEN if line == "running" else DIM, line)
                    continue
                if line == "__exit__":
                    if self.playing and src in self.parts:
                        self.set(src, RED, "stopped (see log)")
                    continue
                self.say(f"[{src}] {line}")
                self.read_status(src, line)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def read_status(self, src, line):
        l = line.lower()
        if src == "triggers":
            if l.startswith("dualsense connected"):
                conn = "Bluetooth" if "bluetooth" in l else "USB"
                self.set("pad", GREEN, conn); self.set("triggers", AMBER, "waiting for the game")
            elif "hooked into forza" in l:
                self.set("triggers", GREEN, "on, game trigger rumble live")
            elif "basic mode" in l:
                self.set("triggers", AMBER, "basic mode (no game hook)")
            elif "game closed" in l:
                self.set("triggers", AMBER, "waiting for the game")
            elif "no dualsense" in l or "not found" in l:
                self.set("pad", RED, "not found"); self.set("triggers", RED, "no controller")
        else:
            if l.startswith("haptics output"):
                self.set("haptics", AMBER, "waiting for the game")
                if self.dots["pad"][1].cget("text") in ("off", ""):
                    self.set("pad", GREEN, "USB")
            elif l.startswith("sound tap loaded"):
                self.set("haptics", GREEN, "on")
            elif l.startswith("sound tap: connected") and "buses: none" not in l:
                n = len(line.split("Buses:")[1].split(","))
                self.set("haptics", GREEN, f"on, {n} car sound channels")
            elif l.startswith("sound tap") and "failed" in l:
                self.set("haptics", AMBER, line.split(":", 1)[1].strip().split(".")[0])
            elif "no dualsense audio" in l:
                self.set("haptics", RED, "plug the controller in with USB")
            elif "stereo" in l:
                self.set("haptics", RED, "set the DualSense speaker to Quadraphonic")
            elif "game closed" in l:
                self.set("haptics", AMBER, "waiting for the game")


if __name__ == "__main__":
    try:   # own taskbar icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("amine.FH2DualSense")
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
