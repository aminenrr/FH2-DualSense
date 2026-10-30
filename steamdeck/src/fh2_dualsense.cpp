// FH2DualSense - FH2 Mod Platform mod for Azimuth (0.9.10, 1.0.1) on Linux / Steam Deck (Proton).
//
// Game side of a DualSense port of aminenrr/FH2-DualSense (MIT):
//  * reads the game's 4 vibration levels (left motor, right motor, left trigger,
//    right trigger) where Azimuth hands them to Windows.Gaming.Input
//    (IGamepad::put_Vibration), with an instruction hook;
//  * taps the game's own car sound buses inside FMOD (code from fh2_fmodtap.c,
//    same RVAs, game build 1.0.0.64) and mixes them into a haptics signal;
//  * shares both with the Linux helper (fh2-dualsense.py) through a file in
//    /dev/shm, which drives the controller (hidraw) and its USB audio.

#include <windows.h>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include "fh2mod.h"

#define FH2DS_VERSION "1.0.0"

static const FH2ModApi* g_api;
static FH2Mod* g_mod;

static void logf(const char* fmt, ...) {
    char line[512];
    va_list a;
    va_start(a, fmt);
    vsnprintf(line, sizeof line, fmt, a);
    va_end(a);
    g_api->log(g_mod, line);
}

// ---- shared memory (layout mirrored in linux/fh2-dualsense.py) ------------
constexpr uint32_t kMagic = 0x44324846;  // "FH2D"
constexpr uint32_t kOutFrames = 16384;    // 4-channel s16 ring
struct Shared {
    uint32_t magic, version, heartbeat, vib_count;  // 0
    float vib[4];                                   // 16: LM RM LT RT (0..1)
    uint32_t state;                                 // 32: bit0 vib hook, bit1 fmod hooked, bit2 sound flowing
    uint32_t attached;                              // 36: FMOD buses tapped (bit mask)
    uint32_t fmod_rate;                             // 40
    uint32_t out_widx;                              // 44: frames written to out[]
    uint32_t set_triggers, set_haptics;             // 48, 52: Mods page switches
    float trig_strength, hapt_strength;             // 56, 60: Mods page sliders
    uint32_t haptics_active;                        // 64: written by the helper (USB audio open)
    uint32_t helper_heartbeat;                      // 68: written by the helper
    uint8_t pad[256 - 72];
    int16_t out[kOutFrames * 4];                    // 256
};
static_assert(sizeof(Shared) == 256 + kOutFrames * 8, "layout");
static Shared* S;

static bool open_shared() {
    HANDLE f = CreateFileW(L"Z:\\dev\\shm\\fh2-dualsense.shm", GENERIC_READ | GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) { logf("can't open Z:\\dev\\shm\\fh2-dualsense.shm (%lu)", GetLastError()); return false; }
    LARGE_INTEGER sz; sz.QuadPart = sizeof(Shared);
    SetFilePointerEx(f, sz, nullptr, FILE_BEGIN);
    SetEndOfFile(f);
    HANDLE m = CreateFileMappingW(f, nullptr, PAGE_READWRITE, 0, sizeof(Shared), nullptr);
    if (!m) { logf("CreateFileMapping failed (%lu)", GetLastError()); return false; }
    S = static_cast<Shared*>(MapViewOfFile(m, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared)));
    if (!S) { logf("MapViewOfFile failed (%lu)", GetLastError()); return false; }
    std::memset(S, 0, 256);
    S->version = 1;
    S->fmod_rate = 48000;
    return true;
}

