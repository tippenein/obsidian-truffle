"""Background app entrypoint — scheduled Obsidian vault context emitter."""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys

from app_runtime.background import BackgroundRunContext, run_background
from truffle.app.background_pb2 import BackgroundContext

from bg_worker import ObsidianBackgroundWorker

logger = logging.getLogger("obsidian.background")
logger.setLevel(logging.INFO)

_worker: ObsidianBackgroundWorker | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _is_verify_mode() -> bool:
    return bool(sys.argv and len(sys.argv) > 1 and "verify" in sys.argv[1].lower())


def _run(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)


def _ensure_worker() -> ObsidianBackgroundWorker:
    global _worker
    if _worker is None:
        _worker = ObsidianBackgroundWorker()
    return _worker


def _submit(ctx: BackgroundRunContext, content: str, priority: int) -> None:
    ctx.bg.submit_context(content=content, uris=[], priority=priority)


def obsidian_ambient(ctx: BackgroundRunContext) -> None:
    worker = _ensure_worker()

    try:
        result = _run(worker.run_cycle())
    except Exception:
        logger.exception("Obsidian background cycle crashed")
        return

    if result.error:
        logger.error("Obsidian background cycle failed: %s", result.error)
        return

    if result.vault_summary:
        _submit(ctx, result.vault_summary, BackgroundContext.PRIORITY_LOW)

    for alert in result.node_alerts:
        alert_type = alert["type"]
        if alert_type == "came_online":
            content = f"Obsidian node online: {alert['node']}"
            _submit(ctx, content, BackgroundContext.PRIORITY_HIGH)
        elif alert_type == "went_offline":
            content = f"Obsidian node offline: {alert['node']}"
            _submit(ctx, content, BackgroundContext.PRIORITY_HIGH)
        elif alert_type == "vault_changed":
            content = (
                f"Vault changed on {alert['node']}: "
                f"{alert['previous_count']} → {alert['current_count']} files"
            )
            _submit(ctx, content, BackgroundContext.PRIORITY_DEFAULT)


def verify() -> int:
    worker = _ensure_worker()
    ok, message = _run(worker.verify())
    if ok:
        logger.info(message)
        return 0
    logger.error(message)
    return 1


def _cleanup() -> None:
    global _loop, _worker
    if _worker is not None:
        try:
            if _loop is not None and not _loop.is_closed():
                _loop.run_until_complete(_worker.close())
        except Exception:
            pass
        _worker = None
    if _loop is not None and not _loop.is_closed():
        _loop.close()
        _loop = None


if __name__ == "__main__":
    atexit.register(_cleanup)
    if _is_verify_mode():
        sys.exit(verify())
    run_background(obsidian_ambient)
