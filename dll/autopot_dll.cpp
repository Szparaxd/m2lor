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
// RVA SPECYFICZNE DLA BIEZACEGO buildu vCore.exe (2026-10-27 - user przekompilowal, WSZYSTKIE stare
// wartosci sie przesunely). Zweryfikowane NA ZYWO (prologi w dzialajacym procesie) + statycznie.
// Hook = CPythonApplication::Process (0xB3A90): petla glowna (PeekMessage) wola go CO KLATKE na glownym
// watku; prolog 55 8B EC 6A FF (push ebp;mov ebp,esp;push -1) = 5 bajtow, BEZ skokow w patch. NIE uzywac
// 0xB3750/RunOneFrame (jne w patch -> crash) ani starego 0xB38C0 (po rekompilacji to juz inna funkcja).
// 2026-10-28: launcher znow podmienil vCore.exe - Process przesunal sie na 0xB3B60 (reszta RVA bez zmian).
// Dlatego hook i singleton/PickCloseItem sa teraz SZUKANE W PAMIECI (resolve_vcore_rvas) - stale ponizej to
// tylko fallback, gdy sygnatura nie trafi.
#define VCORE_HOOK_RVA             0xB3B60
#define VCORE_RULQS_RVA            0x81190        // RequestUseLocalQuickSlot (__thiscall, 1 arg, ret 4)
#define VCORE_PICK_RVA             0x83750        // CPythonPlayer::PickCloseItem (__thiscall, 0 arg, ret)
#define VCORE_CLICKSKILL_RVA       0x8AD40        // CPythonPlayer::ClickSkillSlot (__thiscall, 1 arg = slot, ret 4)
#define VCORE_MOVETO_RVA           0x325F0        // CInstanceBase::NEW_MoveToDestPixelPositionDirection (thiscall, arg TPixelPosition*)
#define VCORE_GETPOS_RVA           0x32BA0        // CInstanceBase::NEW_GetPixelPosition (thiscall, arg float[3] out)
#define VCORE_GETMAIN_VIDX         0x88           // bajtowy indeks wirtualnej GetMainActorPtr w vtable [player+4]
#define VCORE_PLAYER_SINGLETON_RVA 0x1B8D674      // *(void**) = CPythonPlayer* (bylo 0x1B8C664)
static const unsigned char VCORE_PROLOGUE[5] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};

// RVA uzywane w czasie dzialania (vCore) - start = stale, nadpisywane przez resolve_vcore_rvas().
static uintptr_t g_hook_rva = VCORE_HOOK_RVA;
static uintptr_t g_pick_rva = VCORE_PICK_RVA;
static uintptr_t g_sing_rva = VCORE_PLAYER_SINGLETON_RVA;
static uintptr_t g_skill_rva = VCORE_CLICKSKILL_RVA;
// Komendy czatu (/unmount, /ride): CPythonNetworkStream::SendChatPacket(this, const char *, BYTE type),
// __thiscall. vCore (2026-10-28): fn RVA 0xD6CC0, this = *(base+0x1B8DE90) - z wrappera net.SendChatPacket;
// DLL szuka ich sygnatura (resolve_vcore_chat), stale = fallback.
#define VCORE_CHAT_RVA             0xD6CC0
#define VCORE_NETSTREAM_RVA        0x1B8DE90
static uintptr_t g_chat_rva = VCORE_CHAT_RVA;
static uintptr_t g_net_rva = VCORE_NETSTREAM_RVA;
typedef void(__thiscall *PFN_SENDCHAT)(void *self, const char *text, unsigned char type);
static volatile LONG g_want_chat = 0;
static char g_chat_text[128];                    // tekst komendy czatu dla hooka (np. "/unmount")