// ---- settings (Mods page; saved in azimuth\fh2-dualsense\settings.ini) ----
static std::wstring g_ini;
static int ini_int(const wchar_t* k, int d) { return GetPrivateProfileIntW(L"dualsense", k, d, g_ini.c_str()); }
static void ini_put(const wchar_t* k, const wchar_t* v) { WritePrivateProfileStringW(L"dualsense", k, v, g_ini.c_str()); }
static int g_triggers = 1, g_haptics = 1, g_trig_pct = 100, g_hapt_pct = 100;
static void push_settings() {
    if (!S) return;
    S->set_triggers = g_triggers; S->set_haptics = g_haptics;
    S->trig_strength = g_trig_pct / 100.0f; S->hapt_strength = g_hapt_pct / 100.0f;
}
static void on_change(void*, const char* key, int v) {
    if (!std::strcmp(key, "Triggers")) { g_triggers = v; ini_put(L"triggers", v ? L"1" : L"0"); }
    if (!std::strcmp(key, "Haptics")) { g_haptics = v; ini_put(L"haptics", v ? L"1" : L"0"); }
    push_settings();
}
static void on_slide(void*, const char* key, float v) {
    wchar_t b[16]; int p = (int)std::lround(v * 100);
    swprintf(b, 16, L"%d", p);
    if (!std::strcmp(key, "TriggerForce")) { g_trig_pct = p; ini_put(L"trigger_force", b); }
    if (!std::strcmp(key, "HapticsStrength")) { g_hapt_pct = p; ini_put(L"haptics_strength", b); }
    push_settings();
}
static void add_rows() {
    if (g_api->api_version < 4) return;
    FH2Setting t{}; t.size = sizeof t; t.key = "Triggers"; t.label = "DualSense adaptive triggers";
    t.description = "Brake and throttle resistance, plus the game's own trigger rumble (ABS, lock-up, wheelspin) on L2/R2. "
                    "USB or Bluetooth.";
    t.value = g_triggers; t.on_change = on_change; g_api->add_setting(g_mod, &t);
    FH2Setting h{}; h.size = sizeof h; h.key = "Haptics"; h.label = "DualSense HD haptics (USB)";
    h.description = "Engine, road, skid, impact and gear-shift sounds on the DualSense actuators instead of the old rumble. "
                    "Only with a USB cable.";
    h.value = g_haptics; h.on_change = on_change; g_api->add_setting(g_mod, &h);
    if (g_api->api_version < 5) return;
    FH2Setting f{}; f.size = sizeof f; f.key = "TriggerForce"; f.label = "Trigger force";
    f.description = "Scales the trigger resistance and rumble."; f.kind = FH2_SETTING_SLIDER;
    f.min = 0.25f; f.max = 1.5f; f.step = 0.05f; f.slider_value = g_trig_pct / 100.0f; f.display_scale = 100; f.on_slide = on_slide;
    g_api->add_setting(g_mod, &f);
    FH2Setting s{}; s.size = sizeof s; s.key = "HapticsStrength"; s.label = "Haptics strength";
    s.description = "Overall strength of the HD haptics."; s.kind = FH2_SETTING_SLIDER;
    s.min = 0.25f; s.max = 2.0f; s.step = 0.05f; s.slider_value = g_hapt_pct / 100.0f; s.display_scale = 100; s.on_slide = on_slide;
    g_api->add_setting(g_mod, &s);
}

// ---- vibration levels: Azimuth -> IGamepad::put_Vibration (found by pattern; 0.9.10 and 1.0.1) 
// ...cvtss2sd xmm0,xmm6 / movsd [rbp+d4],xmm0 / mov rax,[rbx] / lea rdx,[rbp+d] / mov rcx,rbx / call [rax+38h]
static const char kVibPat[] = "0FC6F6FF0F57C0F30F5AC6F20F1145??488B03488D55??488BCBFF5038";
static int8_t g_vib_disp;
static volatile LONG g_mute;  // zero the motors (haptics replace them)

static unsigned char* find_pattern(unsigned char* p, size_t n, const char* hex, size_t* len_out) {
    unsigned char pat[64]; bool any[64]; size_t len = 0;
    for (const char* h = hex; h[0] && h[1]; h += 2, ++len) {
        any[len] = h[0] == '?';
        if (!any[len]) { char b[3] = {h[0], h[1], 0}; pat[len] = (unsigned char)strtoul(b, nullptr, 16); }
    }
    *len_out = len;
    for (size_t i = 0; i + len <= n; ++i) {
        size_t j = 0;
        while (j < len && (any[j] || p[i + j] == pat[j])) ++j;
        if (j == len) return p + i;
    }
    return nullptr;
}

