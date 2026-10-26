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
// Kanal sterujacy: nazwany potok \\.\pipe\m2autopot. Radar (64-bit, "mozg") czyta HP i gdy trzeba
// pic, pisze 1 bajt = indeks slotu (0..8). Potok przechodzi granice 32/64-bit trywialnie (to bajty).
//
// Budowa: build.bat (vcvars32 -> cl /LD /MT, 32-bit, pod GameCoreTop.exe/python27.dll).

#include <windows.h>
#include <stdio.h>
#include <stdint.h>

#define PIPE_NAME "\\\\.\\pipe\\m2autopot"

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

// Wywolywane przez GLOWNY watek gry (trzyma GIL). Tu dopiero wolno dotykac Pythona.
static int __cdecl pending_drink(void *arg)
{
    int slot = (int)(intptr_t)arg;
    char script[96];
    _snprintf(script, sizeof(script),
              "import player\nplayer.RequestUseLocalQuickSlot(%d)\n", slot);
    // Pending-call biegnie z GIL, ale Ensure/Release sa tu bezpieczne (rekurencyjne) i odporne
    // na ewentualne roznice wersji - wiec zostawiamy je obronnie.
    int st = p_GILEnsure ? p_GILEnsure() : 0;
    p_RunSimpleString(script);
    if (p_GILRelease) p_GILRelease(st);
    return 0;
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

static void queue_drink(int slot)
{
    // Kolejka pending-calls bywa pelna (maly bufor) - ponawiamy chwile.
    for (int t = 0; t < 50; ++t) {
        if (p_AddPendingCall(pending_drink, (void *)(intptr_t)slot) == 0)
            return;
        Sleep(2);
    }
    log_dbg("[autopot] kolejka pending-calls pelna - slot pominiety");
}

static DWORD WINAPI server_thread(LPVOID)
{
    // Jedna instancja na proces: drugie wstrzykniecie nie ma stawiac drugiego serwera.
    CreateMutexA(NULL, FALSE, "m2autopot_dll_once");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        log_dbg("[autopot] DLL juz aktywny - drugi watek serwera konczy");
        return 0;
    }

    // python27.dll jest ladowany wczesnie, ale czekamy spokojnie az bedzie gotowy (max ~60 s).
    for (int i = 0; i < 600 && !resolve_python(); ++i)
        Sleep(100);
    if (!p_AddPendingCall) {
        log_dbg("[autopot] python27.dll / Py_AddPendingCall nie znaleziony - koniec");
        return 0;
    }
    log_dbg("[autopot] gotowy, nasluchuje na " PIPE_NAME);

    for (;;) {
        HANDLE pipe = CreateNamedPipeA(
            PIPE_NAME, PIPE_ACCESS_INBOUND,
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
            unsigned char b;
            DWORD n;
            // Jeden klient (radar) moze trzymac polaczenie i slac wiele bajtow po kolei.
            while (ReadFile(pipe, &b, 1, &n, NULL) && n == 1) {
                if (b <= 8)
                    queue_drink((int)b);
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
