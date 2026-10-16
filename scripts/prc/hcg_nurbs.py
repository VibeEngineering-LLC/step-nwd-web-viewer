import math
from hcg_prim import uvar, ivar


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(s, a):
    return (s * a[0], s * a[1], s * a[2])


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def normalize(a):
    n = norm(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def read_mults(r, deg):
    k = uvar(r, 16)
    nb = math.ceil(math.log2(deg + 2)) if deg else 2
    mults = []
    for i in range(k):
        same = r.read_bool()
        if same and i > 0:
            mults.append(mults[-1])
        elif same and i == 0:
            raise ValueError("first multiplicity flagged as repeated")
        else:
            mults.append(uvar(r, nb))
    return mults


def read_knots(r, mults):
    """Knot reading as established by brute force on old PRC FS3/4/11/12 (only this variant parses all sections):
    bool uniform; if not: 6-bit nbp; bool pseudo -> 1 value; else k-1 values (treated as interval lengths)."""
    k = len(mults)
    uniform = r.read_bool()
    if uniform or k < 2:
        return [i / (k - 1) for i in range(k)] if k > 1 else [0.0]
    nbp = uvar(r, 6)
    tolp = 1.0 / 2 ** (nbp - 1) if nbp > 0 else 1.0
    rd = lambda: r.read_double() if nbp > 30 else ivar(r, nbp) * tolp
    if r.read_bool():
        a = abs(rd())
        if k == 2 or not 0 < a < 0.5:
            return [i / (k - 1) for i in range(k)]
        inner = [a + (1 - 2 * a) * i / (k - 3) for i in range(k - 2)] if k > 3 else [0.5]
        return [0.0] + inner + [1.0]
    d = [abs(rd()) for _ in range(k - 1)]
    tot = sum(d)
    if tot <= 0:
        return [i / (k - 1) for i in range(k)]
    vals, acc = [0.0], 0.0
    for x in d:
        acc += x / tot
        vals.append(acc)
    vals[-1] = 1.0
    return vals


def read_nurbs(r, brep_tol):
    ntol = 0.2 * brep_tol
    deg_u = uvar(r, 5)
    deg_v = uvar(r, 5)
    mult_u = read_mults(r, deg_u)
    mult_v = read_mults(r, deg_v)
    closed_u = r.read_bool()
    closed_v = r.read_bool()
    nu = sum(mult_u) - deg_u - 1
    nv = sum(mult_v) - deg_v - 1
    if nu < 1 or nv < 1 or nu > 10000 or nv > 10000:
        raise ValueError(f"bad nurbs size {nu}x{nv}")
    bits_iso = uvar(r, 20)
    bits_rest = uvar(r, 20)
    P = [[None] * nv for _ in range(nu)]
    P[0][0] = (r.read_double(), r.read_double(), r.read_double())
    for j in range(1, nv):
        d = tuple(ivar(r, bits_iso + 1) * ntol for _ in range(3))
        P[0][j] = add(P[0][j - 1], d)
    for i in range(1, nu):
        d = tuple(ivar(r, bits_iso + 1) * ntol for _ in range(3))
        P[i][0] = add(P[i - 1][0], d)
    for i in range(1, nu):
        for j in range(1, nv):
            t = uvar(r, 2)
            V = sub(P[i - 1][j], P[i - 1][j - 1])
            U = sub(P[i][j - 1], P[i - 1][j - 1])
            base = add(add(P[i - 1][j - 1], U), V)
            if t == 0:
                P[i][j] = base
            elif t == 3:
                x = ivar(r, bits_rest + 1) * ntol
                y = ivar(r, bits_rest + 1) * ntol
                z = ivar(r, bits_rest + 1) * ntol
                P[i][j] = add(base, (x, y, z))
            else:
                N = cross(U, V)
                Ue = normalize(U)
                Ne = normalize(N)
                NUe = cross(Ne, Ue)
                if t == 1:
                    z = ivar(r, bits_rest + 1) * ntol
                    P[i][j] = add(base, mul(z, Ne))
                if t == 2:
                    x = ivar(r, bits_rest + 1) * ntol
                    y = ivar(r, bits_rest + 1) * ntol
                    P[i][j] = add(add(base, mul(x, Ue)), mul(y, NUe))
    knots_u = read_knots(r, mult_u)
    knots_v = read_knots(r, mult_v)
    rational = r.read_bool()
    weights = None
    if rational:
        nbw = uvar(r, 6)
        if nbw > 30:
            weights = [r.read_double() for _ in range(nu * nv)]
        else:
            wtol = r.read_double()
            weights = [ivar(r, nbw) * wtol for _ in range(nu * nv)]
    return {
        'deg_u': deg_u,
        'deg_v': deg_v,
        'mult_u': mult_u,
        'mult_v': mult_v,
        'closed_u': closed_u,
        'closed_v': closed_v,
        'nu': nu,
        'nv': nv,
        'P': P,
        'knots_u': knots_u,
        'knots_v': knots_v,
        'weights': weights,
    }


if __name__ == '__main__':
    print('ok')