static void on_vibration(FH2Context* ctx, void*) {
    double* v = reinterpret_cast<double*>(ctx->rbp + g_vib_disp);  // GamepadVibration: 4 doubles
    if (S) {
        for (int i = 0; i < 4; ++i) S->vib[i] = (float)v[i];
        S->vib_count++;
    }
    if (g_mute) { v[0] = 0; v[1] = 0; }
}

static void install_vibration_hook() {
    auto* base = reinterpret_cast<unsigned char*>(GetModuleHandleW(nullptr));
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(base + reinterpret_cast<IMAGE_DOS_HEADER*>(base)->e_lfanew);
    size_t len = 0;
    unsigned char* m = find_pattern(base, nt->OptionalHeader.SizeOfImage, kVibPat, &len);
    if (!m || find_pattern(m + 1, nt->OptionalHeader.SizeOfImage - (m + 1 - base), kVibPat, &len)) {
        logf("vibration call not found (or not unique) in azimuth.exe: this Azimuth build isn't supported, triggers off");
        return;
    }
    g_vib_disp = (int8_t)m[22];
    unsigned char* at = m + 16;  // mov rax,[rbx]: the 4 doubles are in place
    FH2Hook* h = g_api->hook_instruction(g_mod, "Azimuth put_Vibration levels", at, on_vibration, nullptr);
    if (h && g_api->enable_hooks(g_mod, h)) {
        if (S) S->state |= 1;
        logf("vibration levels hooked at azimuth+0x%llx (struct at rbp%+d)", (unsigned long long)(at - base), g_vib_disp);
    } else {
        logf("vibration hook refused (see above)");
    }
}

// ---- FMOD tap (fh2_fmodtap.c by aminenrr, MIT) ----------------------------
constexpr int NSLOT = 8;
constexpr uint32_t RING = 16384;
static const char* SLOT_PATHS[NSLOT] = {
    "bus:/ANTHEM/01_PLAYER_CAR", "bus:/ANTHEM/02_PLAYER_ROLL", "bus:/ANTHEM/03_PLAYER_SKID",
    "bus:/ANTHEM/04_PLAYER_KICKUP", "bus:/ANTHEM/05_PLAYER_IMPACT", "bus:/ANTHEM/06_WORLD_IMPACT",
    "bus:/ANTHEM/07_SMASHABLES", "bus:/FM5 Content/Cockpit_Shift"};
// RVAs in forza_durango_release_final.exe 1.0.0.64
#define RVA_getSwFormat   0x1999f70
#define RVA_createDSP     0x19970d0
#define RVA_addDSP        0x19a16e0
#define RVA_setActive     0x19a9be0
#define RVA_S_setListener 0x1a9ac60
#define RVA_S_getEvent    0x1a97b20
#define RVA_S_getLLS      0x1a98080
#define RVA_S_lookupID    0x1a99830
#define RVA_S_getStrip    0x1a98300
#define RVA_S_stripCG     0x1a972f0
static const unsigned char CHK_setListener[18] = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x40,0x01,0x00,0x00};
static const unsigned char CHK_getEvent[15]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};
static const unsigned char CHK_getStrip[15]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};
static const unsigned char CHK_lookupID[13]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x48,0x81,0xec,0x48,0x01,0x00,0x00};
static const unsigned char CHK_stripCG[18]     = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x40,0x01,0x00,0x00};
static const unsigned char CHK_getLLS[18]      = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};

typedef long long (*f_gen4)(void*, void*, void*, void*);
typedef int (*f_getSwFormat)(void*, int*, int*, int*);
typedef int (*f_createDSP)(void*, const void*, void**);
typedef int (*f_addDSP)(void*, int, void*, void**);
typedef int (*f_setActive)(void*, int);
typedef int (*f_getLLS)(void*, void**);
typedef int (*f_lookupID)(void*, const char*, void*);
typedef int (*f_getStrip)(void*, const void*, int, void**);
typedef int (*f_stripCG)(void*, void**);
static f_gen4 t_setListener, t_getEvent;
static f_getSwFormat p_getSwFormat; static f_createDSP p_createDSP; static f_addDSP p_addDSP;
static f_setActive p_setActive; static f_getLLS p_getLLS; static f_lookupID p_lookupID;
static f_getStrip p_getStrip; static f_stripCG p_stripCG;

