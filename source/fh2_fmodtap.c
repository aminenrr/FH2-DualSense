// FH2 FMOD tap v4.1 - original code, personal use.
// Loaded into azimuth.exe. Uses the FMOD Studio 1.03 + Core API compiled into
// forza_durango_release_final.exe (1.0.0.64). It catches the game's Studio
// system handle (hooks System::setListenerAttributes and System::getEvent),
// looks the player-car buses up BY PATH (bus:/ANTHEM/01_PLAYER_CAR ...),
// gets each bus's channel group and adds a pass-through capture DSP.
// Each bus's sound goes mono into shared memory "FH2HapticsTap" for
// fh2_haptics.py. Radio, voice, music and ambience are never touched.
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

#define NSLOT 8
#define RING 16384
#define MAGIC 0x31544846u /* 'FHT1' */

typedef struct {
    volatile unsigned magic, version, rate, nslot;
    volatile unsigned attached;      // bit per slot
    volatile unsigned heartbeat;
    volatile unsigned state;         // 1 found image, 2 hooked, 3 studio seen
    volatile unsigned pad;
    char names[NSLOT][32];
    volatile unsigned widx[NSLOT];
    float ring[NSLOT][RING];
} Shared;

static const char *SLOT_NAMES[NSLOT] = {
    "engine", "road", "skid", "kickup", "impact", "world_impact", "smash", "shift"};
static const char *SLOT_PATHS[NSLOT] = {
    "bus:/ANTHEM/01_PLAYER_CAR", "bus:/ANTHEM/02_PLAYER_ROLL", "bus:/ANTHEM/03_PLAYER_SKID",
    "bus:/ANTHEM/04_PLAYER_KICKUP", "bus:/ANTHEM/05_PLAYER_IMPACT", "bus:/ANTHEM/06_WORLD_IMPACT",
    "bus:/ANTHEM/07_SMASHABLES", "bus:/FM5 Content/Cockpit_Shift"};

// RVAs in forza_durango_release_final.exe 1.0.0.64
#define RVA_update        0x199d510   // Core System::update (used only to find the image)
#define RVA_getSwFormat   0x1999f70
#define RVA_createDSP     0x19970d0
#define RVA_addDSP        0x19a16e0
#define RVA_setActive     0x19a9be0
#define RVA_S_setListener 0x1a9ac60   // Studio System::setListenerAttributes
#define RVA_S_getEvent    0x1a97b20   // Studio System::getEvent
#define RVA_S_getLLS      0x1a98080   // Studio System::getLowLevelSystem
#define RVA_S_lookupID    0x1a99830   // Studio System::lookupID
#define RVA_S_getStrip    0x1a98300   // Studio System::getMixerStrip (bus)
#define RVA_S_stripCG     0x1a972f0   // Studio MixerStrip::getChannelGroup

static const unsigned char SIG_update[32] = {
    0x40,0x57,0x48,0x81,0xec,0x60,0x01,0x00,0x00,0x48,0xc7,0x44,0x24,0x48,0xfe,0xff,
    0xff,0xff,0x48,0x89,0x9c,0x24,0x78,0x01,0x00,0x00,0x48,0x8b,0x05,0xef,0xaf,0x4c};
// first bytes of the functions we hook / call, to be sure of the build
static const unsigned char CHK_setListener[18] = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x40,0x01,0x00,0x00};
static const unsigned char CHK_getEvent[15]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};
static const unsigned char CHK_getStrip[15]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};
static const unsigned char CHK_lookupID[13]    = {0x40,0x53,0x55,0x56,0x41,0x56,0x48,0x81,0xec,0x48,0x01,0x00,0x00};
static const unsigned char CHK_stripCG[18]     = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x40,0x01,0x00,0x00};
static const unsigned char CHK_getLLS[18]      = {0x48,0x89,0x5c,0x24,0x18,0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x81,0xec,0x50,0x01,0x00,0x00};

