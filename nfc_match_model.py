#!/usr/bin/env python3
"""Independent re-check of the CLRC663 reader antenna + matching, as built.

Units: metres, henry, ohm, farad internally; mm in the geometry tables.
Coordinates: reader board (KiCad, y down).  z: +z toward the tag (front),
F.Cu coil at z = 0, everything behind the reader at z < 0.
"""
import sys
import numpy as np

MU0 = 4e-7 * np.pi
F = 13.56e6
W = 2 * np.pi * F
RHO_CU = 1.72e-8
T_CU = 35e-6                      # 1 oz finished outer copper
DELTA = np.sqrt(2 * RHO_CU / (W * MU0))

# ---------------------------------------------------------------- geometry
# gen_coil.py, in footprint coordinates (mm), origin = outer copper top-left.
Wt, GAP, N, PITCH = 3.0, 0.5, 3, 3.5
OX, OY = 94.0, 78.0
FEED_X = 45.5
PAD_1 = (FEED_X, OY + 1.0)
PAD_2 = (FEED_X - 2.75, OY + 3.0)
COIL_ORIGIN = (94.67, 1.83)
T_B = -1.6e-3                      # B.Cu z (board thickness)


def turn_path(k):
    i = Wt / 2 + PITCH * k
    x0, y0, x1, y1 = i, i, OX - i, OY - i
    c = 3.0 - 1.0 * k
    start = (FEED_X - PITCH * k, y1)
    end = (start[0] - 2 * PITCH, y1)
    return [start, (x1 - c, y1), (x1, y1 - c), (x1, y0 + c), (x1 - c, y0),
            (x0 + c, y0), (x0, y0 + c), (x0, y1 - c), (x0 + c, y1), end]


def coil_centreline():
    """Polyline [(x,y,z,width)] of the whole conductor pad1 -> ... -> pad2, in metres,
    board coordinates."""
    pts = []
    pts.append((PAD_1[0], PAD_1[1], 0.0, 1.5))
    for k in range(N):
        p = turn_path(k)
        for q in p:
            pts.append((q[0], q[1], 0.0, Wt))
    inner = turn_path(N - 1)[-1]
    # via down, bridge on B.Cu, back up to pad 2
    pts.append((inner[0], inner[1], T_B * 1e3, 1.5))
    pts.append((inner[0], PAD_2[1], T_B * 1e3, 1.5))
    pts.append((PAD_2[0], PAD_2[1], T_B * 1e3, 1.5))
    pts.append((PAD_2[0], PAD_2[1], 0.0, 1.5))
    out = []
    for x, y, z, w in pts:
        out.append(((x + COIL_ORIGIN[0]) * 1e-3, (y + COIL_ORIGIN[1]) * 1e-3, z * 1e-3, w * 1e-3))
    return out


def segments_from_polyline(pl):
    segs = []
    for a, b in zip(pl, pl[1:]):
        pa, pb = np.array(a[:3]), np.array(b[:3])
        if np.linalg.norm(pb - pa) < 1e-9:
            continue
        segs.append((pa, pb, min(a[3], b[3])))
    return segs


# ------------------------------------------------------- inductance kernels
def gl(n):
    x, w = np.polynomial.legendre.leggauss(n)
    return (x + 1) / 2, w / 2


def neumann_pairs(A0, A1, B0, B1, nq=6, sub=2.0e-3):
    """Mutual inductance for many segment pairs via Gauss quadrature.
    A0,A1,B0,B1: (n,3) arrays.  Returns (n,) in henry.  Each segment is split
    into pieces <= sub so the quadrature stays accurate near each other."""
    xq, wq = gl(nq)
    dA = A1 - A0
    dB = B1 - B0
    la = np.linalg.norm(dA, axis=1)
    lb = np.linalg.norm(dB, axis=1)
    na = np.maximum(1, np.ceil(la / sub).astype(int))
    nb = np.maximum(1, np.ceil(lb / sub).astype(int))
    out = np.zeros(len(A0))
    dot = np.einsum('ij,ij->i', dA, dB)
    # group by (na, nb) to vectorise
    for ka in np.unique(na):
        for kb in np.unique(nb):
            idx = np.where((na == ka) & (nb == kb))[0]
            if not len(idx):
                continue
            sa = (np.arange(ka)[:, None] + xq[None, :]).ravel() / ka       # (ka*nq,)
            wa = np.tile(wq, ka) / ka
            sb = (np.arange(kb)[:, None] + xq[None, :]).ravel() / kb
            wb = np.tile(wq, kb) / kb
            PA = A0[idx][:, None, :] + dA[idx][:, None, :] * sa[None, :, None]   # (n, ma, 3)
            PB = B0[idx][:, None, :] + dB[idx][:, None, :] * sb[None, :, None]   # (n, mb, 3)
            d = PA[:, :, None, :] - PB[:, None, :, :]
            r = np.linalg.norm(d, axis=3)
            r = np.maximum(r, 1e-7)
            integ = np.einsum('a,b,nab->n', wa, wb, 1.0 / r)
            out[idx] = MU0 / (4 * np.pi) * dot[idx] * integ
    return out


def self_strip(l, w, t):
    """Self-inductance of a straight rectangular bar (Grover/Ruehli)."""
    return MU0 * l / (2 * np.pi) * (np.log(2 * l / (w + t)) + 0.5 + 0.2235 * (w + t) / l)


