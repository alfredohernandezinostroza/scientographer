# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ForceAtlas2 in 2 or 3 dimensions, standard or LinLog, for citation_layouts.py.

Two engines run the same algorithm (fa2 1.1's forces and speed control, default
settings: scaling ratio 2, gravity 1, no size adjustment, attraction not
distributed by degree):

- ``numba`` (when numba is installed, the ``fast-layout`` extra): exact
  repulsion between every pair of papers, compiled and spread over all cores.
  About 0.1 s per iteration for 23,000 papers in 3D on 128 cores.
- ``fa2``: the fa2 package with its Barnes-Hut approximation. Fast in 2D; its
  3D path runs in pure Python (about 7 s per iteration on 23,000 papers).

Both start from the same seeded positions with the same masses (1 + degree), so
a layout is reproducible for a given engine and seed. Force layouts are
chaotic: two engines, or the same engine on different hardware, drift apart in
detail after a few dozen iterations while giving the same structure.
"""

import logging

import igraph as ig
import numpy as np

logger = logging.getLogger(__name__)

ENGINES = ("auto", "numba", "fa2")
SCALING_RATIO = 2.0
GRAVITY = 1.0
JITTER_TOLERANCE = 1.0


def numba_available() -> bool:
    try:
        import numba  # noqa: F401
    except ImportError:
        return False
    return True


def forceatlas2(graph: ig.Graph, dimensions: int = 2, iterations: int = 1000, lin_log: bool = False,
                seed: int = 1, engine: str = "auto") -> tuple[np.ndarray, str]:
    """Positions (one row per vertex, `dimensions` columns) and the engine used.

    The graph is read undirected, without multiple edges or self-loops."""
    if dimensions not in (2, 3):
        raise ValueError(f"dimensions must be 2 or 3, got {dimensions}")
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
    if engine == "auto":
        engine = "numba" if numba_available() else "fa2"
    simple = graph.as_undirected(mode="collapse")
    simple.simplify(multiple=True, loops=True)
    edges = np.array(simple.get_edgelist(), dtype=np.int64).reshape(-1, 2)
    mass = 1.0 + np.array(simple.degree(), dtype=np.float64)
    start = np.random.default_rng(seed).random((simple.vcount(), dimensions))
    if engine == "numba":
        return _run_numba(start, mass, edges, iterations, lin_log), engine
    return _run_fa2(simple, start, iterations, lin_log, seed), engine


def _run_fa2(graph: ig.Graph, start: np.ndarray, iterations: int, lin_log: bool, seed: int) -> np.ndarray:
    from fa2 import ForceAtlas2
    import scipy.sparse

    n = graph.vcount()
    edges = np.array(graph.get_edgelist(), dtype=np.int64).reshape(-1, 2)
    adjacency = scipy.sparse.coo_matrix(
        (np.ones(2 * len(edges)), (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(n, n)).tolil()
    layout = ForceAtlas2(verbose=False, dim=start.shape[1], linLogMode=lin_log, seed=seed,
                         scalingRatio=SCALING_RATIO, gravity=GRAVITY, jitterTolerance=JITTER_TOLERANCE)
    return np.array(layout.forceatlas2(adjacency, pos=start.copy(), iterations=iterations), dtype=np.float64)


_repulsion_kernel: object | None = None


def _repulsion():
    """The compiled pairwise repulsion (built on first use, so numba stays optional)."""
    global _repulsion_kernel
    if _repulsion_kernel is None:
        import warnings

        import numba as nb

        # numba falls back to its own threads when the installed TBB is too old; that is fine.
        warnings.filterwarnings("ignore", message=".*TBB threading layer.*")

        @nb.njit(parallel=True, cache=True)
        def repulsion(pos, mass, coefficient, out):
            n, dim = pos.shape
            for i in nb.prange(n):
                acc = np.zeros(dim)
                for j in range(n):
                    if j == i:
                        continue
                    d2 = 0.0
                    for d in range(dim):
                        t = pos[i, d] - pos[j, d]
                        d2 += t * t
                    if d2 > 0:
                        f = coefficient * mass[i] * mass[j] / d2
                        for d in range(dim):
                            acc[d] += (pos[i, d] - pos[j, d]) * f
                for d in range(dim):
                    out[i, d] = acc[d]

        _repulsion_kernel = repulsion
    return _repulsion_kernel


def _run_numba(start: np.ndarray, mass: np.ndarray, edges: np.ndarray, iterations: int,
               lin_log: bool) -> np.ndarray:
    """fa2's iteration (its vectorized backend) with the exact repulsion compiled."""
    repulsion = _repulsion()
    n = len(start)
    pos = start.copy()
    src, dst = edges[:, 0], edges[:, 1]
    forces = np.zeros_like(pos)
    rep = np.zeros_like(pos)
    speed, efficiency = 1.0, 1.0
    for _ in range(iterations):
        old = forces
        repulsion(pos, mass, SCALING_RATIO, rep)
        forces = rep.copy()
        # gravity (not strong): constant pull towards the origin
        dist = np.sqrt((pos * pos).sum(1))
        g = np.where(dist > 0, mass * GRAVITY / np.where(dist > 0, dist, 1.0), 0.0)
        forces -= pos * g[:, None]
        # attraction along the edges: linear in distance, or log(1 + distance) in LinLog
        diff = pos[src] - pos[dst]
        if lin_log:
            length = np.sqrt((diff * diff).sum(1))
            factor = np.where(length > 0, -np.log1p(length) / np.where(length > 0, length, 1.0), 0.0)
        else:
            factor = -np.ones(len(src))
        pull = diff * factor[:, None]
        np.add.at(forces, src, pull)
        np.add.at(forces, dst, -pull)
        # speed control: swinging (oscillation) slows the layout, traction speeds it up
        swinging = np.sqrt(((old - forces) ** 2).sum(1))
        traction = np.sqrt(((old + forces) ** 2).sum(1))
        total_swinging = float(np.sum(mass * swinging))
        total_traction = 0.5 * float(np.sum(mass * traction))
        estimated = 0.05 * np.sqrt(n)
        min_jitter = np.sqrt(estimated)
        jitter = (JITTER_TOLERANCE * max(min_jitter, min(10.0, estimated * total_traction / (n * n)))
                  if total_traction > 0 else JITTER_TOLERANCE * min_jitter)
        if total_traction > 0 and total_swinging / total_traction > 2.0:
            if efficiency > 0.05:
                efficiency *= 0.5
            jitter = max(jitter, JITTER_TOLERANCE)
        target = float("inf") if total_swinging == 0 else jitter * efficiency * total_traction / total_swinging
        if total_swinging > jitter * total_traction:
            if efficiency > 0.05:
                efficiency *= 0.7
        elif speed < 1000:
            efficiency *= 1.3
        speed = speed + min(target - speed, 0.5 * speed)
        pos = pos + forces * (speed / (1.0 + np.sqrt(speed * mass * swinging)))[:, None]
    return pos
