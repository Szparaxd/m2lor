// autopot.dll - cienki wykonawca auto-mikstury wstrzykiwany do GameCoreTop.exe (32-bit).
//
// Dlaczego w ogole DLL: gra czyta klawiature przez DirectInput (DISCL_FOREGROUND) raz na klatke -
// zadna symulacja klawiszy w tle nie zadziala bez fokusu. Jedyny sposob "uzyc slotu bez fokusu"
// to wykonac TEN SAM kod, ktory wykonuje slot paska, WEWNATRZ procesu gry. Klient ma wbudowanego
// Pythona (python27.dll), a slot 1..9 wywoluje player.RequestUseLocalQuickSlot(0..8)
// (zweryfikowane w Source Client: PythonPlayer.cpp -> SendItemUsePacket, bez klawiatury).
//
// Dlaczego Py_AddPendingCall: Pythona NIE wolno wolac z dowolnego watku (nasz watek serwera nie ma
// GIL i nie jest glownym watkiem gry - PyRun_SimpleString z niego = crash). Py_AddPendingCall jest
// bezpieczny do wywolania z dowolnego watku: kolejkuje funkcje, ktora GLOWNY watek gry wykona przy
// najblizszej okazji (co klatke), juz trzymajac GIL. Wtedy dopiero wolamy PyRun_SimpleString.
//
// Kanal sterujacy: nazwany potok \\.\pipe\m2autopot. Radar (64-bit, "mozg") pisze LINIE TEKSTU
// zakonczona '\n' = komenda Pythona, ktora DLL wykonuje w grze, np.:
//     player.RequestUseLocalQuickSlot(0)         (wypij slot 1 paska)
//     import player,net\nfor s in ...: ...        (skan ekwipunku i picie wprost - przyszlosc)
// Dzieki temu caly "rozum" moze zostac po stronie radaru, a DLL jest uniwersalnym wykonawca.
// Potok przechodzi granice 32/64-bit trywialnie (to bajty).
//
// Budowa: build.bat (vcvars32 -> cl /LD /MT, 32-bit, pod GameCoreTop.exe/python27.dll).

#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

// Log diagnostyczny do pliku (poziom C) - zeby widziec, czy komenda dociera, czy pending-call sie
// kolejkuje i czy callback sie odpala. Sciezka na sztywno (projekt lokalny).
#define DBG_PATH "C:\\Metin\\radar-project\\logs\\dll_debug.txt"
static void dbg(const char *fmt, ...)
{
    FILE *f = fopen(DBG_PATH, "a");
    if (!f)
        return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    fprintf(f, "[%02d:%02d:%02d.%03d pid=%lu] ", st.wHour, st.wMinute, st.wSecond,
            st.wMilliseconds, GetCurrentProcessId());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

// Pelna nazwa potoku = PIPE_PREFIX + PID procesu gry. PER-INSTANCJA: dwa klienty (GameCoreTop,
// vCore) maja osobne potoki, wiec radar trafia zawsze do wlasciwej gry.
#define PIPE_PREFIX "\\\\.\\pipe\\m2autopot_"

// --- wskazniki na funkcje python27.dll (cdecl) ---
typedef int  (__cdecl *PFN_AddPendingCall)(int (__cdecl *)(void *), void *);
typedef int  (__cdecl *PFN_RunSimpleString)(const char *);
typedef int  (__cdecl *PFN_GILEnsure)(void);     // PyGILState_STATE to enum = int
typedef void (__cdecl *PFN_GILRelease)(int);

static PFN_AddPendingCall  p_AddPendingCall  = NULL;
static PFN_RunSimpleString p_RunSimpleString = NULL;
static PFN_GILEnsure       p_GILEnsure       = NULL;
static PFN_GILRelease      p_GILRelease      = NULL;

static void log_dbg(const char *s) { OutputDebugStringA(s); }

// Wywolanie na GLOWNYM watku gry przez Py_AddPendingCall (bezpieczne). UWAGA: PyGILState_Ensure
// z obcego watku probowano - na vCore psulo stan Pythona i wywalalo klienta PO wykonaniu. Dlatego
// trzymamy sie pending-call: na GameCoreTop dziala (petla opruznia kolejke); na vCore callback
// po prostu nigdy sie nie wykonuje (petla nie opruznia), ale NIC nie psuje - radar pije wtedy
// klawiszem (z fokusem), a HP i tak czyta z pamieci.
static int __cdecl pending_run(void *arg)
{
    char *script = (char *)arg;
    dbg("pending_run ENTER: %.60s", script);
    int st = p_GILEnsure ? p_GILEnsure() : 0;
    int rc = p_RunSimpleString(script);
    if (p_GILRelease) p_GILRelease(st);
    dbg("pending_run DONE rc=%d", rc);
    free(script);
    return 0;
}

static void queue_script(const char *script)
{
    char *copy = _strdup(script);
    if (!copy)
        return;
    for (int t = 0; t < 50; ++t) {
        if (p_AddPendingCall(pending_run, copy) == 0) {
            dbg("Py_AddPendingCall OK (zakolejkowane)");
            return;
        }
        Sleep(2);
    }
    free(copy);
    dbg("Py_AddPendingCall: kolejka pelna");
}

static bool resolve_python(void)
{
    HMODULE py = GetModuleHandleA("python27.dll");
    if (!py)
        return false;
    p_AddPendingCall  = (PFN_AddPendingCall)  GetProcAddress(py, "Py_AddPendingCall");
    p_RunSimpleString = (PFN_RunSimpleString) GetProcAddress(py, "PyRun_SimpleString");
    p_GILEnsure       = (PFN_GILEnsure)       GetProcAddress(py, "PyGILState_Ensure");
    p_GILRelease      = (PFN_GILRelease)      GetProcAddress(py, "PyGILState_Release");
    return p_AddPendingCall && p_RunSimpleString;
}

// ======================= Focus-free drink na vCore (hook per-klatka) =======================
// vCore NIE opruznia kolejki Py_AddPendingCall, a Python z obcego watku go wywala. Rozwiazanie:
// detour na funkcji wywolywanej co klatke NA GLOWNYM WATKU (CPythonApplication::RunOneFrame),
// i w hooku - juz na glownym watku - wolamy NATYWNIE CPythonPlayer::RequestUseLocalQuickSlot.
// Bufor pakietow nie ma blokad, wiec wolno go ruszac TYLKO z glownego watku - stad hook, nie
// nasz watek serwera. Radar ustawia flage przez komende potoku "!drink N".
// RVA SPECYFICZNE DLA TEGO BUILDU vCore.exe (analiza binarki); prolog weryfikowany przed patchem.
// Cel = app.Process inner (0xB38C0), raz na klatke, glowny watek, BEZ skokow do prologu. NIE uzywac
// 0xB3750 (RunOneFrame) - ma wewnetrzny `jne 0xB3753` skaczacy w SRODEK 5-bajtowego patcha -> crash.
#define VCORE_HOOK_RVA             0xB38C0
#define VCORE_HOOK_RESUME_RVA      0xB38C5
#define VCORE_RULQS_RVA            0x80FA0        // RequestUseLocalQuickSlot (__thiscall, ret 4)
#define VCORE_PLAYER_SINGLETON_RVA 0x1B8C664      // *(void**) = CPythonPlayer*
static const unsigned char VCORE_PROLOGUE[5] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};

