"""Preload-and-fork launcher for the long-lived rcon.cli loop commands.

Today each of these (broadcasts, automod, blacklists, ...) is its own
supervisord-managed process, `exec()`-ing `/code/manage.py <name>` fresh —
independently re-importing the entire rcon package (SQLAlchemy models,
hllrcon, pydantic, redis, ...), ~100MB+ RSS paid once per process for
identical, shared machinery.

This launcher imports rcon.cli exactly once, then os.fork()s a child per
logical program instead, so that import cost becomes copy-on-write shared
memory instead of duplicated N times. supervisord manages this one process
instead of the ~12 it used to.

See config/supervisord.conf ([program:worker_launcher]) for how this is
invoked, and rcon/cli.py's `worker_launcher` command for the entrypoint.
"""

import ctypes
import functools
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Callable, Literal
from xmlrpc.server import SimpleXMLRPCServer

logger = logging.getLogger(__name__)

AutoRestart = Literal["always", "unexpected"]

_STATE_PATH = os.getenv(
    "WORKER_LAUNCHER_STATE_PATH", "/data/worker_launcher_state.json"
)
_RPC_PORT = int(os.getenv("WORKER_LAUNCHER_RPC_PORT", "9002"))
_GRACE_PERIOD_SECS = 10
# Backoff before respawning a child that exited before its own startsecs
# elapsed (i.e. it's crash-looping), so a persistently broken loop doesn't
# peg a CPU core the way a startsecs=1 supervisord program would. Capped
# well below supervisord's default so genuinely transient issues (a
# game server restart, a brief DB blip) still recover quickly.
_BACKOFF_SECS = 5

_PR_SET_PDEATHSIG = 1


