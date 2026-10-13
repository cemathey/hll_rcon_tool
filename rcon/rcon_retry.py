import logging
import time
from collections.abc import Callable

from rcon.connection import HLLServerError

logger = logging.getLogger(__name__)

RCON_RETRY_BACKOFF_SECS = 30


def restart_on_rcon_error(run_fn: Callable[[], None], name: str) -> None:
    """auto_settings, broadcast_loop, routines, expiring_vips, and seed_vip
    are all supervisord-managed `while True:` loops that never caught a
    transient/persistent RCON connectivity failure (raw OSError subclasses
    like ConnectionRefusedError, or HLLServerError for protocol-level
    failures) anywhere in their loop body. The first RCON hiccup killed the
    whole process; supervisord's near-instant respawn (startsecs=1) turned
    that into a tight crash loop pegging a CPU core at 400%+ for as long as
    the game server stayed unreachable — observed live across multiple
    fleet nodes causing sustained high CPU/memory. Restarting the loop
    function itself here, after a pause, means an offline game server
    degrades to "quietly retrying" instead of "burning a CPU core forever,"
    without changing behavior for any other kind of bug (those still
    propagate and crash normally, correctly surfacing real issues).

    run_fn returning on its own (e.g. seed_vip noticing it's disabled) is
    treated as an intentional exit, not a crash — no retry.

    Lives outside rcon.cli so the process supervisor's adapters
    (rcon.process_supervisor.programs) can share it without importing the
    whole CLI."""
    while True:
        try:
            run_fn()
            return
        except (OSError, HLLServerError) as e:
            logger.error(
                "%s: RCON connection error, retrying in %ss: %s",
                name,
                RCON_RETRY_BACKOFF_SECS,
                e,
            )
            time.sleep(RCON_RETRY_BACKOFF_SECS)
