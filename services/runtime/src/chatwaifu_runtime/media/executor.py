"""Bounded media execution helper with admission queue, concurrency cap,
and cooperative cancellation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

MAX_OUTSTANDING_DECODER_JOBS = 2
MAX_ADMISSION_QUEUE_SIZE = 8
DEFAULT_MEDIA_TIMEOUT_SECONDS = 5.0

T = TypeVar("T")


class MediaCancellationToken:
    """Thread-safe cooperative cancellation token passed into synchronous decoder tasks."""

    def __init__(self) -> None:
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def __call__(self) -> bool:
        return self.is_cancelled


@dataclass(eq=False)
class _Admission:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]
    granted: bool = False


class MediaExecutionPool:
    """Bounded reusable execution pool for CPU-bound image decoding, validation, and normalization.

    Guarantees:
    - At most max_jobs (default 2) worker threads running concurrently.
    - A slot remains held by the underlying job until the thread actually terminates,
      even if the awaiting asyncio Task was cancelled.
    - Bounded admission queue (max_queue) that rejects excess requests gracefully.
    - Cooperative cancellation tokens signaled promptly on task cancellation.
    - Non-blocking stopping.
    - Overall deadline includes both queue wait time and worker processing time.
    """

    def __init__(
        self,
        max_jobs: int = MAX_OUTSTANDING_DECODER_JOBS,
        max_queue: int = MAX_ADMISSION_QUEUE_SIZE,
    ) -> None:
        self._max_jobs = max_jobs
        self._max_queue = max_queue
        self._active_jobs = 0
        self._lock = threading.Lock()
        self._queue: list[_Admission] = []
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_jobs,
            thread_name_prefix="cw-media-worker",
        )
        self._stopping = False

    @property
    def is_stopping(self) -> bool:
        with self._lock:
            return self._stopping

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active_jobs

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def _release_slot_locked(self) -> None:
        """Release active slot to next non-cancelled queue waiter or decrement active count."""
        while self._queue:
            admission = self._queue.pop(0)
            if admission.future.done() or admission.loop.is_closed():
                continue
            admission.granted = True
            try:
                admission.loop.call_soon_threadsafe(
                    lambda f=admission.future: f.done() or f.set_result(None)
                )
            except RuntimeError:
                admission.granted = False
                continue
            return
        self._active_jobs -= 1

    def stop(self) -> None:
        """Stop the pool responsiveness without blocking."""
        from chatwaifu_runtime.media.image import MediaInvalidError

        with self._lock:
            self._stopping = True
            while self._queue:
                admission = self._queue.pop(0)
                if not admission.future.done() and not admission.loop.is_closed():
                    admission.loop.call_soon_threadsafe(
                        lambda f=admission.future: (
                            f.done()
                            or f.set_exception(
                                MediaInvalidError("Media processing executor is stopping.")
                            )
                        )
                    )
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def run_bounded(
        self,
        func: Callable[..., T],
        *args: Any,
        timeout_seconds: float = DEFAULT_MEDIA_TIMEOUT_SECONDS,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> T:
        """Run func(*args, deadline=..., check_cancelled=..., **kwargs) in bounded worker thread."""
        from chatwaifu_runtime.media.image import MediaInvalidError

        if self._stopping:
            raise MediaInvalidError("Media processing executor is stopping.")

        loop = asyncio.get_running_loop()
        effective_deadline = (
            deadline if deadline is not None else time.monotonic() + timeout_seconds
        )

        admission: _Admission | None = None
        with self._lock:
            if self._stopping:
                raise MediaInvalidError("Media processing executor is stopping.")
            if self._active_jobs < self._max_jobs:
                self._active_jobs += 1
            else:
                if len(self._queue) >= self._max_queue:
                    raise MediaInvalidError("Media processing busy: admission queue full.")
                admission = _Admission(loop, loop.create_future())
                self._queue.append(admission)

        slot_released = threading.Event()

        def _safe_release_slot() -> None:
            if not slot_released.is_set():
                with self._lock:
                    if not slot_released.is_set():
                        slot_released.set()
                        self._release_slot_locked()

        if admission is not None:
            try:
                time_left = effective_deadline - time.monotonic()
                if time_left <= 0:
                    raise TimeoutError("Media processing timed out waiting in queue.")
                await asyncio.wait_for(asyncio.shield(admission.future), timeout=time_left)
            except BaseException:
                # A grant may have left the queue before its event-loop callback ran.
                # Ownership follows the grant flag, not future.done(). Never release
                # recursively while holding the non-reentrant pool lock.
                with self._lock:
                    if admission in self._queue:
                        self._queue.remove(admission)
                    granted = admission.granted
                admission.future.cancel()
                if granted:
                    _safe_release_slot()
                raise

        cancel_token = MediaCancellationToken()

        def _worker_wrapper() -> T:
            try:
                if time.monotonic() > effective_deadline:
                    raise TimeoutError("Media processing deadline exceeded.")
                if cancel_token.is_cancelled:
                    raise asyncio.CancelledError("Media processing cancelled.")
                return func(
                    *args,
                    deadline=effective_deadline,
                    check_cancelled=cancel_token,
                    **kwargs,
                )
            finally:
                _safe_release_slot()

        try:
            worker_future = self._executor.submit(_worker_wrapper)
        except Exception:
            _safe_release_slot()
            raise

        worker_future.add_done_callback(lambda f: _safe_release_slot() if f.cancelled() else None)

        try:
            time_left = max(0.0001, effective_deadline - time.monotonic())
            afut = asyncio.wrap_future(worker_future, loop=loop)
            return await asyncio.wait_for(afut, timeout=time_left)
        except (asyncio.CancelledError, TimeoutError):
            cancel_token.cancel()
            worker_future.cancel()
            raise


_default_media_pool: MediaExecutionPool | None = None
_default_media_pool_lock = threading.Lock()


def get_default_media_pool() -> MediaExecutionPool:
    global _default_media_pool
    if _default_media_pool is None or _default_media_pool.is_stopping:
        with _default_media_pool_lock:
            if _default_media_pool is None or _default_media_pool.is_stopping:
                _default_media_pool = MediaExecutionPool()
    return _default_media_pool