static float g_ring[NSLOT][RING];
static volatile uint32_t g_widx[NSLOT];
static void *g_studio, *g_core, *g_dsp[NSLOT], *g_grp[NSLOT];
static int g_lasterr[NSLOT];
static ULONGLONG g_next;
static volatile LONG g_busy;

typedef int (*dsp_cb)(void*);
typedef int (*dsp_read)(void*, float*, float*, unsigned, int, int*);
struct DSPDesc {  // FMOD 1.03 DSP description (plugin SDK 105, 192 bytes)
    unsigned pluginsdkversion; char name[32]; unsigned version;
    int numinputbuffers, numoutputbuffers;
    dsp_cb create, release, reset; dsp_read read;
    void *process, *setposition;
    int numparameters; int _pad; void* paramdesc;
    void *setf, *seti, *setb, *setd, *getf, *geti, *getb, *getd;
    void *shouldiprocess, *userdata;
};
static_assert(sizeof(DSPDesc) == 192, "FMOD DSP description");
static DSPDesc g_desc[NSLOT];
static int dsp_ok(void*) { return 0; }

static inline void capture(int slot, const float* in, float* out, unsigned len, int ch) {
    std::memcpy(out, in, (size_t)len * ch * sizeof(float));  // pass-through
    uint32_t w = g_widx[slot];
    float* r = g_ring[slot];
    float k = ch > 0 ? 1.0f / ch : 1.0f;
    for (unsigned i = 0; i < len; i++) {
        float s = 0; const float* p = in + (size_t)i * ch;
        for (int c = 0; c < ch; c++) s += p[c];
        r[(w + i) & (RING - 1)] = s * k;
    }
    MemoryBarrier();
    g_widx[slot] = w + len;
}
template <int N> static int rd(void*, float* in, float* out, unsigned len, int inch, int* outch) {
    if (outch) *outch = inch;
    capture(N, in, out, len, inch);
    return 0;
}
static dsp_read READS[NSLOT] = {rd<0>, rd<1>, rd<2>, rd<3>, rd<4>, rd<5>, rd<6>, rd<7>};

static void studio_tick(void* studio) {
    if (!studio) return;
    ULONGLONG now = GetTickCount64();
    if (now < g_next) return;
    if (InterlockedCompareExchange(&g_busy, 1, 0) != 0) return;
    g_next = now + 1000;
    if (g_studio != studio) {
        g_studio = studio;
        int r = p_getLLS(studio, &g_core);
        logf("FMOD Studio system %p, core %p (%d)", studio, g_core, r);
        if (r == 0 && g_core) {
            int rate = 0, sm = 0, raw = 0;
            p_getSwFormat(g_core, &rate, &sm, &raw);
            if (S) S->fmod_rate = rate ? (uint32_t)rate : 48000;
            logf("FMOD mixer %d Hz", rate);
            for (int s = 0; s < NSLOT; s++) {
                if (g_dsp[s]) continue;
                DSPDesc* d = &g_desc[s];
                std::memset(d, 0, sizeof *d);
                d->pluginsdkversion = 105;
                snprintf(d->name, 32, "FH2DSTap%d", s);
                d->version = 0x10000;
                d->numinputbuffers = 1; d->numoutputbuffers = 1;
                d->create = dsp_ok; d->release = dsp_ok; d->reset = dsp_ok;
                d->read = READS[s];
                int e = p_createDSP(g_core, d, &g_dsp[s]);
                if (e) { logf("createDSP %d failed (%d)", s, e); g_dsp[s] = nullptr; }
            }
        }
    }
    bool all = true;
    for (int s = 0; s < NSLOT; s++) {
        if (g_grp[s] || !g_dsp[s]) continue;
        all = false;
        unsigned char guid[16] = {0};
        void *strip = nullptr, *cg = nullptr, *conn = nullptr;
        int e = p_lookupID(studio, SLOT_PATHS[s], guid);
        if (!e) e = p_getStrip(studio, guid, 0, &strip);
        if (!e && strip) e = p_stripCG(strip, &cg);
        if (!e && cg) {
            e = p_addDSP(cg, 0, g_dsp[s], &conn);
            if (!e) {
                p_setActive(g_dsp[s], 1);
                g_grp[s] = cg;
                if (S) S->attached |= 1u << s;
                logf("sound tap on %s", SLOT_PATHS[s]);
                continue;
            }
        }
        if (e != g_lasterr[s]) { g_lasterr[s] = e; logf("%s not ready yet (error %d)", SLOT_PATHS[s], e); }
    }
    if (all) g_next = now + 30000;
    InterlockedExchange(&g_busy, 0);
}
static long long hk_setListener(void* a, void* b, void* c, void* d) { studio_tick(a); return t_setListener(a, b, c, d); }
static long long hk_getEvent(void* a, void* b, void* c, void* d) { studio_tick(a); return t_getEvent(a, b, c, d); }