// GameCoreTop.exe (INNY build; ImageBase 0x400000). Ruch natywny NIE przez hook (go nie ma), lecz przez
// Py_AddPendingCall (pending-calls sa tu opruzniane). Adresy: analiza binarki od eksportu player.PickCloseItem.
// getMain idx 0x88 i konwencja y=-worldY - takie same jak vCore.
#define GC_PLAYER_SINGLETON_RVA    0x48BDF8        // *(void**) = CPythonPlayer*
#define GC_MOVETO_RVA              0xA6A40         // NEW_MoveToDestPixelPositionDirection (ret 4) - NIE 0xA6A00!
#define GC_GETPOS_RVA              0xA70D0         // NEW_GetPixelPosition (ret 4)
#define GC_GETMAIN_VIDX            0x88

// PODMIANA POZYCJI do dalekiego lootu (GameCoreTop, 2026-10-28). Serwer liczy zasieg podnoszenia (~300) od
// SWOJEJ pozycji postaci, a te ustawia z pakietu ruchu (HEADER_CG_CHARACTER_MOVE=7). "!tpick x y vid..." w
// JEDNEJ klatce glownego watku wysyla: ruch FUNC_WAIT na (x,y) -> pakiety podniesienia -> ruch FUNC_WAIT z
// powrotem na biezaca pozycje. Lokalna postac w kliencie sie nie rusza. Adresy (pefile+capstone):
// SendCharacterStatePacket - funkcja z xrefem napisu "CPythonNetworkStream::SendCharacterStatePacket(dwCmdTime",
// thiscall(this, TPixelPosition*, float rot, UINT func, UINT arg), ret 0x10; SendItemPickUpPacket - z wrappera
// net.SendItemPickUpPacket (mov ecx,[0x48DFF8]; call). Pozycja w konwencji RADARU (= NEW_GetPixelPosition).
#define GC_NETSTREAM_RVA           0x48DFF8        // *(void**) = CPythonNetworkStream*
#define GC_SENDSTATE_RVA           0x1162C0
#define GC_SENDPICKUP_RVA          0x11A1A0
#define FUNC_WAIT_STATE            0
#define TPICK_MAX                  32
typedef bool(__thiscall *PFN_SENDSTATE)(void *self, const float *pos3, float rot, unsigned func, unsigned arg);
typedef bool(__thiscall *PFN_SENDPICKUP)(void *self, unsigned vid);
struct TPickArg { float x, y; int n; unsigned vids[TPICK_MAX]; };

// Masowy atak pakietem (GameCoreTop): CPythonNetworkStream::SendAttackPacket(UINT uMotAttack, DWORD dwVID)
// buduje TPacketCGAttack {HEADER_CG_ATTACK, bType, VID} i wysyla - BEZ lokalnej animacji/ruchu (omijamy
// sciezke wejscia OnHit). net NIE eksportuje jej do Pythona -> wolamy natywnie. uMotAttack=0 = cios zwykly.
// RVA namierzone po stringu "Send Battle Attack Packet Error" (Tracen w ciele): base+0x116160, __thiscall,
// ret 8. this = singleton netstream (GC_NETSTREAM_RVA, ten sam co SendState/SendPickUp). TYLKO glowny watek.
#define GC_SENDATTACK_RVA          0x116160
#define ATTACK_MAX                 64
typedef bool(__thiscall *PFN_SENDATTACK)(void *self, unsigned motAttack, unsigned vid);
struct TAttackArg { unsigned mot; int n; unsigned vids[ATTACK_MAX]; };

typedef void(__thiscall *PFN_RULQS)(void *self, unsigned idx);
typedef void(__thiscall *PFN_PICK)(void *self);     // PickCloseItem - bez argumentow
typedef void(__thiscall *PFN_CLICKSKILL)(void *self, unsigned slot);  // ClickSkillSlot - jak klik w okno umiejetnosci
typedef void *(__thiscall *PFN_GETMAIN)(void *self); // zwraca CInstanceBase* glownej postaci
typedef void (__thiscall *PFN_GETPOS)(void *self, float *out3);  // out3 = x, y(=-worldY), z
typedef void (__thiscall *PFN_MOVETO)(void *self, float *dst3);  // idz do punktu (konwencja jak GetPos)