def coil_self_inductance(segs, nfil=5, t=T_CU):
    """Filament method: each conductor segment split into nfil parallel
    filaments across its width, each filament carrying 1/nfil of the current.
    L = sum over all filament pairs (M_ij / nfil^2) + self terms."""
    fil = []   # (p0, p1, w_f)
    for p0, p1, w in segs:
        d = p1 - p0
        l = np.linalg.norm(d)
        u = d / l
        # in-plane normal (segments are in z=const planes except the vias)
        if abs(u[2]) > 0.99:
            fil.append((p0, p1, w))     # via: a single filament
            continue
        nrm = np.array([-u[1], u[0], 0.0])
        for k in range(nfil):
            off = (k - (nfil - 1) / 2) * (w / nfil)
            fil.append((p0 + nrm * off, p1 + nrm * off, w / nfil))
    n = len(fil)
    P0 = np.array([f[0] for f in fil]); P1 = np.array([f[1] for f in fil]); WF = np.array([f[2] for f in fil])
    # group filaments by parent segment for the 1/nfil weights
    Ltot = 0.0
    # pairs i<j (all), self i==j analytic
    ii, jj = np.triu_indices(n, 1)
    M = neumann_pairs(P0[ii], P1[ii], P0[jj], P1[jj])
    # weights: every filament carries I/nfil of its parent; vias carry I
    wgt = np.where(np.abs((P1 - P0)[:, 2]) > 0.99 * np.linalg.norm(P1 - P0, axis=1), 1.0, 1.0 / nfil)
    Ltot += 2 * np.sum(M * wgt[ii] * wgt[jj])
    for k in range(n):
        l = np.linalg.norm(P1[k] - P0[k])
        Ltot += wgt[k] ** 2 * self_strip(l, WF[k], t)
    return Ltot


# ------------------------------------------------------ AC resistance (2-D)
def ac_resistance_per_metre(widths, gaps, t=T_CU, nx=24, nz=8, return_dist=78e-3, nedge=18, nmid=20, first=3e-6, ratio=1.3):
    """PEEC cross-section: N parallel strips in series (same I each), with an
    identical return group at return_dist carrying -I.  Returns R'/length for
    the whole group (sum over the strips) at W, and the DC value."""
    fils = []   # (y, z, dy, dz, conductor id)
    y = 0.0
    cid = 0
    for w, g in zip(widths, list(gaps) + [0]):
        # filament edges graded toward both strip edges (skin depth 18 um)
        e = [0.0]; st = first
        while e[-1] + st < w / 2 - 1e-9 and len(e) < nedge + 1:
            e.append(e[-1] + st); st *= ratio
        e = np.array(e); mid = np.linspace(e[-1], w - e[-1], nmid + 1)
        xe = np.concatenate([e, mid[1:-1], w - e[::-1]])
        for a, b in zip(xe, xe[1:]):
            for iz in range(nz):
                fils.append((y + (a + b) / 2, (iz + 0.5) * t / nz, b - a, t / nz, cid))
        y += w + g
        cid += 1
    base = np.array(fils)
    ret = base.copy(); ret[:, 0] += return_dist; ret[:, 4] += cid
    allf = np.vstack([base, ret])
    yy, zz, dy, dz, cids = allf.T
    n = len(allf)
    # per-unit-length partial inductances, relative (constant cancels: total I = 0)
    d = np.sqrt((yy[:, None] - yy[None, :]) ** 2 + (zz[:, None] - zz[None, :]) ** 2)
    gmd_self = 0.2235 * (dy + dz)
    d[np.arange(n), np.arange(n)] = gmd_self
    Lp = -MU0 / (2 * np.pi) * np.log(d)
    R = RHO_CU / (dy * dz)
    Z = np.diag(R) + 1j * W * Lp
    nc = int(cids.max()) + 1
    # unknowns: filament currents (n) + conductor voltages (nc)
    # Z i - V_c = 0 ; sum_{i in c} i = I_c
    A = np.zeros((n + nc, n + nc), complex)
    A[:n, :n] = Z
    for c in range(nc):
        A[np.where(cids == c)[0], n + c] = -1
        A[n + c, np.where(cids == c)[0]] = 1
    b = np.zeros(n + nc, complex)
    for c in range(nc):
        b[n + c] = 1.0 if c < nc // 2 else -1.0
    sol = np.linalg.solve(A, b)
    i = sol[:n]
    P = np.sum(R * np.abs(i) ** 2)
    # power of the forward group only (half)
    fwd = cids < nc // 2
    Pf = np.sum((R * np.abs(i) ** 2)[fwd])
    Rdc = sum(RHO_CU / (w * t) for w in widths)
    return Pf, Rdc


# --------------------------------------------------- eddy sheets (stream fn)
def hoer_love(a1, a2, b1, b2, rho):
    """Mutual inductance of two parallel filaments along the same axis:
    a in [a1,a2], b in [b1,b2], lateral distance rho (>0).  Vectorised."""
    def G(u):
        return u * np.arcsinh(u / rho) - np.sqrt(u * u + rho * rho)
    return MU0 / (4 * np.pi) * (G(a2 - b1) - G(a1 - b1) - G(a2 - b2) + G(a1 - b2))