typedef long long (*f_gen4)(void *, void *, void *, void *);
typedef int (*f_getSwFormat)(void *, int *, int *, int *);
typedef int (*f_createDSP)(void *, const void *, void **);
typedef int (*f_addDSP)(void *, int, void *, void **);
typedef int (*f_setActive)(void *, int);
typedef int (*f_getLLS)(void *, void **);
typedef int (*f_lookupID)(void *, const char *, void *);
typedef int (*f_getStrip)(void *, const void *, int, void **);
typedef int (*f_stripCG)(void *, void **);

static unsigned char *g_base;
static f_gen4 t_setListener, t_getEvent;
static f_getSwFormat p_getSwFormat; static f_createDSP p_createDSP; static f_addDSP p_addDSP;
static f_setActive p_setActive; static f_getLLS p_getLLS; static f_lookupID p_lookupID;
static f_getStrip p_getStrip; static f_stripCG p_stripCG;

static Shared *S;
static void *g_studio, *g_core;
static void *g_dsp[NSLOT], *g_grp[NSLOT];
static int g_lasterr[NSLOT];
static ULONGLONG g_next;
static volatile LONG g_busy;
static char g_logpath[MAX_PATH];

static void tlog(const char *fmt, ...) {
    FILE *f = fopen(g_logpath, "a");
    if (!f) return;
    va_list a; va_start(a, fmt); vfprintf(f, fmt, a); va_end(a);
    fputc('\n', f); fclose(f);
}

// ---- FMOD 1.03 DSP description (plugin SDK 105, 192 bytes) ----
typedef int (*dsp_cb)(void *);
typedef int (*dsp_read)(void *, float *, float *, unsigned, int, int *);
typedef struct {
    unsigned pluginsdkversion; char name[32]; unsigned version;
    int numinputbuffers, numoutputbuffers;
    dsp_cb create, release, reset; dsp_read read;
    void *process, *setposition;
    int numparameters; int _pad; void *paramdesc;
    void *setf, *seti, *setb, *setd, *getf, *geti, *getb, *getd;
    void *shouldiprocess, *userdata;
} DSPDesc;

static int dsp_ok(void *st) { (void)st; return 0; }

static inline void capture(int slot, const float *in, float *out, unsigned len, int ch) {
    memcpy(out, in, (size_t)len * ch * sizeof(float));      // pass-through
    if (!S) return;
    unsigned w = S->widx[slot];
    float *r = S->ring[slot];
    float k = ch > 0 ? 1.0f / ch : 1.0f;
    for (unsigned i = 0; i < len; i++) {
        float s = 0; const float *p = in + (size_t)i * ch;
        for (int c = 0; c < ch; c++) s += p[c];
        r[(w + i) & (RING - 1)] = s * k;
    }
    MemoryBarrier();
    S->widx[slot] = w + len;
}
#define MKREAD(n) static int rd##n(void *st, float *in, float *out, unsigned len, int inch, int *outch) \
    { (void)st; if (outch) *outch = inch; capture(n, in, out, len, inch); return 0; }
MKREAD(0) MKREAD(1) MKREAD(2) MKREAD(3) MKREAD(4) MKREAD(5) MKREAD(6) MKREAD(7)
static dsp_read READS[NSLOT] = {rd0, rd1, rd2, rd3, rd4, rd5, rd6, rd7};
static DSPDesc g_desc[NSLOT];