def _set_parent_death_signal(parent_pid_at_fork: int) -> None:
    """Linux-only: ask the kernel to deliver SIGTERM to this process if its
    parent (the launcher) dies for ANY reason - a crash, an OOM-killer
    SIGKILL, `docker kill` targeting just that PID, anything. Confirmed
    live that without this, a child simply survives as an orphan when its
    launcher parent is SIGKILLed. That matters a lot here specifically:
    supervisord's autorestart=true then starts a *second* launcher, which
    forks a *second* full set of all 12 children, so the orphaned
    survivors end up running duplicated against the same live game server
    (duplicate broadcasts, automod double-firing, etc.) until someone
    notices and manually cleans it up - not just "one thing crashed."

    The realistic trigger here isn't hypothetical: the OOM killer's victim
    heuristic is RSS-based, and per-process RSS accounting attributes
    shared copy-on-write pages to *every* process that maps them - so
    under real memory pressure, the launcher parent (and each child) can
    look like one of the larger individual processes in this container,
    exactly because of how this feature shares memory.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    except Exception:
        logging.getLogger(__name__).exception(
            "worker_launcher: could not set PR_SET_PDEATHSIG - if the "
            "launcher dies unexpectedly, this child could be orphaned "
            "instead of exiting with it"
        )
        return
    if os.getppid() != parent_pid_at_fork:
        # Closes a narrow race: the parent died in the gap between fork()
        # returning here and the prctl() call actually taking effect.
        # PDEATHSIG isn't delivered retroactively for a parent that was
        # already gone before the association was made, so we have to
        # check for it ourselves.
        os._exit(1)


@dataclass
class ChildSpec:
    name: str
    target: Callable[[], None]
    env: dict[str, str] = field(default_factory=dict)
    startretries: int = 100
    startsecs: int = 1
    autorestart: AutoRestart = "always"
    description: str = ""


@dataclass
class ChildState:
    spec: ChildSpec
    pid: int | None = None
    # Monotonic clock, only ever used for computing durations (ran_secs,
    # startsecs comparisons) - immune to wall-clock adjustments.
    start_monotonic: float | None = None
    # Wall-clock epoch seconds, only for RPC display (matches supervisord's
    # own start/stop/now fields, which the existing services-page frontend
    # already expects to feed to dayjs).
    start_time: float | None = None
    stop_time: float | None = None
    restart_count: int = 0
    enabled: bool = True
    last_exit_code: int | None = None
    last_exit_at: str | None = None
    fatal: bool = False

    def statename(self) -> str:
        if self.pid is not None:
            return "RUNNING"
        if self.fatal:
            return "FATAL"
        if not self.enabled:
            return "STOPPED"
        return "STARTING"


def build_default_child_specs() -> list[ChildSpec]:
    # Imported here, not at module top: by the time this runs, rcon.cli is
    # already fully imported (we're executing code that lives inside the
    # process that just finished importing it) — this is a plain
    # sys.modules cache hit, not a circular import. Keeping it out of this
    # module's own top-level imports means importing rcon.worker_launcher
    # itself (e.g. for the RPC/status dataclasses) doesn't pull in all of
    # rcon.cli as a side effect.
    import rcon.cli as cli

    n = os.getenv("SERVER_NUMBER", "")

    # Each entry is (name, zero-arg factory) rather than a plain ChildSpec:
    # a factory defers the `cli.foo.callback` attribute lookup until it's
    # actually called below, inside a try/except. If upstream ever renames
    # or removes one of these commands and this list isn't updated to
    # match, we want *that one program* to be logged and skipped - not an
    # AttributeError here taking down all 12 before any of them get a
    # chance to fork, which is what a plain list of ChildSpec(...) would do
    # (the attribute lookup happens while building the list itself, before
    # this function can return anything).
    entries: list[tuple[str, Callable[[], ChildSpec]]] = [
        (
            "broadcasts",
            lambda: ChildSpec(
                "broadcasts",
                cli.run_broadcast_loop.callback,
                {"LOGGING_FILENAME": f"broadcasts_{n}.log"},
                startretries=100,
                startsecs=1,
                autorestart="always",
                description="Broadcasts server messages",
            ),
        ),
        (
            "expiring_vips",
            lambda: ChildSpec(
                "expiring_vips",
                cli.run_expiring_vips.callback,
                {"LOGGING_FILENAME": f"expiring_vips_{n}.log"},
                startretries=10,
                startsecs=0,
                autorestart="always",
            ),
        ),
        (
            "seed_vip",
            lambda: ChildSpec(
                "seed_vip",
                cli.run_seed_vip.callback,
                {"LOGGING_FILENAME": f"seed_vip_{n}.log"},
                startretries=5,
                startsecs=1,
                autorestart="unexpected",
            ),
        ),
        (
            "log_event_loop",
            lambda: ChildSpec(
                "log_event_loop",
                cli.run_log_loop.callback,
                {
                    "LOGGING_FILENAME": f"log_event_loop_{n}.log",
                    "HLL_DB_DISABLE_CONNECTION_POOL": "1",
                },
                startretries=1_000_000,
                startsecs=1,
                autorestart="always",
            ),
        ),
        (
            "log_stream",
            lambda: ChildSpec(
                "log_stream",
                cli.run_log_stream.callback,
                {
                    "LOGGING_FILENAME": f"log_stream_{n}.log",
                    "HLL_DB_DISABLE_CONNECTION_POOL": "1",
                },
                startretries=5,
                startsecs=0,
                autorestart="unexpected",
            ),
        ),
        (
            "log_recorder",
            lambda: ChildSpec(
                "log_recorder",
                functools.partial(
                    cli.run_log_recorder.callback,
                    interval=10,
                    frequency_min=None,
                    now=False,
                ),
                {"LOGGING_FILENAME": f"log_recorder_{n}.log"},
                startretries=1_000_000,
                startsecs=1,
                autorestart="always",
            ),
        ),
        (
            "auto_settings",
            lambda: ChildSpec(
                "auto_settings",
                cli.auto_settings_loop.callback,
                {
                    "LOGGING_FILENAME": f"auto_settings_{n}.log",
                    "HLL_DB_DISABLE_CONNECTION_POOL": "1",
                },
                startretries=100,
                startsecs=1,
                autorestart="always",
            ),
        ),
        (
            "routines",
            lambda: ChildSpec(
                "routines",
                cli.run_routines.callback,
                {"LOGGING_FILENAME": f"routines_{n}.log"},
                startretries=100,
                startsecs=1,
                autorestart="always",
            ),
        ),
        (
            "live_stats_refresh",
            lambda: ChildSpec(
                "live_stats_refresh",
                cli.run_stats_loop.callback,
                {
                    "LOGGING_FILENAME": f"live_stats_loop_{n}.log",
                    "HLL_DB_DISABLE_CONNECTION_POOL": "1",
                },
                startretries=100,
                startsecs=1,
                autorestart="always",
            ),
        ),
        (
            "automod",
            lambda: ChildSpec(
                "automod",
                cli.run_automod.callback,
                {
                    "LOGGING_FILENAME": f"automod_{n}.log",
                    "COMMANDS_LOGLEVEL": "CRITICAL",
                    "LOGGING_LEVEL": "INFO",
                },
                startretries=100,
                startsecs=10,
                autorestart="always",
            ),
        ),
        (
            "blacklists",
            lambda: ChildSpec(
                "blacklists",
                cli.run_blacklists.callback,
                {"LOGGING_FILENAME": f"blacklists_{n}.log"},
                startretries=100,
                startsecs=10,
                autorestart="always",
            ),
        ),
        (
            "watch_killrate",
            lambda: ChildSpec(
                "watch_killrate",
                cli.watch_killrate.callback,
                {"LOGGING_FILENAME": f"watch_killrate_{n}.log"},
                startretries=5,
                startsecs=0,
                autorestart="unexpected",
            ),
        ),
    ]

    specs = []
    for name, build in entries:
        try:
            specs.append(build())
        except AttributeError:
            logger.error(
                "worker_launcher: %r is no longer available in rcon.cli "
                "(likely renamed/removed upstream without updating "
                "build_default_child_specs() to match) - skipping it, "
                "the other %d programs are unaffected",
                name,
                len(entries) - 1,
                exc_info=True,
            )
    return specs


def _reset_after_fork() -> None:
    """Registered once via os.register_at_fork(after_in_child=...), before
    the first fork. Runs automatically in every forked child, before any of
    _spawn()'s own child-branch code.

    rcon.cli's module-level `ctl = get_rcon()` (needed for click's dynamic
    subcommand registration) transitively creates SQLAlchemy's global
    engine with a live pooled DB connection at import time — inherited by
    every fork()ed child otherwise, which is unsafe to share (the classic
    prefork gotcha). Nulling it out (not just disposing it) also matters
    for a second reason: get_engine()'s poolclass choice (NullPool vs
    QueuePool) is baked in at creation time from HLL_DB_DISABLE_CONNECTION_POOL,
    and that engine is a singleton — a plain .dispose() would leave every
    child stuck with whichever pool class the launcher's own env happened
    to have, silently losing the per-program NullPool/QueuePool
    differentiation that exists today.

    CPython catches any exception raised by an after_in_child hook itself
    (prints "Exception ignored in: ..." straight to stderr, verified
    live) rather than letting it propagate out of os.fork() - so a bug in
    here can't crash the child or corrupt the parent's control flow. But
    that also means a failure here is otherwise invisible: it bypasses
    our own logging entirely (nothing lands in this child's actual log
    file, only in raw stderr) and, if it happened inside one big
    try-less block, a failure resetting the redis pools would prevent
    the SQLAlchemy engine reset from even being attempted. Each cleanup
    step below is therefore independent and self-logging.
    """
    try:
        import rcon.models as models

        old_engine = models._ENGINE
        models._ENGINE = None
        if old_engine is not None:
            old_engine.dispose(close=False)
    except Exception:
        logger.exception(
            "worker_launcher: failed to reset the inherited SQLAlchemy "
            "engine after fork - this child may share a DB connection "
            "with its parent/siblings"
        )

    try:
        # redis-py (8.1.0) already self-heals across fork: ConnectionPool
        # checks pid on every get_connection() and resets itself if it
        # changed. This is kept anyway as cheap, explicit defense-in-depth
        # rather than relying on that undocumented-to-us internal
        # behavior.
        import rcon.cache_utils as cache_utils

        for pool in (cache_utils._REDIS_POOL, cache_utils._GLOBAL_REDIS_POOL):
            if pool is not None:
                pool.reset()
    except Exception:
        logger.exception(
            "worker_launcher: failed to reset the inherited redis "
            "connection pool(s) after fork"
        )


def _reinit_logging_for_child(spec: ChildSpec) -> None:
    """rcon.settings.LOGGING is built once, in the parent, before any child
    knows its own LOGGING_FILENAME — so without this, every forked child
    would share the parent's single inherited file descriptor, losing
    today's one-log-file-per-command separation."""
    import rcon.settings as settings

    stale_handlers = [
        h
        for name in ("rcon", "__main__", "rcon.automods.automod")
        for h in logging.getLogger(name).handlers
        if isinstance(h, logging.FileHandler)
    ]
    dictConfig(settings.build_logging_config())
    for h in stale_handlers:
        h.close()


