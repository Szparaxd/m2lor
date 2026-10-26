// inject.exe - prosty injector DLL (klasyka: VirtualAllocEx + WriteProcessMemory + CreateRemoteThread
// -> LoadLibraryA). 32-bit, bo wstrzykujemy do 32-bitowego GameCoreTop.exe: adres LoadLibraryA w
// kernel32 jest ten sam w naszym procesie i w celu TYLKO przy zgodnej bitowosci, a kod powrotu watku
// (HMODULE) mialby 64-bit obciecie przy niezgodnosci. Dlatego radar (64-bit) uruchamia ten exe.
//
// Uzycie:  inject.exe <proces.exe | pid> <sciezka\autopot.dll>
// Wymaga uprawnien administratora (jak odczyt pamieci gry).
//
// Budowa: build.bat (vcvars32 -> cl /MT, 32-bit).

#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>

static DWORD pid_by_name(const char *name)
{
    DWORD pid = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE)
        return 0;
    PROCESSENTRY32 pe;
    pe.dwSize = sizeof(pe);
    if (Process32First(snap, &pe)) {
        do {
            if (_stricmp(pe.szExeFile, name) == 0) {
                pid = pe.th32ProcessID;
                break;
            }
        } while (Process32Next(snap, &pe));
    }
    CloseHandle(snap);
    return pid;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        printf("Uzycie: inject.exe <proces.exe | pid> <sciezka\\autopot.dll>\n");
        return 1;
    }

    DWORD pid = (DWORD)atoi(argv[1]);
    if (!pid)
        pid = pid_by_name(argv[1]);
    if (!pid) {
        printf("[!] Nie znalazlem procesu '%s'. Gra wlaczona?\n", argv[1]);
        return 2;
    }

    char full[MAX_PATH];
    if (!GetFullPathNameA(argv[2], MAX_PATH, full, NULL)) {
        printf("[!] Zla sciezka DLL: %s\n", argv[2]);
        return 3;
    }
    if (GetFileAttributesA(full) == INVALID_FILE_ATTRIBUTES) {
        printf("[!] Nie ma pliku DLL: %s\n", full);
        return 3;
    }

    HANDLE proc = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
                                  PROCESS_VM_WRITE | PROCESS_VM_READ |
                                  PROCESS_QUERY_INFORMATION,
                              FALSE, pid);
    if (!proc) {
        printf("[!] OpenProcess blad %lu (uruchom terminal JAKO ADMINISTRATOR)\n",
               GetLastError());
        return 4;
    }

    SIZE_T n = strlen(full) + 1;
    void *mem = VirtualAllocEx(proc, NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem) {
        printf("[!] VirtualAllocEx blad %lu\n", GetLastError());
        CloseHandle(proc);
        return 5;
    }
    if (!WriteProcessMemory(proc, mem, full, n, NULL)) {
        printf("[!] WriteProcessMemory blad %lu\n", GetLastError());
        VirtualFreeEx(proc, mem, 0, MEM_RELEASE);
        CloseHandle(proc);
        return 5;
    }

    FARPROC load = GetProcAddress(GetModuleHandleA("kernel32.dll"), "LoadLibraryA");
    HANDLE th = CreateRemoteThread(proc, NULL, 0,
                                   (LPTHREAD_START_ROUTINE)load, mem, 0, NULL);
    if (!th) {
        printf("[!] CreateRemoteThread blad %lu\n", GetLastError());
        VirtualFreeEx(proc, mem, 0, MEM_RELEASE);
        CloseHandle(proc);
        return 6;
    }

    WaitForSingleObject(th, 10000);
    DWORD ret = 0;
    GetExitCodeThread(th, &ret);          // = HMODULE zaladowanego DLL (0 = blad)
    VirtualFreeEx(proc, mem, 0, MEM_RELEASE);
    CloseHandle(th);
    CloseHandle(proc);

    if (!ret) {
        printf("[!] LoadLibrary zwrocil 0 - DLL sie nie zaladowal (zla bitowosc? brak zaleznosci?)\n");
        return 7;
    }
    printf("[ok] Wstrzyknieto %s do PID %lu (HMODULE=0x%08lX)\n", full, pid, ret);
    return 0;
}