static unsigned char *g_base = NULL;
static volatile LONG g_want_drink = 0;
static volatile LONG g_want_pick = 0;
static volatile LONG g_want_goto = 0;
static volatile LONG g_want_getpos = 0;
static volatile LONG g_want_skill = 0;
static int g_slot = 0;
static int g_skill_slot = 0;                     // slot UMIEJETNOSCI (indeks aSkill), nie paska
static float g_goto_x = 0.0f, g_goto_y = 0.0f;   // cel w konwencji RADARU (worldX, worldY)
static void *g_tramp = NULL;
static bool g_is_vcore = false;                  // ktory build -> zestaw RVA + droga wykonania
struct GotoArg { float x, y; };                  // ladunek dla natywnego pending-call (GameCoreTop)

// Wskaznik glownej postaci (CInstanceBase*) - jak PickCloseItem: singleton -> [player+4] ->
// vtable[0x88] (GetMainActorPtr), wolana z this=player+4. Zestaw RVA zalezy od buildu. NULL poza gra.
static void *get_main_char(void)
{
    uintptr_t sing = g_is_vcore ? g_sing_rva : GC_PLAYER_SINGLETON_RVA;
    char *pl = *(char **)(g_base + sing);
    if (!pl)
        return NULL;
    void **vt = *(void ***)(pl + 4);
    if (!vt)
        return NULL;
    PFN_GETMAIN gm = (PFN_GETMAIN)vt[GC_GETMAIN_VIDX / 4];   // idx 0x88 w obu buildach
    return gm(pl + 4);
}

// Idz do punktu. NEW_GetPixelPosition zwraca DOKLADNIE radarowe (worldX, worldY) - zweryfikowane na zywo
// (!getpos: x=72118 y=132080 == radar), wiec BEZ negacji: dst = {wx, wy, biezace z}. TYLKO glowny watek!
static void do_goto(float wx, float wy)
{
    void *self = get_main_char();
    if (!self)
        return;
    uintptr_t gp = g_is_vcore ? VCORE_GETPOS_RVA : GC_GETPOS_RVA;
    uintptr_t mv = g_is_vcore ? VCORE_MOVETO_RVA : GC_MOVETO_RVA;
    float cur[3] = {0, 0, 0};
    ((PFN_GETPOS)(g_base + gp))(self, cur);
    float dst[3] = {wx, wy, cur[2]};
    ((PFN_MOVETO)(g_base + mv))(self, dst);
}

// Kanarek: zaloguj biezaca pozycje glownej postaci (weryfikacja konwencji). TYLKO glowny watek!
static void do_getpos(void)
{
    void *self = get_main_char();
    if (!self) {
        dbg("getpos: brak glownej postaci (self=NULL)");
        return;
    }
    uintptr_t gp = g_is_vcore ? VCORE_GETPOS_RVA : GC_GETPOS_RVA;
    float cur[3] = {0, 0, 0};
    ((PFN_GETPOS)(g_base + gp))(self, cur);
    dbg("getpos: x=%.1f y=%.1f z=%.1f (= radar worldX, worldY)", cur[0], cur[1], cur[2]);
}