class Launcher:
    def __init__(self, specs: list[ChildSpec]) -> None:
        allowlist = os.getenv("WORKER_LAUNCHER_PROGRAMS")
        if allowlist:
            names = {n.strip() for n in allowlist.split(",") if n.strip()}
            specs = [s for s in specs if s.name in names]
        self._specs = {s.name: s for s in specs}
        self._children: dict[str, ChildState] = {
            name: ChildState(spec=spec) for name, spec in self._specs.items()
        }
        self._pid_to_name: dict[int, str] = {}
        self._shutdown_requested = False
        self._lock = threading.Lock()

    # -- persistence --------------------------------------------------

    def _load_state(self) -> None:
        try:
            with open(_STATE_PATH) as f:
                saved = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("Failed to load worker_launcher state, ignoring")
            return
        for name, enabled in saved.items():
            if name in self._children:
                self._children[name].enabled = enabled

    def _save_state(self) -> None:
        try:
            with self._lock:
                data = {name: c.enabled for name, c in self._children.items()}
            tmp = f"{_STATE_PATH}.tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, _STATE_PATH)
        except Exception:
            logger.exception("Failed to persist worker_launcher state")

    # -- spawn / reap ---------------------------------------------------

    def _spawn(self, name: str) -> None:
        spec = self._specs[name]
        parent_pid = os.getpid()
        try:
            pid = os.fork()
        except OSError:
            # Most likely resource exhaustion (memory, PID/process limits,
            # etc.) - letting this propagate would kill the whole launcher,
            # orphaning every already-running child (they'd keep running,
            # unsupervised, while supervisord starts a *second* launcher
            # that forks a *second* full set of 12 - duplicate broadcasts/
            # automod/etc. against the same live game server). Handling it
            # here, in the one function every spawn path (initial startup,
            # the backoff timer, and RPC start/restart) already funnels
            # through, means a transient resource crunch degrades to "this
            # one program backs off and retries" instead.
            logger.exception(
                "worker_launcher: os.fork() failed spawning %s - retrying "
                "in %ss",
                name,
                _BACKOFF_SECS,
            )
            with self._lock:
                state = self._children[name]
                state.restart_count += 1
                give_up = state.restart_count > spec.startretries
                if give_up:
                    state.fatal = True
            if give_up:
                logger.critical(
                    "worker %s: os.fork() kept failing past startretries "
                    "(%s), giving up",
                    name,
                    spec.startretries,
                )
                self._save_state()
                return
            timer = threading.Timer(_BACKOFF_SECS, self._spawn, args=(name,))
            timer.daemon = True
            timer.start()
            return
        if pid == 0:
            # Child branch. Must never fall through to normal interpreter
            # shutdown (atexit hooks / buffer flushes would then run in
            # both parent and child) — always os._exit().
            _set_parent_death_signal(parent_pid)
            try:
                os.environ.update(spec.env)
                sys.argv = ["/code/manage.py", spec.name]
                _reinit_logging_for_child(spec)
                spec.target()
                os._exit(0)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                os._exit(code)
            except BaseException:
                logging.getLogger(__name__).exception(
                    "worker %s crashed", spec.name
                )
                os._exit(1)
        else:
            with self._lock:
                state = self._children[name]
                state.pid = pid
                state.start_monotonic = time.monotonic()
                state.start_time = time.time()
                state.last_exit_code = None
                state.fatal = False
                self._pid_to_name[pid] = name

    def _handle_child_exit(self, pid: int, status: int) -> None:
        with self._lock:
            name = self._pid_to_name.pop(pid, None)
        if name is None:
            return
        state = self._children[name]
        spec = state.spec
        ran_secs = (
            time.monotonic() - state.start_monotonic
            if state.start_monotonic
            else 0
        )
        exited_cleanly = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

        with self._lock:
            state.pid = None
            state.stop_time = time.time()
            state.last_exit_code = (
                os.WEXITSTATUS(status) if os.WIFEXITED(status) else -status
            )
            state.last_exit_at = datetime.now(UTC).isoformat()
            if ran_secs >= spec.startsecs:
                state.restart_count = 0
            else:
                state.restart_count += 1

        if not state.enabled:
            logger.info("worker %s exited (disabled, not respawning)", name)
            self._save_state()
            return

        if spec.autorestart == "unexpected" and exited_cleanly:
            logger.info("worker %s exited cleanly, not respawning", name)
            return

        if state.restart_count > spec.startretries:
            with self._lock:
                state.fatal = True
            logger.critical(
                "worker %s exceeded startretries (%s), giving up",
                name,
                spec.startretries,
            )
            self._save_state()
            return

        if ran_secs < spec.startsecs:
            logger.warning(
                "worker %s exited after %.1fs (< startsecs=%s), "
                "backing off %ss before respawn",
                name, ran_secs, spec.startsecs, _BACKOFF_SECS,
            )
            timer = threading.Timer(_BACKOFF_SECS, self._spawn, args=(name,))
            timer.daemon = True
            timer.start()
        else:
            self._spawn(name)

    # -- lifecycle --------------------------------------------------

    def run(self) -> None:
        os.register_at_fork(after_in_child=_reset_after_fork)

        rpc_thread = threading.Thread(
            target=self._serve_rpc, daemon=True, name="worker-launcher-rpc"
        )
        rpc_thread.start()

        class _ShutdownRequested(Exception):
            pass

        def _handle_signal(signum, frame):
            # Must raise, not just set a flag: PEP 475 auto-retries a
            # blocked syscall (our waitpid below) after a signal unless the
            # handler itself raises — a flag-only handler would leave us
            # stuck inside waitpid() until some child happens to exit on
            # its own, never noticing the shutdown request.
            raise _ShutdownRequested()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # Everything from here down - including the initial spawn loop -
        # is inside one umbrella: whatever goes wrong (a failed fork(), a
        # bug in _handle_child_exit, anything), we must still try to
        # SIGTERM every child we've already forked before this process
        # exits. Without this, an uncaught exception here orphans whatever
        # was already running while supervisord starts a *second* launcher
        # that forks a *second* full set of children - duplicate
        # broadcasts/automod/etc. against the same live game server, not
        # just a crash.
        try:
            self._load_state()
            for name, state in self._children.items():
                if state.enabled:
                    self._spawn(name)

            while True:
                pid, status = os.waitpid(-1, 0)
                self._handle_child_exit(pid, status)
        except _ShutdownRequested:
            self._graceful_shutdown()
        except ChildProcessError:
            # No children left to wait on (e.g. all disabled at startup).
            self._graceful_shutdown()
        except BaseException:
            logger.critical(
                "worker_launcher: unexpected error, stopping all children "
                "before exiting",
                exc_info=True,
            )
            self._graceful_shutdown(unexpected=True)

    def _graceful_shutdown(self, unexpected: bool = False) -> None:
        logger.info("worker_launcher shutting down, stopping all children")
        with self._lock:
            live_pids = list(self._pid_to_name)
        for pid in live_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        deadline = time.monotonic() + _GRACE_PERIOD_SECS
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pid_to_name:
                    break
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid:
                    with self._lock:
                        self._pid_to_name.pop(pid, None)
                else:
                    time.sleep(0.2)
            except ChildProcessError:
                break

        with self._lock:
            stragglers = list(self._pid_to_name)
        for pid in stragglers:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Non-zero on an unexpected error (rather than a real SIGTERM/
        # SIGINT) so anything watching exit codes for alerting can tell the
        # difference - supervisord's own autorestart=true here doesn't
        # care either way, but this shouldn't look like a clean exit.
        sys.exit(1 if unexpected else 0)

    # -- RPC control surface, backing the CRCON services page -----------

    def get_all_child_info(self) -> list[dict]:
        now = time.time()
        with self._lock:
            infos = []
            for name, state in self._children.items():
                infos.append(
                    {
                        "name": name,
                        "group": "worker_launcher",
                        "description": state.spec.description,
                        "statename": state.statename(),
                        "pid": state.pid or 0,
                        "start": state.start_time or 0,
                        "stop": state.stop_time or 0,
                        "now": now,
                        "spawnerr": "",
                        "exitstatus": state.last_exit_code or 0,
                    }
                )
            return infos

    def start_child(self, name: str) -> bool:
        if name not in self._children:
            return False
        with self._lock:
            state = self._children[name]
            already_running = state.pid is not None
            state.enabled = True
            state.restart_count = 0
            state.fatal = False
        self._save_state()
        if not already_running:
            self._spawn(name)
        return True

    def stop_child(self, name: str) -> bool:
        if name not in self._children:
            return False
        with self._lock:
            state = self._children[name]
            state.enabled = False
            pid = state.pid
        self._save_state()
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return True

    def restart_child(self, name: str) -> bool:
        if name not in self._children:
            return False
        with self._lock:
            state = self._children[name]
            state.enabled = True
            state.restart_count = 0
            state.fatal = False
            pid = state.pid
        self._save_state()
        if pid is not None:
            # _handle_child_exit will see enabled=True and respawn it once
            # the SIGTERM is reaped by the main waitpid loop.
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                self._spawn(name)
        else:
            self._spawn(name)
        return True

    def _serve_rpc(self) -> None:
        # Runs in a daemon thread, so an uncaught exception here wouldn't
        # take down the launcher's main loop or any of its children (the
        # 12 game-critical loops are unaffected either way) - but without
        # this retry loop, a transient failure (e.g. the port still in
        # TIME_WAIT from a fast launcher restart) would permanently kill
        # the services page's visibility/control over all 12 children for
        # this launcher's entire lifetime, with nothing but a stderr
        # traceback to show for it.
        while True:
            try:
                server = SimpleXMLRPCServer(
                    ("0.0.0.0", _RPC_PORT), logRequests=False, allow_none=True
                )
                server.register_function(
                    self.get_all_child_info, "getAllChildInfo"
                )
                server.register_function(self.start_child, "startChild")
                server.register_function(self.stop_child, "stopChild")
                server.register_function(self.restart_child, "restartChild")
                server.serve_forever()
            except Exception:
                logger.exception(
                    "worker_launcher: RPC control server failed, "
                    "retrying in %ss",
                    _BACKOFF_SECS,
                )
                time.sleep(_BACKOFF_SECS)
