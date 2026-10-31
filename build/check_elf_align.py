# 临时：检查 ELF 的 PT_LOAD 段对齐（16KB = 0x4000 合规）与文件类型（DYN + INTERP = PIE）
import struct, sys, os

def check(path):
    b = open(path, 'rb').read()
    is64 = b[4] == 2
    e_type = struct.unpack_from('<H', b, 16)[0]
    if is64:
        e_phoff = struct.unpack_from('<Q', b, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from('<HH', b, 54)
    else:
        e_phoff = struct.unpack_from('<I', b, 28)[0]
        e_phentsize, e_phnum = struct.unpack_from('<HH', b, 42)
    loads = []
    interp = None
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        if is64:
            p_type, p_flags = struct.unpack_from('<II', b, o)
            p_align = struct.unpack_from('<Q', b, o + 48)[0]
            p_offset = struct.unpack_from('<Q', b, o + 8)[0]
        else:
            p_type = struct.unpack_from('<I', b, o)[0]
            p_align = struct.unpack_from('<I', b, o + 28)[0]
            p_offset = struct.unpack_from('<I', b, o + 4)[0]
        if p_type == 1:  # PT_LOAD
            loads.append((hex(p_align), p_offset))
        if p_type == 3:  # PT_INTERP
            end = b.index(b'\0', p_offset)
            interp = b[p_offset:end].decode()
    return e_type, loads, interp

for p in sys.argv[1:]:
    e_type, loads, interp = check(p)
    ok = all(a == '0x4000' for a, _ in loads) and len(loads) > 0
    print(f"{os.path.basename(p):24s} type={e_type}(2=DYN) PT_LOAD={len(loads)} align={[a for a, _ in loads]} "
          f"-> 16KB对齐{'OK' if ok else 'FAIL'}  INTERP={interp}")