typedef void(__thiscall *PFN_RULQS)(void *self, unsigned idx);

static unsigned char *g_base = NULL;
static volatile LONG g_want_drink = 0;
static int g_slot = 0;
static void *g_tramp = NULL;

// Wywolywane CO KLATKE z glownego watku (z wnetrza hooka). Gdy radar poprosil - pijemy natywnie.
static __declspec(noinline) void on_frame(void)
{
    if (InterlockedExchange(&g_want_drink, 0)) {
        void *self = *(void **)(g_base + VCORE_PLAYER_SINGLETON_RVA);
        if (self)
            ((PFN_RULQS)(g_base + VCORE_RULQS_RVA))(self, (unsigned)g_slot);
    }
}

// Detour-stub: zachowuje rejestry, wola on_frame, odtwarza rejestry i skacze do trampoliny
// (skradzione bajty + powrot do RunOneFrame+5).
static __declspec(naked) void hook_stub(void)
{
    __asm {
        pushad
        pushfd
        call on_frame
        popfd
        popad
        mov eax, g_tramp      ; eax = adres trampoliny (eax jest scratch na wejsciu funkcji)
        jmp eax
    }
}

static bool is_vcore(void)
{
    char path[MAX_PATH];
    if (!GetModuleFileNameA(NULL, path, MAX_PATH))
        return false;
    char *b = strrchr(path, '\\');
    b = b ? b + 1 : path;
    return _stricmp(b, "vCore.exe") == 0;
}

