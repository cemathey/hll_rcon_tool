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
    start_time: float | None = None
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
    return [
        ChildSpec(
            "broadcasts",
            cli.run_broadcast_loop.callback,
            {"LOGGING_FILENAME": f"broadcasts_{n}.log"},
            startretries=100,
            startsecs=1,
            autorestart="always",
            description="Broadcasts server messages",
        ),
        ChildSpec(
            "expiring_vips",
            cli.run_expiring_vips.callback,
            {"LOGGING_FILENAME": f"expiring_vips_{n}.log"},
            startretries=10,
            startsecs=0,
            autorestart="always",
        ),
        ChildSpec(
            "seed_vip",
            cli.run_seed_vip.callback,
            {"LOGGING_FILENAME": f"seed_vip_{n}.log"},
            startretries=5,
            startsecs=1,
            autorestart="unexpected",
        ),
        ChildSpec(
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
        ChildSpec(
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
        ChildSpec(
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
        ChildSpec(
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
        ChildSpec(
            "routines",
            cli.run_routines.callback,
            {"LOGGING_FILENAME": f"routines_{n}.log"},
            startretries=100,
            startsecs=1,
            autorestart="always",
        ),
        ChildSpec(
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
        ChildSpec(
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
        ChildSpec(
            "blacklists",
            cli.run_blacklists.callback,
            {"LOGGING_FILENAME": f"blacklists_{n}.log"},
            startretries=100,
            startsecs=10,
            autorestart="always",
        ),
        ChildSpec(
            "watch_killrate",
            cli.watch_killrate.callback,
            {"LOGGING_FILENAME": f"watch_killrate_{n}.log"},
            startretries=5,
            startsecs=0,
            autorestart="unexpected",
        ),
    ]


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
    """
    import rcon.models as models

    old_engine = models._ENGINE
    models._ENGINE = None
    if old_engine is not None:
        old_engine.dispose(close=False)

    # redis-py (8.1.0) already self-heals across fork: ConnectionPool
    # checks pid on every get_connection() and resets itself if it
    # changed. This is kept anyway as cheap, explicit defense-in-depth
    # rather than relying on that undocumented-to-us internal behavior.
    import rcon.cache_utils as cache_utils

    for pool in (cache_utils._REDIS_POOL, cache_utils._GLOBAL_REDIS_POOL):
        if pool is not None:
            pool.reset()


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
        pid = os.fork()
        if pid == 0:
            # Child branch. Must never fall through to normal interpreter
            # shutdown (atexit hooks / buffer flushes would then run in
            # both parent and child) — always os._exit().
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
                state.start_time = time.monotonic()
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
            time.monotonic() - state.start_time if state.start_time else 0
        )
        exited_cleanly = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

        with self._lock:
            state.pid = None
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

        self._load_state()
        for name, state in self._children.items():
            if state.enabled:
                self._spawn(name)

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

        try:
            while True:
                pid, status = os.waitpid(-1, 0)
                self._handle_child_exit(pid, status)
        except _ShutdownRequested:
            self._graceful_shutdown()
        except ChildProcessError:
            # No children left to wait on (e.g. all disabled at startup).
            self._graceful_shutdown()

    def _graceful_shutdown(self) -> None:
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
        sys.exit(0)

    # -- RPC control surface, backing the CRCON services page -----------

    def get_all_child_info(self) -> list[dict]:
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
                        "stop": 0,
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
        server = SimpleXMLRPCServer(
            ("0.0.0.0", _RPC_PORT), logRequests=False, allow_none=True
        )
        server.register_function(self.get_all_child_info, "getAllChildInfo")
        server.register_function(self.start_child, "startChild")
        server.register_function(self.stop_child, "stopChild")
        server.register_function(self.restart_child, "restartChild")
        server.serve_forever()