// Natywne pending-call (GameCoreTop: ruch na glownym watku bez hooka; cdecl wg Py_AddPendingCall).
static int __cdecl pending_goto(void *arg)
{
    struct GotoArg *g = (struct GotoArg *)arg;
    do_goto(g->x, g->y);
    free(g);
    return 0;
}
// Skok-podnies-powrot (patrz GC_SENDSTATE_RVA). TYLKO glowny watek (bufor pakietow bez blokad).
static int __cdecl pending_tpick(void *arg)
{
    struct TPickArg *t = (struct TPickArg *)arg;
    void *self = get_main_char();
    void *net = *(void **)(g_base + GC_NETSTREAM_RVA);
    if (self && net) {
        float cur[3] = {0, 0, 0};
        ((PFN_GETPOS)(g_base + GC_GETPOS_RVA))(self, cur);
        float dst[3] = {t->x, t->y, cur[2]};
        PFN_SENDSTATE st = (PFN_SENDSTATE)(g_base + GC_SENDSTATE_RVA);
        PFN_SENDPICKUP pk = (PFN_SENDPICKUP)(g_base + GC_SENDPICKUP_RVA);
        bool a = st(net, dst, 0.0f, FUNC_WAIT_STATE, 0);
        for (int i = 0; i < t->n; ++i)
            pk(net, t->vids[i]);
        bool b = st(net, cur, 0.0f, FUNC_WAIT_STATE, 0);
        dbg("tpick: (%.0f,%.0f) -> (%.0f,%.0f) vid x%d -> powrot; state %d/%d",
            cur[0], cur[1], t->x, t->y, t->n, (int)a, (int)b);
    } else {
        dbg("tpick: brak postaci/sieci (self=%p net=%p)", self, net);
    }
    free(t);
    return 0;
}

static int __cdecl pending_getpos(void *arg)
{
    (void)arg;
    do_getpos();
    return 0;
}

// Masowy atak: dla kazdego VID wyslij pakiet ataku. TYLKO glowny watek (bufor pakietow bez blokad).
// Serwer waliduje dystans/cooldown - my tylko proponujemy; radar decyduje tempo (throttle/tryb).
static int __cdecl pending_attack(void *arg)
{
    struct TAttackArg *a = (struct TAttackArg *)arg;
    void *net = *(void **)(g_base + GC_NETSTREAM_RVA);
    if (net) {
        PFN_SENDATTACK atk = (PFN_SENDATTACK)(g_base + GC_SENDATTACK_RVA);
        for (int i = 0; i < a->n; ++i)
            atk(net, a->mot, a->vids[i]);
        dbg("attack: mot=%u vid x%d", a->mot, a->n);
    } else {
        dbg("attack: brak sieci (net=NULL)");
    }
    free(a);
    return 0;
}

// Wywolywane CO KLATKE z glownego watku (z wnetrza hooka). Gdy radar poprosil - robimy natywnie.
static __declspec(noinline) void on_frame(void)
{
    if (InterlockedExchange(&g_want_drink, 0)) {
        void *self = *(void **)(g_base + g_sing_rva);
        if (self)
            ((PFN_RULQS)(g_base + VCORE_RULQS_RVA))(self, (unsigned)g_slot);
    }
    // auto-loot: natywne PickCloseItem (podnies najblizszy drop) - tez TYLKO z glownego watku
    // (SendClickItemPacket rusza bufor pakietow). Radar prosi komenda potoku "!pick".
    if (InterlockedExchange(&g_want_pick, 0)) {
        void *self = *(void **)(g_base + g_sing_rva);
        if (self)
            ((PFN_PICK)(g_base + g_pick_rva))(self);
    }
    // umiejetnosc: ClickSkillSlot = ta sama sciezka co klik w okno umiejetnosci (animacja, lokalny
    // cooldown, pakiet). Radar sam sprawdza kon/cooldown/aktywny buff - tu tylko wykonanie. "!skill N".
    if (InterlockedExchange(&g_want_skill, 0)) {
        void *self = *(void **)(g_base + g_sing_rva);
        if (self)
            ((PFN_CLICKSKILL)(g_base + g_skill_rva))(self, (unsigned)g_skill_slot);
    }
    // komenda czatu (zsiadanie/wsiadanie z konia: "/unmount", "/ride") - pakiet czatu z glownego watku.
    if (InterlockedExchange(&g_want_chat, 0)) {
        void *net = *(void **)(g_base + g_net_rva);
        if (net)
            ((PFN_SENDCHAT)(g_base + g_chat_rva))(net, g_chat_text, 0);   // 0 = CHAT_TYPE_TALKING
    }
    // kanarek (!getpos) i ruch (!goto) - wspolne helpery (vCore: stad z hooka; GameCoreTop: z pending-call)
    if (InterlockedExchange(&g_want_getpos, 0))
        do_getpos();
    if (InterlockedExchange(&g_want_goto, 0))
        do_goto(g_goto_x, g_goto_y);
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

// --- wyszukiwanie RVA po sygnaturach (odporne na przekompilowanie/podmiane exe przez launcher) ---
static bool sec_range(const char *name, unsigned char **lo, unsigned char **hi)
{
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)g_base;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(g_base + dos->e_lfanew);
    IMAGE_SECTION_HEADER *sh = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sh) {
        if (strncmp((const char *)sh->Name, name, 8) == 0) {
            *lo = g_base + sh->VirtualAddress;
            *hi = *lo + sh->Misc.VirtualSize;
            return true;
        }
    }
    return false;
}