class Sheet:
    """Rectangular thin conducting sheet, grid of h x h cells, loop current
    (stream function) per cell."""

    def __init__(self, x0, x1, y0, y1, z, h, t_eff):
        nx = max(1, int(round((x1 - x0) / h)))
        ny = max(1, int(round((y1 - y0) / h)))
        self.hx, self.hy = (x1 - x0) / nx, (y1 - y0) / ny
        self.nx, self.ny, self.z = nx, ny, z
        self.x0, self.y0 = x0, y0
        self.rs = RHO_CU / t_eff
        # edges: horizontal (along x) at rows 0..ny, columns 0..nx-1 ; vertical (along y) at cols 0..nx, rows 0..ny-1
        self.ncell = nx * ny
        H = [(x0 + i * self.hx, y0 + j * self.hy, 'x') for j in range(ny + 1) for i in range(nx)]
        V = [(x0 + i * self.hx, y0 + j * self.hy, 'y') for j in range(ny) for i in range(nx + 1)]
        self.edges = H + V
        self.nH = len(H)
        # incidence: loop c (i,j) runs counter-clockwise: bottom edge +x, right edge +y, top edge -x, left edge -y
        A = np.zeros((len(self.edges), self.ncell))
        for j in range(ny):
            for i in range(nx):
                c = j * nx + i
                A[j * nx + i, c] += 1                 # bottom (row j), along +x
                A[(j + 1) * nx + i, c] -= 1           # top (row j+1)
                A[self.nH + j * (nx + 1) + (i + 1), c] += 1   # right (col i+1), along +y
                A[self.nH + j * (nx + 1) + i, c] -= 1         # left
        self.A = A

    def edge_arrays(self):
        P0, P1, L = [], [], []
        for x, y, o in self.edges:
            if o == 'x':
                P0.append((x, y, self.z)); P1.append((x + self.hx, y, self.z)); L.append(self.hx)
            else:
                P0.append((x, y, self.z)); P1.append((x, y + self.hy, self.z)); L.append(self.hy)
        return np.array(P0), np.array(P1), np.array(L)


def edge_partial_inductance(sheets):
    """Partial inductance matrix among all edges of all sheets (closed form)."""
    P0s, P1s, Ws = [], [], []
    for s in sheets:
        P0, P1, L = s.edge_arrays()
        P0s.append(P0); P1s.append(P1)
        Ws.append(np.where([o == 'x' for _, _, o in s.edges], s.hy, s.hx))   # strip width of the edge current
    P0 = np.vstack(P0s); P1 = np.vstack(P1s); Wd = np.concatenate(Ws)
    n = len(P0)
    d = P1 - P0
    alongx = np.abs(d[:, 0]) > 0
    P = np.zeros((n, n))
    for ax, mask in ((0, alongx), (1, ~alongx)):
        idx = np.where(mask)[0]
        a1 = P0[idx, ax]; a2 = P1[idx, ax]
        oth = [k for k in range(3) if k != ax]
        lat = P0[idx][:, oth]
        rho = np.sqrt(((lat[:, None, :] - lat[None, :, :]) ** 2).sum(2))
        gmd = 0.2235 * (Wd[idx] + T_CU)
        rho_eff = np.where(rho < 1e-9, gmd[:, None], rho)
        # small-rho correction between neighbouring strips: use centre distance (ok for rho >= width)
        M = hoer_love(a1[:, None], a2[:, None], a1[None, :], a2[None, :], rho_eff)
        P[np.ix_(idx, idx)] = M
    return P


def coil_to_edges(coil_segs, sheets):
    P0s, P1s = [], []
    for s in sheets:
        P0, P1, _ = s.edge_arrays()
        P0s.append(P0); P1s.append(P1)
    E0 = np.vstack(P0s); E1 = np.vstack(P1s)
    C0 = np.array([s[0] for s in coil_segs]); C1 = np.array([s[1] for s in coil_segs])
    M = np.zeros(len(E0))
    for k in range(len(C0)):
        M += neumann_pairs(np.repeat(C0[k][None], len(E0), 0), np.repeat(C1[k][None], len(E0), 0), E0, E1, nq=4, sub=2.5e-3)
    return M


def sheet_effect(coil_segs, sheets):
    """Return (dL, dR) of the coil from eddy currents in the sheets."""
    P = edge_partial_inductance(sheets)
    A = np.zeros((P.shape[0], sum(s.ncell for s in sheets)))
    Rvec = np.zeros(P.shape[0])
    r0 = c0 = 0
    for s in sheets:
        A[r0:r0 + len(s.edges), c0:c0 + s.ncell] = s.A
        # edge resistance: sheet resistance * length / width
        _, _, L = s.edge_arrays()
        wd = np.where([o == 'x' for _, _, o in s.edges], s.hy, s.hx)
        Rvec[r0:r0 + len(s.edges)] = s.rs * L / wd
        r0 += len(s.edges); c0 += s.ncell
    assert np.isfinite(P).all(), 'non-finite partial inductance'
    Lloop = A.T @ P @ A
    Rloop = A.T @ np.diag(Rvec) @ A
    m = A.T @ coil_to_edges(coil_segs, sheets)
    Zl = Rloop + 1j * W * Lloop
    psi = np.linalg.solve(Zl, -1j * W * m)          # induced loop currents for I_coil = 1
    dZ = 1j * W * (m @ psi)                         # reflected impedance
    return dZ.imag / W, dZ.real


# ------------------------------------------------------------ field on axis
def h_field(coil_segs, pts, I=1.0):
    """Biot-Savart H (A/m) at pts (n,3) from the coil polyline carrying I."""
    H = np.zeros_like(pts)
    for p0, p1, _ in coil_segs:
        d = p1 - p0
        l = np.linalg.norm(d)
        nsub = max(2, int(np.ceil(l / 1e-3)))
        s = (np.arange(nsub) + 0.5) / nsub
        P = p0[None, :] + d[None, :] * s[:, None]
        dl = d / nsub
        r = pts[:, None, :] - P[None, :, :]
        rn = np.linalg.norm(r, axis=2)[:, :, None]
        H += I / (4 * np.pi) * np.sum(np.cross(dl[None, None, :], r) / rn ** 3, axis=1)
    return H