static void studio_tick(void *studio) {
    if (!S || !studio) return;
    S->heartbeat++;
    ULONGLONG now = GetTickCount64();
    if (now < g_next) return;
    if (InterlockedCompareExchange(&g_busy, 1, 0) != 0) return;
    g_next = now + 1000;

    if (g_studio != studio) {
        g_studio = studio;
        S->state = 3;
        int r = p_getLLS(studio, &g_core);
        tlog("FMOD Studio system %p, core %p (%d)", studio, g_core, r);
        if (r == 0 && g_core) {
            int rate = 0, sm = 0, raw = 0;
            p_getSwFormat(g_core, &rate, &sm, &raw);
            S->rate = rate ? (unsigned)rate : 48000;
            tlog("mixer %d Hz", rate);
            for (int s = 0; s < NSLOT; s++) {
                if (g_dsp[s]) continue;
                DSPDesc *d = &g_desc[s];
                memset(d, 0, sizeof(*d));
                d->pluginsdkversion = 105;
                snprintf(d->name, 32, "FH2Tap%d", s);
                d->version = 0x10000;
                d->numinputbuffers = 1; d->numoutputbuffers = 1;
                d->create = dsp_ok; d->release = dsp_ok; d->reset = dsp_ok;
                d->read = READS[s];
                int e = p_createDSP(g_core, d, &g_dsp[s]);
                if (e) { tlog("createDSP %d failed (%d)", s, e); g_dsp[s] = 0; }
            }
        }
    }
    int all = 1;
    for (int s = 0; s < NSLOT; s++) {
        if (g_grp[s] || !g_dsp[s]) continue;
        all = 0;
        unsigned char guid[16] = {0};
        void *strip = 0, *cg = 0, *conn = 0;
        int e = p_lookupID(studio, SLOT_PATHS[s], guid);
        if (!e) e = p_getStrip(studio, guid, 0, &strip);
        if (!e && strip) e = p_stripCG(strip, &cg);
        if (!e && cg) {
            e = p_addDSP(cg, 0, g_dsp[s], &conn);
            if (!e) {
                p_setActive(g_dsp[s], 1);
                g_grp[s] = cg;
                S->attached |= 1u << s;
                tlog("tap on: %s (%s)", SLOT_NAMES[s], SLOT_PATHS[s]);
                continue;
            }
        }
        if (e != g_lasterr[s]) { g_lasterr[s] = e; tlog("%s not ready yet (error %d)", SLOT_PATHS[s], e); }
    }
    if (all) g_next = now + 30000;
    InterlockedExchange(&g_busy, 0);
}

static long long hk_setListener(void *a, void *b, void *c, void *d) { studio_tick(a); return t_setListener(a, b, c, d); }
static long long hk_getEvent(void *a, void *b, void *c, void *d)    { studio_tick(a); return t_getEvent(a, b, c, d); }

static unsigned char *find_update(void) {
    SYSTEM_INFO si; GetSystemInfo(&si);
    unsigned char *a = (unsigned char *)si.lpMinimumApplicationAddress;
    MEMORY_BASIC_INFORMATION mb;
    while (a < (unsigned char *)si.lpMaximumApplicationAddress && VirtualQuery(a, &mb, sizeof mb)) {
        DWORD x = mb.Protect & 0xff;
        if (mb.State == MEM_COMMIT && !(mb.Protect & PAGE_GUARD) &&
            (x == PAGE_EXECUTE_READ || x == PAGE_EXECUTE_READWRITE || x == PAGE_EXECUTE_WRITECOPY)) {
            unsigned char *p = (unsigned char *)mb.BaseAddress, *e = p + mb.RegionSize - 32;
            for (; p < e; p++) {
                p = memchr(p, 0x40, (size_t)(e - p));
                if (!p) break;
                if (p[1] == 0x57 && memcmp(p, SIG_update, 32) == 0) return p;
            }
        }
        a = (unsigned char *)mb.BaseAddress + mb.RegionSize;
    }
    return 0;
}