// Wzorzec z maska ('?' = dowolny bajt). Zwraca JEDYNE trafienie; brak albo wiecej niz 1 -> NULL.
static unsigned char *find_unique(unsigned char *lo, unsigned char *hi, const unsigned char *pat,
                                  const char *mask, size_t n)
{
    unsigned char *hit = NULL;
    for (unsigned char *p = lo; p + n <= hi; ++p) {
        size_t k = 0;
        while (k < n && (mask[k] == '?' || p[k] == pat[k]))
            ++k;
        if (k == n) {
            if (hit)
                return NULL;                       // niejednoznaczne - nie ufamy
            hit = p;
        }
    }
    return hit;
}

static void resolve_vcore_rvas(void)
{
    unsigned char *tlo, *thi, *rlo, *rhi, *dlo, *dhi;
    if (!sec_range(".text", &tlo, &thi) || !sec_range(".rdata", &rlo, &rhi) || !sec_range(".data", &dlo, &dhi)) {
        dbg("sig: brak sekcji - zostaja stale");
        return;
    }
    // 1) Petla glowna: while(1){ if(IsMessage()){ if(!MessageProcess()) break; }
    //    else { if(!Process()) break; ... } }  ->  ostatnie E8 = CPythonApplication::Process (co klatke).
    //    56 8B F1 | 8B CE E8 ?? | 8B CE 84 C0 74 ?? | E8 ?? 84 C0 75 ?? | 5E C3 | E8 <Process>
    static const unsigned char lp[] = {0x56, 0x8B, 0xF1, 0x8B, 0xCE, 0xE8, 0, 0, 0, 0, 0x8B, 0xCE, 0x84, 0xC0,
                                       0x74, 0, 0xE8, 0, 0, 0, 0, 0x84, 0xC0, 0x75, 0, 0x5E, 0xC3, 0xE8};
    static const char lm[] = "xxxxxx????xxxxx?x????xxx?xxx";
    unsigned char *m = find_unique(tlo, thi, lp, lm, sizeof(lp));
    if (m) {
        unsigned char *call = m + sizeof(lp) - 1;
        unsigned char *proc = call + 5 + *(int32_t *)(call + 1);
        if (proc >= tlo && proc + 5 <= thi && memcmp(proc, VCORE_PROLOGUE, 5) == 0) {
            g_hook_rva = (uintptr_t)(proc - g_base);
            dbg("sig: Process = base+0x%X (z petli glownej @0x%X)", (unsigned)g_hook_rva, (unsigned)(m - g_base));
        } else {
            dbg("sig: Process z petli ma inny prolog - zostaje 0x%X", (unsigned)g_hook_rva);
        }
    } else {
        dbg("sig: petla glowna nie znaleziona - zostaje 0x%X", (unsigned)g_hook_rva);
    }
    // 2) Tabela metod modulu player: PyMethodDef{"PickCloseItem", playerPickCloseItem, ...}. Wrapper:
    //    8B 0D <&singleton> E8 <PickCloseItem>  ->  oba RVA naraz.
    static const char nm[] = "PickCloseItem";
    for (unsigned char *p = rlo + 1; p + sizeof(nm) <= rhi; ++p) {
        if (p[-1] != 0 || memcmp(p, nm, sizeof(nm)) != 0)
            continue;
        for (unsigned char *q = dlo; q + 8 <= dhi; q += 4) {
            if (*(unsigned char **)q != p)
                continue;
            unsigned char *fn = *(unsigned char **)(q + 4);
            if (fn < tlo || fn + 11 > thi || fn[0] != 0x8B || fn[1] != 0x0D || fn[6] != 0xE8)
                continue;
            unsigned char *sing = *(unsigned char **)(fn + 2);
            unsigned char *pick = fn + 11 + *(int32_t *)(fn + 7);
            if (sing < dlo || sing >= dhi || pick < tlo || pick >= thi)
                continue;
            g_sing_rva = (uintptr_t)(sing - g_base);
            g_pick_rva = (uintptr_t)(pick - g_base);
            dbg("sig: singleton = base+0x%X, PickCloseItem = base+0x%X", (unsigned)g_sing_rva, (unsigned)g_pick_rva);
            goto skill;
        }
    }
    dbg("sig: PyMethodDef PickCloseItem nie znaleziony - zostaja stale");
skill:
    // 3) ClickSkillSlot: wrapper najpierw parsuje argument (PyTuple_GetInteger), potem
    //    push [ebp-8] | 8B 0D <&singleton> | E8 <ClickSkillSlot>. Szukamy w pierwszych 0x80 bajtach
    //    wrappera "8B 0D <singleton gracza> E8" - singleton z kroku 2 odroznia modul player od
    //    innych PyMethodDef o tej samej nazwie (sa co najmniej 3).
    unsigned char *want = g_base + g_sing_rva;
    static const char sk[] = "ClickSkillSlot";
    for (unsigned char *p = rlo + 1; p + sizeof(sk) <= rhi; ++p) {
        if (p[-1] != 0 || memcmp(p, sk, sizeof(sk)) != 0)
            continue;
        for (unsigned char *q = dlo; q + 8 <= dhi; q += 4) {
            if (*(unsigned char **)q != p)
                continue;
            unsigned char *fn = *(unsigned char **)(q + 4);
            if (fn < tlo || fn + 0x80 > thi)
                continue;
            for (unsigned char *c = fn; c < fn + 0x80 - 11; ++c) {
                if (c[0] != 0x8B || c[1] != 0x0D || *(unsigned char **)(c + 2) != want || c[6] != 0xE8)
                    continue;
                unsigned char *tgt = c + 11 + *(int32_t *)(c + 7);
                if (tgt < tlo || tgt >= thi)
                    continue;
                g_skill_rva = (uintptr_t)(tgt - g_base);
                dbg("sig: ClickSkillSlot = base+0x%X", (unsigned)g_skill_rva);
                return;
            }
        }
    }
    dbg("sig: ClickSkillSlot nie znaleziony - zostaje 0x%X", (unsigned)g_skill_rva);
}