# ------------------------------------------------------------- the network
def microstrip(w, h, er=4.4):
    """Hammerstad: Z0 and eps_eff for a microstrip w over h."""
    u = w / h
    ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 12 / u) ** -0.5
    if u < 1:
        z0 = 60 / np.sqrt(ee) * np.log(8 / u + u / 4)
    else:
        z0 = 120 * np.pi / (np.sqrt(ee) * (u + 1.393 + 0.667 * np.log(u + 1.444)))
    c = np.sqrt(ee) / (3e8 * z0)
    l = z0 * np.sqrt(ee) / 3e8
    return z0, ee, c, l


def track_lc(layer, w, length):
    """(L, C) of a track of w x length on a layer, over the strip's GND."""
    # stackup: F.Cu | 0.2104 | In1 (GND) | 1.065 | In2 | 0.2104 | B.Cu ; B.Cu pour is GND too
    if layer == 'F.Cu':
        _, _, c, l = microstrip(w, 0.2104e-3)
    elif layer == 'B.Cu':
        _, _, c, l = microstrip(w, 0.2104e-3 + 0.015e-3 + 1.065e-3 + 0.015e-3)
    elif layer == 'In2':
        # between In1 (1.065 above) and the B.Cu pour (0.21 below): take the embedded microstrip over 0.21 in er
        _, _, c, l = microstrip(w, 0.2104e-3, er=4.4)
        c *= 4.4 / ((4.4 + 1) / 2 + (4.4 - 1) / 2 * (1 + 12 / (w / 0.2104e-3)) ** -0.5)  # fully embedded
        l *= 0.92
    return l * length, c * length


NETS = {
    'ANT_A': [('F.Cu', 0.5e-3, 6.5e-3)],
    'ANT_B': [('F.Cu', 0.5e-3, 4.4e-3)],
    'ANT_C2_A': [('F.Cu', 0.5e-3, 13.2e-3)],
    'ANT_C2_B': [('F.Cu', 0.5e-3, 13.3e-3), ('In2', 0.5e-3, 12.3e-3)],
    'ANT_NODE_A': [('B.Cu', 0.3e-3, 50.0e-3), ('F.Cu', 0.3e-3, 15.3e-3)],
    'ANT_NODE_B': [('B.Cu', 0.3e-3, 33.8e-3), ('F.Cu', 0.3e-3, 20.6e-3)],
    'TX1': [('B.Cu', 0.25e-3, 17.1e-3), ('F.Cu', 0.25e-3, 6.7e-3)],
    'TX2': [('B.Cu', 0.25e-3, 4.1e-3), ('F.Cu', 0.25e-3, 6.4e-3), ('In2', 0.25e-3, 11.2e-3)],
}
VIAS = {'ANT_C2_B': 2, 'ANT_NODE_A': 3, 'ANT_NODE_B': 2, 'TX1': 3, 'TX2': 3}
L_VIA = 0.5e-9


def net_lc(name):
    L = sum(track_lc(*t)[0] for t in NETS[name]) + VIAS.get(name, 0) * L_VIA
    C = sum(track_lc(*t)[1] for t in NETS[name])
    return L, C


def z_tx(Lc, Rc, C1=(47e-12, 3.3e-12), C2=(110e-12, 4.3e-12), L0=470e-9, C0=120e-12, Rq=2.94,
         parasitic=True, Rl0=0.8, Rsdiv=17.9e3, f=F, detail=False, dC2=(0, 0), dC1=(0, 0), dL0=(0, 0)):
    """Nodal analysis of the full ladder, differential drive +-1 V on TX1/TX2.
    Returns Z_diff = 2 V / I_avg, plus per-side Z."""
    w = 2 * np.pi * f
    yC = lambda C: 1j * w * C
    zL = lambda L, R=0: R + 1j * w * L
    # nodes: 0 TX1, 1 A1 (L0 side), 2 A1' (C1 side), 3 N1 (C2 node), 4 ANT1, 5 ANT2, 6 N2, 7 A2', 8 A2, 9 TX2
    nn = 10
    Y = np.zeros((nn, nn), complex)

    def ser(a, b, z):
        y = 1 / z
        Y[a, a] += y; Y[b, b] += y; Y[a, b] -= y; Y[b, a] -= y

    def sh(a, y):
        Y[a, a] += y
    par = {k: net_lc(k) for k in NETS} if parasitic else {k: (0, 0) for k in NETS}
    for side, (tx, a, a2, n, ant, sgn) in enumerate([('TX1', 1, 2, 3, 4, 'A'), ('TX2', 8, 7, 6, 5, 'B')]):
        txn = 0 if side == 0 else 9
        Ltx, Ctx = par[tx]
        sh(txn, yC(Ctx / 2))
        ser(txn, a, zL(L0 + Ltx + dL0[side], Rl0))        # TX track L lumped into L0
        sh(a, yC(Ctx / 2))
        sh(a, yC(C0))
        Ln, Cn = par['ANT_NODE_' + sgn]
        sh(a, yC(Cn / 2))
        ser(a, a2, zL(Ln) if Ln else 1e-9)
        sh(a2, yC(Cn / 2))
        sh(a2, 1 / Rsdiv)
        ser(a2, n, 1 / yC(sum(C1) + dC1[side]))
        Lc2, Cc2 = par['ANT_C2_' + sgn]
        sh(n, yC(sum(C2) + dC2[side] + Cc2))
        La, Ca = par['ANT_' + sgn]
        ser(n, ant, Rq + zL(Lc2 + La))
        sh(ant, yC(Ca))
    ser(4, 5, zL(Lc, Rc))
    # drive: TX1 = +1, TX2 = -1
    known = {0: 1.0, 9: -1.0}
    unk = [i for i in range(nn) if i not in known]
    Yuu = Y[np.ix_(unk, unk)]
    rhs = -sum(Y[np.ix_(unk, [k])][:, 0] * v for k, v in known.items())
    vu = np.linalg.solve(Yuu, rhs)
    V = np.zeros(nn, complex)
    for k, v in known.items():
        V[k] = v
    V[unk] = vu
    I1 = (Y[0] @ V)
    I2 = -(Y[9] @ V)
    Iavg = (I1 + I2) / 2
    Zd = 2 / Iavg
    Icoil = (V[4] - V[5]) / zL(Lc, Rc)
    out = dict(Zd=Zd, Z1=1 / I1, Z2=1 / I2, Icoil_per_V=Icoil, V=V)
    return out if detail else Zd


