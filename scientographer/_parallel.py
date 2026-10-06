# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run independent per-resolution tasks in worker processes.

Python threads cannot run igraph/leidenalg/networkx code in parallel, so stages
that sweep many resolutions (`communities.workers` in params.yaml) use a pool of
spawned processes. Each worker rebuilds what it needs once, in `initializer`
(typically a structure-only igraph graph from its edge list, which pickles cheaply),
and then runs `task_function` on the tasks it is handed.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
from typing import Any

# One thread per worker for numpy/BLAS: the parallelism is across processes.
_THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def resolve_workers(setting, n_tasks: int) -> int:
    """`auto` (or None) = one process per task, up to the number of CPUs."""
    if setting in (None, "auto"):
        return max(1, min(os.cpu_count() or 1, n_tasks))
    return max(1, min(int(setting), n_tasks))


def run_in_worker_processes(
    tasks: Sequence[Any],
    task_function: Callable[[Any], Any],
    initializer: Callable[..., None],
    initargs: tuple,
    workers: int,
    cost: Callable[[Any], float],
    cleanup: Callable[[], None] | None = None,
) -> list:
    """`task_function(task)` for every task, returned in `tasks` order.

    With workers > 1 the tasks run in a pool of spawned processes, submitted
    costliest first (by `cost`) so one slow task does not start last. With
    workers == 1 the same initializer and task function run in this process, then
    `cleanup` releases what the initializer built. Both functions must be defined
    at module level so the spawned workers can import them.
    """
    if workers <= 1:
        initializer(*initargs)
        try:
            return [task_function(task) for task in tasks]
        finally:
            if cleanup is not None:
                cleanup()

    order = sorted(range(len(tasks)), key=lambda i: cost(tasks[i]), reverse=True)
    # Spawned children inherit the environment at start-up.
    saved = {name: os.environ.get(name) for name in _THREAD_VARIABLES}
    os.environ.update({name: "1" for name in _THREAD_VARIABLES})
    try:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=context, initializer=initializer, initargs=initargs,
        ) as pool:
            futures = {i: pool.submit(task_function, tasks[i]) for i in order}
            return [futures[i].result() for i in range(len(tasks))]
    finally:
        for name, old in saved.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