static void install_fmod_tap(unsigned char* base) {
    if (std::memcmp(base + RVA_S_setListener, CHK_setListener, 18) || std::memcmp(base + RVA_S_getEvent, CHK_getEvent, 15) ||
        std::memcmp(base + RVA_S_getStrip, CHK_getStrip, 15) || std::memcmp(base + RVA_S_lookupID, CHK_lookupID, 13) ||
        std::memcmp(base + RVA_S_stripCG, CHK_stripCG, 18) || std::memcmp(base + RVA_S_getLLS, CHK_getLLS, 18)) {
        logf("FMOD Studio code isn't game build 1.0.0.64: HD haptics off");
        return;
    }
    p_getSwFormat = (f_getSwFormat)(base + RVA_getSwFormat);
    p_createDSP = (f_createDSP)(base + RVA_createDSP);
    p_addDSP = (f_addDSP)(base + RVA_addDSP);
    p_setActive = (f_setActive)(base + RVA_setActive);
    p_getLLS = (f_getLLS)(base + RVA_S_getLLS);
    p_lookupID = (f_lookupID)(base + RVA_S_lookupID);
    p_getStrip = (f_getStrip)(base + RVA_S_getStrip);
    p_stripCG = (f_stripCG)(base + RVA_S_stripCG);
    FH2Hook* a = g_api->hook_function(g_mod, "FMOD Studio::System::setListenerAttributes", base + RVA_S_setListener,
                                      (void*)hk_setListener, (void**)&t_setListener);
    FH2Hook* b = g_api->hook_function(g_mod, "FMOD Studio::System::getEvent", base + RVA_S_getEvent,
                                      (void*)hk_getEvent, (void**)&t_getEvent);
    if (a && b && g_api->enable_hooks(g_mod, a) && g_api->enable_hooks(g_mod, b)) {
        if (S) S->state |= 2;
        logf("FMOD Studio hooked");
    } else {
        logf("FMOD hooks refused (see above): HD haptics off");
    }
}

// ---- haptics mixer (fh2_haptics.py by aminenrr, MIT, in C) ----------------
struct Biquad {
    float b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1 = 0, z2 = 0;
    void set(bool high, float fc, float fs) {  // RBJ, Butterworth Q
        float w = 2.0f * 3.14159265f * fc / fs, c = std::cos(w), al = std::sin(w) / (2.0f * 0.70710678f), a0 = 1 + al;
        if (high) { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = (1 + c) / 2; }
        else { b0 = (1 - c) / 2; b1 = 1 - c; b2 = (1 - c) / 2; }
        b0 /= a0; b1 /= a0; b2 /= a0; a1 = -2 * c / a0; a2 = (1 - al) / a0;
    }
    float run(float x) { float y = b0 * x + z1; z1 = b1 * x - a1 * y + z2; z2 = b2 * x - a2 * y; return y; }
};
struct Bus { float gain, lo, hi, left, right; };
static const Bus BUSES[NSLOT] = {  // gain, low cut, high cut, left share, right share
    {1.2f, 25, 250, 1.0f, 0.8f}, {2.5f, 20, 300, 0.8f, 1.0f}, {1.6f, 50, 400, 0.6f, 1.0f}, {2.0f, 60, 450, 0.8f, 1.0f},
    {3.0f, 0, 300, 1.0f, 1.0f},  {2.0f, 0, 300, 1.0f, 1.0f},  {1.5f, 0, 300, 1.0f, 1.0f},  {2.5f, 0, 300, 1.0f, 1.0f}};
