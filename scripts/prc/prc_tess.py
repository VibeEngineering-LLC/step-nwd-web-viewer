import numpy as np


def decode(tess):
    coords = tess['coords']
    tri = tess['tri']
    faces = tess['faces']
    V = np.array(coords, dtype=np.float64).reshape(-1, 3)
    n_v = len(V)
    tri_len = len(tri)
    warnings = []
    tris = []

    for k, face in enumerate(faces):
        flags = face['flags']
        start_tri = face['start_tri']
        sizes = face['sizes_tri']
        n_tex_idx = face['n_tex_idx']
        pos = start_tri
        si = 0

        textured = bool(flags & (0x0F00 | 0xF000))
        T = n_tex_idx if textured else 0

        def take(kk):
            nonlocal pos
            if pos + kk > tri_len:
                raise IndexError("tri overflow")
            chunk = tri[pos:pos + kk]
            pos += kk
            return chunk

        def point():
            nonlocal pos
            if pos + 1 + T + 1 > tri_len:
                raise IndexError("tri overflow")
            pos += 1
            pos += T
            idx = tri[pos]
            pos += 1
            return idx // 3

        def point_nonormal():
            nonlocal pos
            if pos + T + 1 > tri_len:
                raise IndexError("tri overflow")
            pos += T
            idx = tri[pos]
            pos += 1
            return idx // 3

        def check_si():
            nonlocal si
            if si >= len(sizes):
                raise IndexError("sizes overflow")

        try:
            if flags & (0x0001 | 0x0010):
                warnings.append(f"face {k}: polyface flags {flags:#x} skipped")
                continue

            if flags & (0x0002 | 0x0200):
                check_si()
                n = sizes[si]
                si += 1
                for _ in range(n):
                    a = point()
                    b = point()
                    c = point()
                    tris.append((a, b, c))

            if flags & (0x0004 | 0x0400):
                check_si()
                nfans = sizes[si]
                si += 1
                for _ in range(nfans):
                    check_si()
                    cnt = sizes[si] & 0x3FFFFFFF
                    single = bool(sizes[si] & 0x40000000)
                    si += 1
                    verts = []
                    if single:
                        if pos + 1 > tri_len:
                            raise IndexError("tri overflow")
                        pos += 1
                        for _ in range(cnt):
                            verts.append(point_nonormal())
                    else:
                        for _ in range(cnt):
                            verts.append(point())
                    for i in range(1, cnt - 1):
                        tris.append((verts[0], verts[i], verts[i + 1]))

            if flags & (0x0008 | 0x0800):
                check_si()
                nstr = sizes[si]
                si += 1
                for _ in range(nstr):
                    check_si()
                    cnt = sizes[si] & 0x3FFFFFFF
                    single = bool(sizes[si] & 0x40000000)
                    si += 1
                    verts = []
                    if single:
                        if pos + 1 > tri_len:
                            raise IndexError("tri overflow")
                        pos += 1
                        for _ in range(cnt):
                            verts.append(point_nonormal())
                    else:
                        for _ in range(cnt):
                            verts.append(point())
                    for i in range(cnt - 2):
                        if i % 2 == 0:
                            tris.append((verts[i], verts[i + 1], verts[i + 2]))
                        else:
                            tris.append((verts[i + 1], verts[i], verts[i + 2]))

            if flags & (0x0020 | 0x2000):
                check_si()
                n = sizes[si]
                si += 1
                for _ in range(n):
                    if pos + 1 > tri_len:
                        raise IndexError("tri overflow")
                    pos += 1
                    a = point_nonormal()
                    b = point_nonormal()
                    c = point_nonormal()
                    tris.append((a, b, c))

            if flags & (0x0040 | 0x4000):
                check_si()
                nfans = sizes[si]
                si += 1
                for _ in range(nfans):
                    check_si()
                    cnt = sizes[si] & 0x3FFFFFFF
                    si += 1
                    if pos + 1 > tri_len:
                        raise IndexError("tri overflow")
                    pos += 1
                    verts = []
                    for _ in range(cnt):
                        verts.append(point_nonormal())
                    for i in range(1, cnt - 1):
                        tris.append((verts[0], verts[i], verts[i + 1]))

            if flags & (0x0080 | 0x8000):
                check_si()
                nstr = sizes[si]
                si += 1
                for _ in range(nstr):
                    check_si()
                    cnt = sizes[si] & 0x3FFFFFFF
                    si += 1
                    if pos + 1 > tri_len:
                        raise IndexError("tri overflow")
                    pos += 1
                    verts = []
                    for _ in range(cnt):
                        verts.append(point_nonormal())
                    for i in range(cnt - 2):
                        if i % 2 == 0:
                            tris.append((verts[i], verts[i + 1], verts[i + 2]))
                        else:
                            tris.append((verts[i + 1], verts[i], verts[i + 2]))

        except IndexError:
            warnings.append(f"face {k}: index overflow")
            continue

    if tris:
        F = np.array(tris, dtype=np.int64)
        mask = np.all(F < n_v, axis=1)
        dropped = int(np.sum(~mask))
        if dropped > 0:
            warnings.append(f"{dropped} triangles dropped due to out-of-bounds indices")
            F = F[mask]
    else:
        F = np.empty((0, 3), dtype=np.int64)

    return V, F, warnings


def bbox(V):
    return V.min(0), V.max(0)


if __name__ == '__main__':
    print('ok')
