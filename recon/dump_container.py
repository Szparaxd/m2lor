"""Surowy dump kontenera nazwy 'Metin Walki' @ 0x232dafd0 (obj~0x232dac7c).
Pokaz kazdy DWORD w [0x232da700, 0x232db200): offset od name_ptr, modul-ptr?, float world-range?, vnum 8xxx?."""
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
NAMEPTR = 0x232dafd0
LO = NAMEPTR - 0x900
HI = NAMEPTR + 0x300
pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
cx, cy = 29279.1, 35064.3

blk = pm.read_bytes(LO, HI-LO)
u = np.frombuffer(blk, dtype="<u4")
f = np.frombuffer(blk, dtype="<f4")
print(f"base={base:#x}  name_ptr={NAMEPTR:#x}  dump [{LO:#x},{HI:#x})")
for i in range(len(u)):
    a = LO + i*4
    v = int(u[i]); fv = float(f[i])
    tags = []
    if mlo <= v < mhi:
        try:
            p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
            if int(np.sum((p>=mlo)&(p<mhi)))>=2: tags.append(f"VTABLE base+{v-base:#x}")
            else: tags.append(f"modptr base+{v-base:#x}")
        except Exception: tags.append(f"modptr base+{v-base:#x}")
    if 1000 < abs(fv) < 100000:
        t = f"FLOAT {fv:.1f}"
        if abs(fv-cx)<900: t+=" ~X"
        if abs(fv-cy)<900: t+=" ~Y"
        if abs(-fv-cy)<900: t+=" ~-Y"
        tags.append(t)
    if 8000 <= v <= 8999: tags.append(f"VNUM {v}")
    # heap-ptr?
    if 0x100000 <= v < 0x80000000 and not (mlo<=v<mhi): tags.append("ptr")
    off = a - NAMEPTR
    if tags:
        print(f"  @{a:#x} name{off:+#06x}: u={v:#010x} {' | '.join(tags)}")
# tez: pokaz bajty nazwy
print("\nname bytes:", pm.read_bytes(NAMEPTR, 24))
print("as string field: size=", pm.read_uint(NAMEPTR+16), "cap=", pm.read_uint(NAMEPTR+20), "ptr=", hex(pm.read_uint(NAMEPTR)))