constexpr float kBoost = 2.5f, kAutoMax = 12.0f, kRumbleGain = 1.0f, kRumbleDuck = 0.6f, kHeavyHz = 55, kLightHz = 170;
constexpr int kRate = 48000;

static DWORD WINAPI mixer_thread(void*) {
    Biquad hp[NSLOT], lp[NSLOT];
    for (int s = 0; s < NSLOT; ++s) {
        if (BUSES[s].lo) hp[s].set(true, BUSES[s].lo, kRate);
        lp[s].set(false, BUSES[s].hi, kRate);
    }
    float env[NSLOT] = {0};
    double rd_pos[NSLOT] = {0};
    bool have_rd[NSLOT] = {false}, silent[NSLOT];
    double ph[2] = {0, 0};
    float sm[2] = {0, 0};
    const float decay = std::pow(0.5f, 1.0f / kRate / 4.0f);
    LARGE_INTEGER f, t0, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t0);
    uint64_t made = 0;
    bool was_on = false;
    static float x[NSLOT][2400];
    for (;;) {
        Sleep(5);
        bool on = S && S->haptics_active && S->set_haptics;
        InterlockedExchange(&g_mute, on ? 1 : 0);
        if (!on) { was_on = false; continue; }
        QueryPerformanceCounter(&t);
        if (!was_on) { t0 = t; made = 0; for (auto& h : have_rd) h = false; was_on = true; }
        uint64_t due = (uint64_t)((t.QuadPart - t0.QuadPart) * (double)kRate / f.QuadPart);
        int n = (int)(due - made);
        if (n <= 0) continue;
        if (n > 2400) { made = due - 2400; n = 2400; }
        const double rate = S->fmod_rate ? S->fmod_rate : kRate, step = rate / kRate;
        const uint32_t target = (uint32_t)(rate * 0.030f);
        bool flow = false;
        for (int s = 0; s < NSLOT; ++s) {  // read each bus, resampled to 48 kHz
            const uint32_t w = g_widx[s];
            double r = rd_pos[s];
            uint32_t lag = have_rd[s] ? (uint32_t)(w - (uint32_t)r) : 0;
            if (!have_rd[s] || lag > RING / 2 || lag > target * 4) { r = (double)(uint32_t)(w - target); have_rd[s] = true; }
            bool any = false;
            for (int i = 0; i < n; ++i) {
                uint32_t ri = (uint32_t)r;
                float v = 0;
                if ((uint32_t)(w - ri) > 1) {
                    float fr = (float)(r - std::floor(r));
                    v = g_ring[s][ri & (RING - 1)] * (1 - fr) + g_ring[s][(ri + 1) & (RING - 1)] * fr;
                    r += step;
                }
                x[s][i] = v;
                any |= v != 0;
            }
            rd_pos[s] = std::fmod(r, 4294967296.0);
            silent[s] = !any;
            flow |= any;
        }
        const float lm = S->vib[0] < 0 ? 0 : (S->vib[0] > 1 ? 1 : S->vib[0]);
        const float rm = S->vib[1] < 0 ? 0 : (S->vib[1] > 1 ? 1 : S->vib[1]);
        const float rg = kRumbleGain, master = S->hapt_strength > 0 ? S->hapt_strength : 1.0f;
        const float tgt[2] = {lm * rg, rm * rg};
        int16_t* out = S->out;
        uint32_t wi = S->out_widx;
        for (int i = 0; i < n; ++i) {
            float L = 0, R = 0;
            for (int s = 0; s < NSLOT; ++s) {
                if (silent[s]) continue;
                float y = lp[s].run(BUSES[s].lo ? hp[s].run(x[s][i]) : x[s][i]);
                env[s] = std::fabs(y) > env[s] * decay ? std::fabs(y) : env[s] * decay;
                float k = kBoost;
                if (env[s] > 1e-4f) { float g = 0.6f / env[s]; k *= g < 1 ? 1 : (g > kAutoMax ? kAutoMax : g); }
                L += k * BUSES[s].gain * BUSES[s].left * y;
                R += k * BUSES[s].gain * BUSES[s].right * y;
            }
            float a0 = sm[0] + (tgt[0] - sm[0]) * i / n, a1 = sm[1] + (tgt[1] - sm[1]) * i / n;
            float w0 = (float)std::sin(2 * 3.14159265358979 * (ph[0] + kHeavyHz * (double)i / kRate));
            float w1 = (float)std::sin(2 * 3.14159265358979 * (ph[1] + kLightHz * (double)i / kRate));
            float rL = a0 * w0 + a1 * w1 * 0.4f, rR = a1 * w1 + a0 * w0 * 0.4f;
            float duck = 1 - kRumbleDuck * (a0 > a1 ? (a0 > 1 ? 1 : a0) : (a1 > 1 ? 1 : a1));
            float oL = (std::tanh(L) * duck + rL) * master, oR = (std::tanh(R) * duck + rR) * master;
            oL = oL > 1 ? 1 : (oL < -1 ? -1 : oL);
            oR = oR > 1 ? 1 : (oR < -1 ? -1 : oR);
            int16_t* fr = out + ((wi + i) & (kOutFrames - 1)) * 4;
            fr[0] = 0; fr[1] = 0; fr[2] = (int16_t)(oL * 32767); fr[3] = (int16_t)(oR * 32767);
        }
        sm[0] = tgt[0]; sm[1] = tgt[1];
        ph[0] = std::fmod(ph[0] + kHeavyHz * (double)n / kRate, 1.0);
        ph[1] = std::fmod(ph[1] + kLightHz * (double)n / kRate, 1.0);
        MemoryBarrier();
        S->out_widx = wi + n;
        if (flow) S->state |= 4; else S->state &= ~4u;
        made += n;
    }
}

