"""
Hotfix bez restartu gry: zaklada hook per-klatka (vCore) Z ZEWNATRZ dla JUZ WSTRZYKNIETEJ autopot.dll.

Kiedy potrzebne: launcher podmienil vCore.exe, Process sie przesunal, a wstrzyknieta (stara) DLL miala
sztywny RVA -> log "hook: prolog @0x... nie pasuje" i brak hooka (nie dziala natywny loot/chodzenie/picie).
Nowa DLL sama szuka Process po sygnaturze petli glownej, ale do procesu, w ktorym siedzi stara, nowej
nie wstrzykniemy (mutex per-PID). Zamiast restartu gry robimy dokladnie to, co install_vcore_hook w DLL:
  1) Process = cel ostatniego CALL w petli glownej (ta sama sygnatura co w DLL), prolog 55 8B EC 6A FF;
  2) trampolina RWX: 5 skradzionych bajtow + push <Process+5> + ret; adres zapisany do g_tramp starej DLL;
  3) jmp rel32 na Process -> hook_stub starej DLL (pushad/pushfd/call on_frame/.../jmp [g_tramp]).
Zapis 5 bajtow przy ZATRZYMANYM procesie (NtSuspendProcess) - glowny watek nie wykona polowy patcha.
Po sukcesie pisze logs/hook_<pid>.ok (radar wtedy uzywa natywnych komend).

Uzycie: python recon/hotfix_hook.py [-e vCore.exe]
"""
import argparse
import ctypes
import os
import re
import struct
import sys

import pymem
import pymem.process

PROLOGUE = bytes([0x55, 0x8B, 0xEC, 0x6A, 0xFF])
LOOP_SIG = rb'\x56\x8b\xf1\x8b\xce\xe8....\x8b\xce\x84\xc0\x74.\xe8....\x84\xc0\x75.\x5e\xc3\xe8'
STUB_SIG = rb'\x60\x9c\xe8....\x9d\x61\xa1(....)\xff\xe0'   # hook_stub: pushad;pushfd;call;popfd;popad;mov eax,[g_tramp];jmp eax
LOGS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"))

ntdll = ctypes.WinDLL("ntdll")


def section(pm, base, name):
    pe = base + pm.read_uint(base + 0x3C)
    nsec = pm.read_ushort(pe + 6)
    opt = pm.read_ushort(pe + 0x14)
    sh = pe + 0x18 + opt
    for i in range(nsec):
        s = sh + i * 40
        if pm.read_bytes(s, 8).rstrip(b"\0") == name:
            return base + pm.read_uint(s + 12), pm.read_uint(s + 8)
    raise LookupError(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-e", "--exe", default="vCore.exe")
    args = ap.parse_args()
    pm = pymem.Pymem(args.exe)
    base = pymem.process.module_from_name(pm.process_handle, args.exe).lpBaseOfDll
    pid = pm.process_id

    tlo, tsz = section(pm, base, b".text")
    text = pm.read_bytes(tlo, tsz)
    hits = [m.start() for m in re.finditer(LOOP_SIG, text, re.S)]
    if len(hits) != 1:
        sys.exit("[!] petla glowna: %d trafien (oczekiwano 1)" % len(hits))
    call = tlo + hits[0] + 27
    proc = call + 5 + struct.unpack("<i", pm.read_bytes(call + 1, 4))[0]
    cur = pm.read_bytes(proc, 5)
    print("Process = base+0x%X  bajty %s" % (proc - base, cur.hex()))
    if cur[0] == 0xE9:
        sys.exit("[=] Process juz zahookowany (jmp) - nic nie robie")
    if cur != PROLOGUE:
        sys.exit("[!] nieoczekiwany prolog - rezygnuje")

    dll = [m for m in pm.list_modules() if m.name.lower() == "autopot.dll"]
    if not dll:
        sys.exit("[!] autopot.dll nie jest wstrzyknieta - uruchom radar (wstrzyknie nowa DLL)")
    d = dll[0]
    dimg = pm.read_bytes(d.lpBaseOfDll, d.SizeOfImage)
    sm = list(re.finditer(STUB_SIG, dimg, re.S))
    if len(sm) != 1:
        sys.exit("[!] hook_stub w DLL: %d trafien" % len(sm))
    stub = d.lpBaseOfDll + sm[0].start()
    g_tramp = struct.unpack("<I", sm[0].group(1))[0]
    if pm.read_uint(g_tramp):
        sys.exit("[=] g_tramp juz ustawiony - hook byl zakladany")
    print("hook_stub = 0x%X, g_tramp @ 0x%X" % (stub, g_tramp))

    tramp = pm.allocate(16)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    old = ctypes.c_ulong()
    k32.VirtualProtectEx(pm.process_handle, ctypes.c_void_p(tramp), 16, 0x40, ctypes.byref(old))
    pm.write_bytes(tramp, PROLOGUE + b"\x68" + struct.pack("<I", proc + 5) + b"\xC3", 11)
    pm.write_uint(g_tramp, tramp)

    patch = b"\xE9" + struct.pack("<i", stub - (proc + 5))
    ntdll.NtSuspendProcess(pm.process_handle)
    try:
        k32.VirtualProtectEx(pm.process_handle, ctypes.c_void_p(proc), 5, 0x40, ctypes.byref(old))
        pm.write_bytes(proc, patch, 5)
        k32.VirtualProtectEx(pm.process_handle, ctypes.c_void_p(proc), 5, old.value, ctypes.byref(old))
        k32.FlushInstructionCache(pm.process_handle, ctypes.c_void_p(proc), 5)
    finally:
        ntdll.NtResumeProcess(pm.process_handle)
    with open(os.path.join(LOGS, "hook_%d.ok" % pid), "w") as f:
        f.write("ok")
    print("[+] hook zalozony (tramp=0x%X), marker logs/hook_%d.ok" % (tramp, pid))


if __name__ == "__main__":
    main()