// SendChatPacket: wrapper PyMethodDef "SendChatPacket" (modul net) po sparsowaniu argumentow robi
//   8B 0D <&CPythonNetworkStream singleton> | push typ | push tekst | E8 <SendChatPacket>.
// Szukamy w pierwszych 0x100 bajtach wrappera "8B 0D" z E8 w nastepnych 12 bajtach.
static void resolve_vcore_chat(void)
{
    unsigned char *tlo, *thi, *rlo, *rhi, *dlo, *dhi;
    if (!sec_range(".text", &tlo, &thi) || !sec_range(".rdata", &rlo, &rhi) || !sec_range(".data", &dlo, &dhi))
        return;
    static const char nm[] = "SendChatPacket";
    for (unsigned char *p = rlo + 1; p + sizeof(nm) <= rhi; ++p) {
        if (p[-1] != 0 || memcmp(p, nm, sizeof(nm)) != 0)
            continue;
        for (unsigned char *q = dlo; q + 8 <= dhi; q += 4) {
            if (*(unsigned char **)q != p)
                continue;
            unsigned char *fn = *(unsigned char **)(q + 4);
            if (fn < tlo || fn + 0x100 > thi)
                continue;
            for (unsigned char *c = fn; c < fn + 0x100 - 18; ++c) {
                if (c[0] != 0x8B || c[1] != 0x0D)
                    continue;
                unsigned char *net = *(unsigned char **)(c + 2);
                if (net < dlo || net >= dhi)
                    continue;
                for (unsigned char *e = c + 6; e < c + 18; ++e) {
                    if (*e != 0xE8)
                        continue;
                    unsigned char *tgt = e + 5 + *(int32_t *)(e + 1);
                    if (tgt < tlo || tgt >= thi)
                        continue;
                    g_net_rva = (uintptr_t)(net - g_base);
                    g_chat_rva = (uintptr_t)(tgt - g_base);
                    dbg("sig: SendChatPacket = base+0x%X, NetworkStream = base+0x%X", (unsigned)g_chat_rva,
                        (unsigned)g_net_rva);
                    return;
                }
            }
        }
    }
    dbg("sig: SendChatPacket nie znaleziony - zostaje 0x%X", (unsigned)g_chat_rva);
}