// detour fn at target: copy `stolen` whole instructions to a trampoline
static void *detour(unsigned char *target, int stolen, void *hook) {
    unsigned char *t = VirtualAlloc(0, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!t) return 0;
    memcpy(t, target, stolen);
    t[stolen] = 0xFF; t[stolen + 1] = 0x25; *(DWORD *)(t + stolen + 2) = 0;
    *(void **)(t + stolen + 6) = target + stolen;
    FlushInstructionCache(GetCurrentProcess(), t, 64);
    unsigned char patch[32];
    patch[0] = 0xFF; patch[1] = 0x25; *(DWORD *)(patch + 2) = 0; *(void **)(patch + 6) = hook;
    memset(patch + 14, 0x90, stolen - 14);
    DWORD old;
    if (!VirtualProtect(target, stolen, PAGE_EXECUTE_READWRITE, &old)) return 0;
    memcpy(target, patch, stolen);
    VirtualProtect(target, stolen, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, stolen);
    return t;
}

static DWORD WINAPI worker(LPVOID arg) {
    (void)arg;
    HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, 0, PAGE_READWRITE, 0, sizeof(Shared), "FH2HapticsTap");
    if (!h) { tlog("shared memory failed (%lu)", GetLastError()); return 0; }
    S = (Shared *)MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared));
    if (!S) return 0;
    S->version = 2; S->nslot = NSLOT; S->rate = 48000; S->attached = 0;
    for (int s = 0; s < NSLOT; s++) { memset(S->names[s], 0, 32); strncpy(S->names[s], SLOT_NAMES[s], 31); }
    S->magic = MAGIC;

    unsigned char *u = 0;
    for (int tries = 0; tries < 120 && !u; tries++) { u = find_update(); if (!u) Sleep(1000); }
    if (!u) { tlog("FMOD not found in this process (game build not 1.0.0.64?)"); return 0; }
    g_base = u - RVA_update;
    unsigned char *sl = g_base + RVA_S_setListener, *ge = g_base + RVA_S_getEvent;
    if (memcmp(sl, CHK_setListener, 18) || memcmp(ge, CHK_getEvent, 15) ||
        memcmp(g_base + RVA_S_getStrip, CHK_getStrip, 15) || memcmp(g_base + RVA_S_lookupID, CHK_lookupID, 13) ||
        memcmp(g_base + RVA_S_stripCG, CHK_stripCG, 18) || memcmp(g_base + RVA_S_getLLS, CHK_getLLS, 18)) {
        tlog("FMOD Studio layout mismatch (or already hooked), not hooking"); return 0;
    }
    S->state = 1;
    tlog("game image at %p", g_base);
    p_getSwFormat = (f_getSwFormat)(g_base + RVA_getSwFormat);
    p_createDSP = (f_createDSP)(g_base + RVA_createDSP);
    p_addDSP = (f_addDSP)(g_base + RVA_addDSP);
    p_setActive = (f_setActive)(g_base + RVA_setActive);
    p_getLLS = (f_getLLS)(g_base + RVA_S_getLLS);
    p_lookupID = (f_lookupID)(g_base + RVA_S_lookupID);
    p_getStrip = (f_getStrip)(g_base + RVA_S_getStrip);
    p_stripCG = (f_stripCG)(g_base + RVA_S_stripCG);

    t_setListener = (f_gen4)detour(sl, 18, (void *)hk_setListener);
    t_getEvent = (f_gen4)detour(ge, 15, (void *)hk_getEvent);
    if (!t_setListener || !t_getEvent) { tlog("hook failed"); return 0; }
    S->state = 2;
    tlog("hooked FMOD Studio (setListenerAttributes, getEvent)");
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hi, DWORD why, LPVOID r) {
    (void)r;
    if (why == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hi);
        GetModuleFileNameA(hi, g_logpath, MAX_PATH);
        char *s = strrchr(g_logpath, '\\');
        if (s) strcpy(s + 1, "fmodtap_log.txt"); else strcpy(g_logpath, "fmodtap_log.txt");
        DeleteFileA(g_logpath);
        tlog("FH2 FMOD tap v4.1 loaded");
        CreateThread(0, 0, worker, 0, 0, 0);
    }
    return TRUE;
}