static void install_vcore_hook(void)
{
    g_base = (unsigned char *)GetModuleHandleA(NULL);     // baza glownego exe (vCore.exe)
    unsigned char *target = g_base + VCORE_HOOK_RVA;
    if (memcmp(target, VCORE_PROLOGUE, 5) != 0) {
        dbg("hook: prolog @0x%X nie pasuje (inny build vCore?) - rezygnuje", VCORE_HOOK_RVA);
        return;
    }
    g_tramp = VirtualAlloc(NULL, 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_tramp) {
        dbg("hook: VirtualAlloc trampoliny nieudany");
        return;
    }
    unsigned char *t = (unsigned char *)g_tramp;
    memcpy(t, target, 5);                                  // skradzione: 56 8B F1 8B CE
    t[5] = 0x68;                                           // push imm32
    *(DWORD *)(t + 6) = (DWORD)(g_base + VCORE_HOOK_RESUME_RVA);
    t[10] = 0xC3;                                          // ret -> RunOneFrame+5

    DWORD old;
    if (!VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old)) {
        dbg("hook: VirtualProtect nieudany");
        return;
    }
    target[0] = 0xE9;                                      // jmp rel32 -> hook_stub
    *(DWORD *)(target + 1) = (DWORD)((unsigned char *)hook_stub - (target + 5));
    VirtualProtect(target, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    dbg("hook: ZAINSTALOWANY na RunOneFrame (base+0x%X), tramp=%p", VCORE_HOOK_RVA, g_tramp);

    char mk[160];
    _snprintf(mk, sizeof(mk), "C:\\Metin\\radar-project\\logs\\hook_%lu.ok", GetCurrentProcessId());
    FILE *f = fopen(mk, "w");
    if (f) {
        fputs("ok", f);
        fclose(f);
    }
}
// ===========================================================================================

static DWORD WINAPI server_thread(LPVOID)
{
    // Nazwa potoku i mutexu zawieraja PID tego procesu gry (per-instancja).
    DWORD pid = GetCurrentProcessId();
    char pipe_name[64], mutex_name[64];
    _snprintf(pipe_name, sizeof(pipe_name), "%s%lu", PIPE_PREFIX, pid);
    _snprintf(mutex_name, sizeof(mutex_name), "m2autopot_dll_%lu", pid);

    // Jedna instancja na proces: drugie wstrzykniecie do TEGO procesu nie stawia drugiego serwera.
    CreateMutexA(NULL, FALSE, mutex_name);
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        log_dbg("[autopot] DLL juz aktywny w tym procesie - drugi watek serwera konczy");
        return 0;
    }

    dbg("server start, pipe=%s", pipe_name);
    // vCore: zainstaluj hook per-klatka (picie bez fokusu). Inne buildy: picie przez pending-call.
    if (is_vcore())
        install_vcore_hook();
    // python27.dll jest ladowany wczesnie, ale czekamy spokojnie az bedzie gotowy (max ~60 s).
    for (int i = 0; i < 600 && !resolve_python(); ++i)
        Sleep(100);
    if (!p_AddPendingCall || !p_RunSimpleString) {
        dbg("python27 API (AddPendingCall/RunSimpleString) NIE znalezione - koniec");
        log_dbg("[autopot] python27 API nie znalezione - koniec");
        return 0;
    }
    dbg("python OK: AddPendingCall=%p RunSimpleString=%p",
        (void *)p_AddPendingCall, (void *)p_RunSimpleString);
    log_dbg("[autopot] gotowy, nasluchuje na potoku per-PID");

    for (;;) {
        HANDLE pipe = CreateNamedPipeA(
            pipe_name, PIPE_ACCESS_INBOUND,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES, 0, 64, 0, NULL);
        if (pipe == INVALID_HANDLE_VALUE) {
            Sleep(500);
            continue;
        }
        BOOL ok = ConnectNamedPipe(pipe, NULL)
                      ? TRUE
                      : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (ok) {
            // Komenda = skrypt UTF-8 zakonczony bajtem '\0' (NUL). NIE dzielimy po '\n', bo skrypt
            // bywa wieloliniowy (petla po ekwipunku) - cala komenda idzie w jeden PyRun_SimpleString.
            char cmd[4096];
            size_t len = 0;
            unsigned char b;
            DWORD n;
            while (ReadFile(pipe, &b, 1, &n, NULL) && n == 1) {
                if (b == 0) {
                    cmd[len] = 0;
                    if (len) {
                        dbg("komenda odebrana (len=%u): %.60s", (unsigned)len, cmd);
                        if (cmd[0] == '!') {
                            // komendy natywne (nie-Python). "!drink N" = ustaw flage dla hooka.
                            if (strncmp(cmd, "!drink", 6) == 0) {
                                g_slot = atoi(cmd + 6);
                                InterlockedExchange(&g_want_drink, 1);
                                dbg("native drink: slot=%d (flaga dla hooka)", g_slot);
                            }
                        } else {
                            queue_script(cmd);          // Python (dziala tam, gdzie petla opruznia kolejke)
                        }
                    }
                    len = 0;
                } else if (len < sizeof(cmd) - 1) {
                    cmd[len++] = (char)b;
                } else {
                    len = 0;                 // komenda za dluga - odrzucamy do nastepnego '\0'
                }
            }
        }
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }
    // nieosiagalne
}

BOOL WINAPI DllMain(HINSTANCE hInst, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hInst);
        // W DllMain nie wolno robic nic ciezkiego (loader lock) - od razu oddajemy prace watkowi.
        HANDLE t = CreateThread(NULL, 0, server_thread, NULL, 0, NULL);
        if (t)
            CloseHandle(t);
    }
    return TRUE;
}