def drive(Zd, Icoil_per_V, tvdd, Rdrv=3.0):
    """Square-wave H-bridge drive at TVDD: fundamental rms V = 0.9 TVDD into Rdrv + Zd."""
    Vf = (4 / np.pi) * tvdd / np.sqrt(2)
    Zt = Zd + Rdrv
    I = Vf / Zt
    P = abs(I) ** 2 * (Zd.real + Rdrv)
    Itvdd = P / tvdd
    Vload = I * Zd                                   # rms differential at the TX pins
    Icoil = abs(Icoil_per_V * Vload / 2)             # the model used +-1 V (2 V differential)
    return Itvdd, Icoil, abs(I)


def main():
    global NETS
    pl = coil_centreline()
    segs = segments_from_polyline(pl)
    length = sum(np.linalg.norm(b - a) for a, b, _ in segs)
    print(f'skin depth {DELTA*1e6:.1f} um; conductor length {length*1e3:.1f} mm')

    # 1. inductance free-air
    L_free = coil_self_inductance(segs, nfil=5)
    L_free7 = coil_self_inductance(segs, nfil=7)
    print(f'L free-air: {L_free*1e9:.0f} nH (5 fil), {L_free7*1e9:.0f} nH (7 fil)')

    # 2. AC resistance
    Pf, Rdc = ac_resistance_per_metre([Wt * 1e-3] * 3, [GAP * 1e-3] * 2)
    straight = 2 * (94 + 78) * 1e-3 - 0.0105 * 4   # rough straight length per turn group ~ one turn-set
    # total per-metre for the 3-strip group corresponds to one "lap"; laps = length/3 approx. use total length/3 per strip
    lap = length / 3
    R_ac_turns = Pf * lap
    R_dc_turns = Rdc * lap
    # bridge + feed (1.5 mm wide, ~25 mm): isolated strip
    Pb, Rdcb = ac_resistance_per_metre([1.5e-3], [])
    R_bridge = Pb * 26e-3
    R_ac = R_ac_turns + R_bridge
    print(f'R dc turns {R_dc_turns*1e3:.0f} mohm, R ac turns {R_ac_turns*1e3:.0f} mohm (x{Pf/Rdc:.2f}), bridge+feed {R_bridge*1e3:.0f} mohm -> R_ac {R_ac*1e3:.0f} mohm')
    Qu = W * L_free / R_ac
    print(f'Q unloaded {Qu:.0f}; loaded with 2 Rq: {W*L_free/(R_ac+2*2.94):.1f}')

    # 3. sheets
    t_eff = min(T_CU, DELTA)   # sheet resistance of the eddy currents (one skin depth of copper)
    z_shield = -13e-3
    z_d1 = -24e-3
    cases = {
        'reader strip (F/In1/B, coplanar)': [Sheet(91.97e-3, 191.37e-3, 84.83e-3, 100.03e-3, -0.25e-3, 2.5e-3, T_CU)],
        'shield strip pour (z -13)': [Sheet(91.67e-3, 191.67e-3, 84.83e-3, 100.33e-3, z_shield, 2.5e-3, T_CU)],
        'D1 as a solid sheet (z -24)': [Sheet(100.0e-3, 168.6e-3, 46.7e-3, 100.0e-3, z_d1, 4e-3, T_CU)],
    }
    tot_dL = tot_dR = 0
    results = {}
    for name, sh in cases.items():
        dL, dR = sheet_effect(segs, sh)
        results[name] = (dL, dR)
        print(f'  {name}: dL {dL*1e9:+.1f} nH ({dL/L_free*100:+.2f} %), dR {dR*1e3:+.1f} mohm')
    all_sheets = [s for sh in cases.values() for s in sh]
    dL_all, dR_all = sheet_effect(segs, all_sheets)
    print(f'  all together: dL {dL_all*1e9:+.1f} nH ({dL_all/L_free*100:+.2f} %), dR {dR_all*1e3:+.1f} mohm')
    # shield copper 50 % coverage sensitivity: D1 half
    d1half = [Sheet(100.0e-3, 168.6e-3, 46.7e-3, 73e-3, z_d1, 4e-3, T_CU)]
    dLh, _ = sheet_effect(segs, d1half)
    print(f'  D1 only its north half under the coil: dL {dLh*1e9:+.1f} nH')

    # Small metal parts: energy method, dL = -mu0 * alpha * (H/I)^2 with alpha the
    # magnetic polarizability volume of a perfect conductor (eddy limit holds:
    # skin depth 18 um in Cu, ~4 um in steel, both << part size).
    #   thin disk radius R, normal field: alpha = 8 R^3 / 3
    #   cylinder radius a length l, axial field: alpha = pi a^2 l
    #   thin strip width w per length, normal field: alpha' = pi w^2 / 4
    #   closed ring: -M^2/L_ring
    scr = np.array([[108.67e-3, 21.44e-3, 0.5e-3], [174.67e-3, 21.44e-3, 0.5e-3],
                    [113.97e-3, 97.46e-3, 0.5e-3], [166.04e-3, 92.38e-3, 0.5e-3]])
    Hs = h_field(segs, scr)
    dL_screw = 0.0
    for k in range(4):
        hz = Hs[k, 2]
        alpha = 8 / 3 * (2.75e-3) ** 3 + np.pi * (1.5e-3) ** 2 * 12e-3 + 8 / 3 * (2.75e-3) ** 3   # head + 12 mm shaft + nut
        dLk = -MU0 * alpha * hz ** 2
        dL_screw += dLk
        print(f'  steel M3 at ({scr[k,0]*1e3:.1f},{scr[k,1]*1e3:.1f}): Hz {hz:+.1f} A/m per A -> dL {dLk*1e12:+.0f} pH')
    print(f'  steel screws total: dL {dL_screw*1e9:+.2f} nH ({dL_screw/L_free*100:+.3f} %)')
    # logo copper (planned): outlines 0.35 mm x ~400 mm, decor 0.6 mm x ~300 mm, 10 via rings
    logo_pts = np.array([[x, y, 0.0] for x in np.linspace(114e-3, 169e-3, 12) for y in np.linspace(21e-3, 61e-3, 9)])
    Hl = h_field(segs, logo_pts)[:, 2]
    h2 = np.mean(Hl ** 2)
    a_logo = np.pi * (0.35e-3) ** 2 / 4 * 0.40 + np.pi * (0.6e-3) ** 2 / 4 * 0.30
    dL_logo = -MU0 * a_logo * h2
    Hc = h_field(segs, np.array([[141.67e-3, 40.8e-3, 0.0]]))[0, 2]
    Mring = MU0 * Hc * np.pi * (0.55e-3) ** 2
    Lring = MU0 * 0.55e-3 * (np.log(8 * 0.55 / 0.25) - 2)
    dL_rings = -10 * Mring ** 2 / Lring
    # loss: surface resistance Rs = rho/delta on the strip edges; order Rs * (2H)^2 * w * l
    Rs = RHO_CU / DELTA
    dR_logo = Rs * 4 * h2 * (0.35e-3 * 0.40 + 0.6e-3 * 0.30) * 2
    print(f'  logo copper (planned, ~0.7 m of 0.35-0.6 mm strokes): H_rms over the box {np.sqrt(h2):.1f} A/m per A; dL {dL_logo*1e12:+.0f} pH, via rings x10 {dL_rings*1e12:+.1f} pH, dR ~{dR_logo*1e3:.1f} mohm')
    # LED leadframes: 2 x WS2812B, 4 pads ~2x2.5 mm each, in the strip
    led = np.array([[96.2e-3, 87.9e-3, 0.0], [177.5e-3, 87.9e-3, 0.0]])
    Hled = h_field(segs, led)[:, 2]
    dL_led = sum(-MU0 * 4 * (8 / 3) * (1.2e-3) ** 3 * h ** 2 for h in Hled)
    print(f'  WS2812B leadframes: Hz {Hled[0]:+.1f}/{Hled[1]:+.1f} A/m per A -> dL {dL_led*1e12:+.1f} pH')
    dL_screw = dL_screw + dL_logo + dL_rings + dL_led
    L_inst = L_free + dL_all + dL_screw
    R_inst = R_ac + dR_all
    print(f'L installed {L_inst*1e9:.0f} nH, R {R_inst*1e3:.0f} mohm; bench (reader alone, no screws) L {(L_free+results["reader strip (F/In1/B, coplanar)"][0])*1e9:.0f} nH')

    # 4. parasitics
    print('\nrouted-net parasitics (L nH, C pF):')
    for k in NETS:
        L, C = net_lc(k)
        print(f'  {k:11s} L {L*1e9:5.1f}  C {C*1e12:4.1f}')
    Ltx1, _ = net_lc('TX1'); Ltx2, _ = net_lc('TX2')
    _, Cn1 = net_lc('ANT_NODE_A'); _, Cn2 = net_lc('ANT_NODE_B')
    for nm, Lt, Cn in (('side A', Ltx1, Cn1), ('side B', Ltx2, Cn2)):
        fc = 1 / (2 * np.pi * np.sqrt((470e-9 + Lt) * (120e-12 + Cn / 2)))
        print(f'  EMC cutoff {nm}: {fc/1e6:.2f} MHz (ideal {1/(2*np.pi*np.sqrt(470e-9*120e-12))/1e6:.2f})')

    # 5. matching
    print('\nmatching, target 18.8 + j0.7 (17-21, |X|<=3):')
    for label, Lc, Rc, par in [('spec coil 1440/0.46, no parasitics', 1440e-9, 0.46, False),
                               ('my coil installed, no parasitics', L_inst, R_inst, False),
                               ('my coil installed, with parasitics', L_inst, R_inst, True),
                               ('my coil bench (reader alone), with parasitics', L_free + results['reader strip (F/In1/B, coplanar)'][0], R_ac, True),
                               ('my coil free-air, with parasitics', L_free, R_ac, True)]:
        r = z_tx(Lc, Rc, parasitic=par, detail=True)
        print(f'  {label:50s} Zd {r["Zd"].real:6.2f} {r["Zd"].imag:+6.2f}j   Z1 {r["Z1"].real:5.2f}{r["Z1"].imag:+5.2f}j  Z2 {r["Z2"].real:5.2f}{r["Z2"].imag:+5.2f}j')

    # what L would make the fitted values hit the target?
    print('\nL sweep with fitted values (no parasitics):')
    for Lnh in (1380, 1400, 1420, 1440, 1460, 1480, 1500, 1520, 1540, 1560):
        Zd = z_tx(Lnh * 1e-9, 0.46, parasitic=False)
        print(f'  L {Lnh} nH -> {Zd.real:6.2f} {Zd.imag:+6.2f}j')

    # trim search: C1 and C2 values that centre the target for my installed coil with parasitics
    print('\nC1/C2 grid for installed coil + parasitics (differential Z):')
    best = None
    for c1 in np.arange(44, 60.1, 0.5):
        for c2 in np.arange(100, 130.1, 0.5):
            Zd = z_tx(L_inst, R_inst, C1=(c1 * 1e-12, 0), C2=(c2 * 1e-12, 0), parasitic=True)
            err = abs(Zd - (18.8 + 0.7j))
            if best is None or err < best[0]:
                best = (err, c1, c2, Zd)
    print(f'  best: C1 {best[1]:.1f} pF, C2 {best[2]:.1f} pF -> {best[3].real:.2f}{best[3].imag:+.2f}j')
    for c1, c2 in [(50.3, 114.3), (47, 114.3), (50.3, 110), (47, 110), (47 + 3.3, 110 + 4.3 + 4.3), (47, 110 + 4.3 + 4.3), (51, 118), (47 + 5.6, 110), (47 + 3.3 + 1.5, 110)]:
        Zd = z_tx(L_inst, R_inst, C1=(c1 * 1e-12, 0), C2=(c2 * 1e-12, 0), parasitic=True)
        print(f'  C1 {c1:5.1f} C2 {c2:6.1f} -> {Zd.real:6.2f} {Zd.imag:+6.2f}j')

    # best values vs the uncertainty of L_inst
    print('\nbest C1/C2 (0.5 pF grid) vs installed L, with parasitics:')
    for Lnh in (1400, 1420, 1443, 1465, 1490):
        bb = None
        for c1 in np.arange(42, 60.1, 0.5):
            for c2 in np.arange(100, 135.1, 0.5):
                Zd = z_tx(Lnh * 1e-9, R_inst, C1=(c1 * 1e-12, 0), C2=(c2 * 1e-12, 0), parasitic=True)
                err = abs(Zd - (18.8 + 0.7j))
                if bb is None or err < bb[0]:
                    bb = (err, c1, c2, Zd)
        Zfit = z_tx(Lnh * 1e-9, R_inst, parasitic=True)
        Zrec = z_tx(Lnh * 1e-9, R_inst, C1=(47e-12, 0), C2=(120e-12, 0), parasitic=True)
        print(f'  L {Lnh}: fitted set -> {Zfit.real:5.1f}{Zfit.imag:+5.1f}j ; best C1 {bb[1]:.1f} C2 {bb[2]:.1f} -> {bb[3].real:.1f}{bb[3].imag:+.1f}j ; (47,120) -> {Zrec.real:5.1f}{Zrec.imag:+5.1f}j')
    # what the recommended set reads on the bench (reader alone) and in free air
    for nm, Lb in (('bench, reader alone', L_free + results['reader strip (F/In1/B, coplanar)'][0]), ('free air', L_free)):
        Zb = z_tx(Lb, R_ac, C1=(47e-12, 0), C2=(120e-12, 0), parasitic=True)
        print(f'  (47,120) {nm}: {Zb.real:.1f}{Zb.imag:+.1f}j')

    # sensitivity
    print('\nsensitivities (installed, parasitics):')
    base = z_tx(L_inst, R_inst, parasitic=True)
    for nm, kw in [('L +1 %', dict(Lc=L_inst * 1.01)), ('C1 +1 pF both', dict(C1=(47e-12, 4.3e-12))), ('C2 +1 pF both', dict(C2=(110e-12, 5.3e-12))),
                   ('L0 +2 %', dict(L0=470e-9 * 1.02)), ('C0 +2 %', dict(C0=120e-12 * 1.02)), ('Rq +5 %', dict(Rq=2.94 * 1.05))]:
        args = dict(Lc=L_inst, Rc=R_inst, parasitic=True); args.update(kw)
        Zd = z_tx(**args)
        print(f'  {nm:14s} dZ {(Zd-base).real:+.2f} {(Zd-base).imag:+.2f}j')

    # 6. drive, current, field, range
    print('\ndrive / current / range:')
    pts_axis = np.array([[141.67e-3, 40.83e-3, z] for z in np.arange(0, 0.1201, 0.001)])
    Hz = h_field(segs, pts_axis)[:, 2]
    for label, Lc, Rc, par, cvals in [('spec coil, no parasitics', 1440e-9, 0.46, False, None),
                                      ('installed as built', L_inst, R_inst, True, None),
                                      ('installed, re-trimmed', L_inst, R_inst, True, best)]:
        kw = {}
        if cvals is not None:
            kw = dict(C1=(cvals[1] * 1e-12, 0), C2=(cvals[2] * 1e-12, 0))
        r = z_tx(Lc, Rc, parasitic=par, detail=True, **kw)
        for tvdd in (3.3, 5.0):
            It, Ic, Itx = drive(r['Zd'], r['Icoil_per_V'], tvdd)
            Hface = abs(Hz[0]) * Ic
            rng = []
            for thr in (1.5, 3.0, 5.0):
                hh = np.abs(Hz) * Ic
                idx = np.where(hh < thr)[0]
                rng.append(pts_axis[idx[0], 2] * 1e3 if len(idx) else 120)
            Vcoil = Ic * np.sqrt(Rc ** 2 + (W * Lc) ** 2)
            print(f'  {label:26s} TVDD {tvdd} V: Zd {r["Zd"].real:.1f}{r["Zd"].imag:+.1f}j  ITVDD {It*1e3:.0f} mA  Icoil {Ic*1e3:.0f} mA rms  H face {Hface:.1f} A/m  range 1.5/3/5 A/m: {rng[0]:.0f}/{rng[1]:.0f}/{rng[2]:.0f} mm  Vcoil {Vcoil:.0f} Vrms  P_Rq {Ic**2*2.94:.2f} W each')

    # 7. layout options: parasitic placement variants
    print('\nlayout variants (installed coil, re-trimmed C1/C2 for each):')
    variants = {
        'as built': dict(NODE_A=(50.0e-3, 15.3e-3), NODE_B=(33.8e-3, 20.6e-3)),
        'C1 beside C0 (ANT_NODE ~5 mm, run becomes ANT_C2)': dict(NODE_A=(0, 5e-3), NODE_B=(0, 5e-3), C2_A_extra=60e-3, C2_B_extra=50e-3),
        'L0/C0 beside C1 (run becomes TX)': dict(NODE_A=(0, 5e-3), NODE_B=(0, 5e-3), TX_extra=(50e-3, 40e-3)),
    }
    saved = {k: list(v) for k, v in NETS.items()}
    for name, v in variants.items():
        NETS = {k: list(vv) for k, vv in saved.items()}
        NETS['ANT_NODE_A'] = [('B.Cu', 0.3e-3, v['NODE_A'][0]), ('F.Cu', 0.3e-3, v['NODE_A'][1])]
        NETS['ANT_NODE_B'] = [('B.Cu', 0.3e-3, v['NODE_B'][0]), ('F.Cu', 0.3e-3, v['NODE_B'][1])]
        if 'C2_A_extra' in v:
            NETS['ANT_C2_A'] = NETS['ANT_C2_A'] + [('B.Cu', 0.5e-3, v['C2_A_extra'])]
            NETS['ANT_C2_B'] = NETS['ANT_C2_B'] + [('B.Cu', 0.5e-3, v['C2_B_extra'])]
        if 'TX_extra' in v:
            NETS['TX1'] = NETS['TX1'] + [('B.Cu', 0.25e-3, v['TX_extra'][0])]
            NETS['TX2'] = NETS['TX2'] + [('B.Cu', 0.25e-3, v['TX_extra'][1])]
        Zd0 = z_tx(L_inst, R_inst, parasitic=True)
        # sensitivity of Z to +-10 % of all parasitic C (fab tolerance of er/thickness) and to L +1 %
        bestv = None
        for c1 in np.arange(44, 60.1, 0.5):
            for c2 in np.arange(100, 130.1, 0.5):
                Zd = z_tx(L_inst, R_inst, C1=(c1 * 1e-12, 0), C2=(c2 * 1e-12, 0), parasitic=True)
                err = abs(Zd - (18.8 + 0.7j))
                if bestv is None or err < bestv[0]:
                    bestv = (err, c1, c2, Zd)
        r = z_tx(L_inst, R_inst, C1=(bestv[1] * 1e-12, 0), C2=(bestv[2] * 1e-12, 0), parasitic=True, detail=True)
        asym = abs(r['Z1'] - r['Z2'])
        # tuning stability: all parasitic C +15 % (prepreg thickness / er), all parasitic L +10 %
        keep = {k: list(v) for k, v in NETS.items()}
        for k in NETS:
            NETS[k] = [(ly, w, ln) for ly, w, ln in NETS[k]]
        Zp = None
        def scaled(cs, ls):
            global NETS
            NETS = {k: [(ly, w, ln) for ly, w, ln in keep[k]] for k in keep}
            # emulate by scaling lengths for L and widths for C is messy: scale via net_lc monkeypatch
            orig = net_lc
            def patched(name, orig=orig):
                L, C = orig(name)
                return L * ls, C * cs
            globals()['net_lc'] = patched
            Z = z_tx(L_inst, R_inst, C1=(bestv[1] * 1e-12, 0), C2=(bestv[2] * 1e-12, 0), parasitic=True)
            globals()['net_lc'] = orig
            return Z
        Zc = scaled(1.15, 1.0); Zl = scaled(1.0, 1.10); Zn = scaled(1.0, 1.0)
        NETS = keep
        print(f'  {name:52s} fitted-values Zd {Zd0.real:5.1f}{Zd0.imag:+5.1f}j; re-trim C1 {bestv[1]:.1f} C2 {bestv[2]:.1f}; |Z1-Z2| {asym:.2f}; parasitic C +15 %: dZ {(Zc-Zn).real:+.2f}{(Zc-Zn).imag:+.2f}j, parasitic L +10 %: dZ {(Zl-Zn).real:+.2f}{(Zl-Zn).imag:+.2f}j')
        for k in ('ANT_NODE_A', 'ANT_NODE_B', 'ANT_C2_A', 'ANT_C2_B', 'TX1', 'TX2'):
            L, C = net_lc(k)
            print(f'      {k:11s} L {L*1e9:5.1f} nH C {C*1e12:4.1f} pF')
    NETS = saved


if __name__ == '__main__':
    main()