static DWORD WINAPI heartbeat_thread(void*) {
    for (;;) { if (S) { S->heartbeat++; if (!S->magic) S->magic = kMagic; } Sleep(50); }
}

static DWORD WINAPI start_thread(void*) {
    install_vibration_hook();
    auto* base = static_cast<unsigned char*>(g_api->wait_for_code(0xFFFFFFFFu));
    if (!base) { logf("game never became ready"); return 0; }
    install_fmod_tap(base);
    return 0;
}

extern "C" __declspec(dllexport) int fh2mod_init(const FH2ModApi* api) {
    g_api = api;
    FH2ModInfo info{};
    info.size = sizeof info;
    info.name = "FH2DualSense";
    info.version = FH2DS_VERSION;
    info.github_repo = nullptr;
    info.platform_version = FH2MOD_PLATFORM_VERSION;
    info.check_updates = 0;
    info.description = "DualSense adaptive triggers (USB or Bluetooth) and HD haptics (USB) on Linux/Proton, with the "
                       "fh2-dualsense.py helper. Port of aminenrr/FH2-DualSense.";
    g_mod = api->register_mod(&info);
    if (!g_mod) return 1;
    if (api->api_version < 7) { api->log(g_mod, "needs platform API 7 (instruction hooks)"); return 1; }
    g_ini = std::wstring(api->azimuth_dir) + L"\\fh2-dualsense\\settings.ini";
    CreateDirectoryW((std::wstring(api->azimuth_dir) + L"\\fh2-dualsense").c_str(), nullptr);
    g_triggers = ini_int(L"triggers", 1);
    g_haptics = ini_int(L"haptics", 1);
    g_trig_pct = ini_int(L"trigger_force", 100);
    g_hapt_pct = ini_int(L"haptics_strength", 100);
    add_rows();
    if (!open_shared()) return 0;
    push_settings();
    S->magic = kMagic;
    CreateThread(nullptr, 0, heartbeat_thread, nullptr, 0, nullptr);
    CreateThread(nullptr, 0, mixer_thread, nullptr, 0, nullptr);
    CreateThread(nullptr, 0, start_thread, nullptr, 0, nullptr);
    logf("FH2DualSense %s started", FH2DS_VERSION);
    return 0;
}