static void install_vcore_hook(void)
{
    g_base = (unsigned char *)GetModuleHandleA(NULL);     // baza glownego exe (vCore.exe)
    resolve_vcore_rvas();
    resolve_vcore_chat();
    unsigned char *target = g_base + g_hook_rva;
    if (memcmp(target, VCORE_PROLOGUE, 5) != 0) {
        dbg("hook: prolog @0x%X nie pasuje (inny build vCore?) - rezygnuje", (unsigned)g_hook_rva);
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
    *(DWORD *)(t + 6) = (DWORD)(target + 5);              // powrot za skradzione bajty
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
    dbg("hook: ZAINSTALOWANY na Process (base+0x%X), tramp=%p", (unsigned)g_hook_rva, g_tramp);

    char mk[160];
    _snprintf(mk, sizeof(mk), "C:\\Metin\\radar-project\\logs\\hook_%lu.ok", GetCurrentProcessId());
    FILE *f = fopen(mk, "w");
    if (f) {
        fputs("ok skill chat", f);     // "skill"/"chat" = ta DLL zna komendy !skill / !chat (radar sprawdza)
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

    g_base = (unsigned char *)GetModuleHandleA(NULL);   // baza exe gry (ruch natywny uzywa jej na obu buildach)
    g_is_vcore = is_vcore();
    dbg("server start, pipe=%s (vcore=%d)", pipe_name, (int)g_is_vcore);
    // vCore: hook per-klatka (natywne wywolania bez fokusu). GameCoreTop: brak hooka - ruch/getpos ida
    // przez Py_AddPendingCall (pending-calls sa tu opruzniane), picie/loot przez Pythona.
    if (g_is_vcore) {
        install_vcore_hook();
    } else {
        // GameCoreTop: znacznik mozliwosci tej DLL (radar sprawdza, czy wstrzyknieta wersja zna "!tpick")
        char mk[MAX_PATH];
        _snprintf(mk, sizeof(mk), "C:\\Metin\\radar-project\\logs\\caps_%lu.ok", GetCurrentProcessId());
        FILE *f = fopen(mk, "w");
        if (f) {
            fputs("tpick attack", f);
            fclose(f);
        }
    }
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
                            } else if (strncmp(cmd, "!chat ", 6) == 0) {
                                // "!chat /unmount" - tekst kopiowany dla hooka (gdy poprzedni jeszcze nie
                                // wyslany, nadpisujemy - liczy sie ostatnia komenda)
                                strncpy(g_chat_text, cmd + 6, sizeof(g_chat_text) - 1);
                                g_chat_text[sizeof(g_chat_text) - 1] = 0;
                                InterlockedExchange(&g_want_chat, 1);
                                dbg("native chat: '%s' (flaga dla hooka)", g_chat_text);
                            } else if (strncmp(cmd, "!skill", 6) == 0) {
                                g_skill_slot = atoi(cmd + 6);
                                InterlockedExchange(&g_want_skill, 1);
                                dbg("native skill: slot=%d (flaga dla hooka)", g_skill_slot);
                            } else if (strncmp(cmd, "!pick", 5) == 0) {
                                InterlockedExchange(&g_want_pick, 1);
                                dbg("native pick (flaga dla hooka)");
                            } else if (strncmp(cmd, "!getpos", 7) == 0) {
                                if (g_is_vcore)
                                    InterlockedExchange(&g_want_getpos, 1);     // vCore: wykona hook on_frame
                                else if (p_AddPendingCall)
                                    p_AddPendingCall(pending_getpos, NULL);     // GameCoreTop: pending-call
                                dbg("native getpos (vcore=%d)", (int)g_is_vcore);
                            } else if (strncmp(cmd, "!goto", 5) == 0) {
                                float gx = 0.0f, gy = 0.0f;
                                sscanf(cmd + 5, "%f %f", &gx, &gy);
                                if (g_is_vcore) {
                                    g_goto_x = gx;
                                    g_goto_y = gy;
                                    InterlockedExchange(&g_want_goto, 1);       // vCore: hook on_frame
                                } else if (p_AddPendingCall) {
                                    struct GotoArg *a = (struct GotoArg *)malloc(sizeof(struct GotoArg));
                                    if (a) {
                                        a->x = gx;
                                        a->y = gy;
                                        p_AddPendingCall(pending_goto, a);      // GameCoreTop: pending-call
                                    }
                                }
                                dbg("native goto: (%.1f, %.1f) vcore=%d", gx, gy, (int)g_is_vcore);
                            } else if (strncmp(cmd, "!tpick ", 7) == 0) {
                                // "!tpick x y vid vid ..." - tylko GameCoreTop (RVA vCore nieznane)
                                struct TPickArg *t = (struct TPickArg *)calloc(1, sizeof(struct TPickArg));
                                char *p = cmd + 7, *e;
                                if (t && !g_is_vcore && p_AddPendingCall) {
                                    t->x = (float)strtod(p, &e); p = e;
                                    t->y = (float)strtod(p, &e); p = e;
                                    while (t->n < TPICK_MAX) {
                                        unsigned long v = strtoul(p, &e, 10);
                                        if (e == p)
                                            break;
                                        t->vids[t->n++] = (unsigned)v;
                                        p = e;
                                    }
                                    p_AddPendingCall(pending_tpick, t);
                                    dbg("native tpick: (%.1f, %.1f) vid x%d", t->x, t->y, t->n);
                                } else {
                                    free(t);
                                    dbg("tpick: nieobslugiwane (vcore=%d)", (int)g_is_vcore);
                                }
                            } else if (strncmp(cmd, "!attack ", 8) == 0) {
                                // "!attack <mot> <vid vid ...>" - masowy atak pakietem (GameCoreTop).
                                // mot=0 cios zwykly. Natywny pending-call na glownym watku.
                                struct TAttackArg *a = (struct TAttackArg *)calloc(1, sizeof(struct TAttackArg));
                                char *p = cmd + 8, *e;
                                if (a && !g_is_vcore && p_AddPendingCall) {
                                    a->mot = (unsigned)strtoul(p, &e, 10); p = e;
                                    while (a->n < ATTACK_MAX) {
                                        unsigned long v = strtoul(p, &e, 10);
                                        if (e == p)
                                            break;
                                        a->vids[a->n++] = (unsigned)v;
                                        p = e;
                                    }
                                    p_AddPendingCall(pending_attack, a);
                                    dbg("native attack: mot=%u vid x%d", a->mot, a->n);
                                } else {
                                    free(a);
                                    dbg("attack: nieobslugiwane (vcore=%d)", (int)g_is_vcore);
                                }
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
