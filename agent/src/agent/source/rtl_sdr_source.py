"""RTL-SDR hardware source.

Reads IQ samples from a real-time RTL-SDR device via pyrtlsdr.
Converts the device's complex64 output to float32 interleaved bytes
(I0, Q0, I1, Q1, …) suitable for parse_iq.

Requires the 'sdr' optional dependency group:
    pip install -e ".[sdr]"
"""

from __future__ import annotations

import asyncio
import ctypes
from collections.abc import Callable
from typing import Any

import numpy as np

from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    TunerConfig,
)


# pyrtlsdr 0.4.0 resolves several optional symbols (dithering, GPIO control)
# at import time, but they are missing from the librtlsdr shipped by
# Debian/Ubuntu (2.0.2). None of them are used by this agent, so we install
# no-op stubs so the import succeeds. Has no effect on builds whose librtlsdr
# already exports the symbols.
_MISSING_LIBRTLSDR_SYMBOLS = frozenset(
    {
        "rtlsdr_set_dithering",
        "rtlsdr_set_gpio_output",
        "rtlsdr_set_gpio_input",
        "rtlsdr_set_gpio_bit",
        "rtlsdr_get_gpio_bit",
        "rtlsdr_set_gpio_byte",
        "rtlsdr_get_gpio_byte",
        "rtlsdr_set_gpio_status",
    }
)


def _install_missing_symbol_stubs() -> None:
    if getattr(ctypes.CDLL, "_rf_agent_patched", False):
        return

    _original_getattr = ctypes.CDLL.__getattr__

    def _patched_getattr(self: ctypes.CDLL, name: str) -> Any:
        try:
            return _original_getattr(self, name)
        except AttributeError:
            if name not in _MISSING_LIBRTLSDR_SYMBOLS:
                raise
            stub = ctypes.CFUNCTYPE(ctypes.c_int)(lambda *_: 0)
            setattr(self, name, stub)
            return stub

    ctypes.CDLL.__getattr__ = _patched_getattr  # type: ignore[method-assign]
    ctypes.CDLL._rf_agent_patched = True  # type: ignore[attr-defined]


class RTLSDRSource:
    """IQSource backed by a real-time RTL-SDR device.

    Args:
        center_freq_hz:  RF centre frequency in Hz.
        sample_rate_hz:  Sample rate in Hz.
        device_index:    RTL-SDR device index (0 = first found).
        gain:            Tuner gain. ``"auto"`` enables AGC; a float sets
                         manual gain in dB (driver units — see pyrtlsdr docs).
        chunk_samples:   Complex samples per read call. Default: 8192.
        fps:             Target chunks per second. The hardware always streams
                         at ``sample_rate_hz``; we sleep between reads so the
                         agent emits ~``fps`` chunks/sec. Hardware buffer
                         overruns are expected and harmless — we resync on
                         each read. Default: 10.
        _sdr_factory:    Optional callable ``(device_index: int) → sdr_obj``.
                         Injected in tests to replace real hardware.
    """

    def __init__(
        self,
        center_freq_hz: int,
        sample_rate_hz: int,
        device_index: int = 0,
        gain: str | float = "auto",
        chunk_samples: int = 8192,
        fps: float = 10.0,
        _sdr_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self._center_freq_hz = center_freq_hz
        self._sample_rate_hz = sample_rate_hz
        self._device_index = device_index
        self._gain = gain
        self._chunk_samples = chunk_samples
        self._fps = fps
        self._sdr_factory = _sdr_factory
        self._sdr: Any | None = None
        # Set by stop(). When True, the next loop iteration in run() exits
        # cleanly and any error coming out of read_samples is treated as
        # expected shutdown noise (close() racing with read).
        self._stopped = False
        # Backpressure counter: number of frames dropped because the consumer
        # queue was full. Visible for tests and telemetry.
        self.frames_dropped = 0
        # Serializes apply_rf_update against itself; the read loop is allowed
        # to run concurrently — its in-flight chunk during a retune is
        # discarded explicitly via _retune_generation.
        self._retune_lock = asyncio.Lock()
        # Incremented on every successful apply_rf_update; the read loop tags
        # each launched read with the generation it started under so the
        # in-flight chunk that straddled a retune can be dropped.
        self._retune_generation = 0
        self._descriptor = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=center_freq_hz,
            normalize=True,
            dc_offset_remove=True,
        )

    @property
    def descriptor(self) -> IQDescriptor:
        return self._descriptor

    async def start(self) -> None:
        """Open and configure the RTL-SDR device."""
        if self._sdr_factory is not None:
            sdr = self._sdr_factory(self._device_index)
        else:
            try:
                _install_missing_symbol_stubs()
                from rtlsdr import RtlSdr
            except ImportError as exc:
                raise ImportError(
                    "pyrtlsdr is required for RTL-SDR support. "
                    "Install it with: pip install -e '.[sdr]'"
                ) from exc
            sdr = RtlSdr(self._device_index)

        sdr.sample_rate = self._sample_rate_hz
        sdr.center_freq = self._center_freq_hz
        if self._gain == "auto":
            sdr.gain = "auto"
        else:
            sdr.gain = float(self._gain)

        self._sdr = sdr

    async def stop(self) -> None:
        """Close the RTL-SDR device. Idempotent — safe to call multiple times.

        Side-effects:
          - sets ``_stopped`` so the next ``run()`` iteration exits cleanly,
          - closes the underlying device,
          - clears ``_sdr`` so subsequent calls are no-ops.

        Closing the device while ``run_in_executor`` is mid-``read_samples``
        typically causes libusb to return an error from the worker thread;
        ``run()`` recognises ``_stopped`` and swallows that error rather than
        propagating it.
        """
        self._stopped = True
        sdr = self._sdr
        if sdr is None:
            return
        self._sdr = None
        try:
            sdr.close()
        except Exception:
            # The device may already be in an error state (USB removed,
            # double-close, etc.). We don't want a cleanup error to mask the
            # actual shutdown cause.
            pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        """Read chunks from the device and push float32 interleaved bytes to output.

        pyrtlsdr returns complex values already in the [-1.0, 1.0] range.
        We convert to complex64 then reinterpret as float32 to get the
        canonical [I0, Q0, I1, Q1, …] layout.

        Shutdown semantics:
          - On ``asyncio.CancelledError`` we re-raise so the runner can shut
            down deterministically. We do NOT swallow cancellation.
          - On any non-cancellation exception, we propagate it unless
            ``stop()`` was called concurrently — in that case the exception
            is treated as expected close-race noise and we return cleanly.
          - The runner is responsible for calling ``stop()`` after ``run()``
            exits (whether via return, raise, or cancellation).

        Backpressure: emission uses ``put_nowait``. If the consumer queue is
        full we drop the oldest pending frame before enqueueing the newest
        one (latest-frame-wins). For real-time RF, fresh data beats stale.
        ``self.frames_dropped`` tracks this for telemetry.

        Raises ``asyncio.CancelledError`` on cancellation.
        """
        if self._sdr is None:
            raise RuntimeError("call start() before run()")

        loop = asyncio.get_running_loop()
        chunk = self._chunk_samples
        sleep_per_chunk = 1.0 / self._fps if self._fps > 0 else 0.0

        while not self._stopped:
            sdr = self._sdr
            if sdr is None:
                # stop() raced with this iteration after the while-check.
                return
            read_generation = self._retune_generation
            try:
                complex_samples = await loop.run_in_executor(
                    None, sdr.read_samples, chunk
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # If stop() ran concurrently, close()-ing the SDR makes
                # read_samples error out — that's expected. Otherwise the
                # error is real and the runner should see it.
                if self._stopped:
                    return
                raise

            if self._stopped:
                return

            # Drop the in-flight chunk if a retune occurred while we were
            # blocked on read_samples — those samples were captured at the
            # pre-retune RF config and don't belong to the new generation.
            if read_generation != self._retune_generation:
                continue

            arr = np.asarray(complex_samples, dtype=np.complex64)
            self._emit(output, arr.view(np.float32).tobytes())

            if sleep_per_chunk > 0:
                await asyncio.sleep(sleep_per_chunk)

    async def apply_rf_update(self, rf: RFConfig, tuner: TunerConfig | None) -> None:
        """Live-retune the SDR. Only center_freq, sample_rate, and gain are
        meaningful for the source; fft_size / window_fn are handled by the
        FFT processor and are ignored here.
        """
        async with self._retune_lock:
            sdr = self._sdr
            if sdr is None:
                raise RuntimeError("cannot retune before start() or after stop()")

            sdr.sample_rate = rf.sample_rate_hz
            sdr.center_freq = rf.center_freq_hz
            if tuner is not None:
                if tuner.agc:
                    sdr.gain = "auto"
                    self._gain = "auto"
                elif tuner.gain_db is not None:
                    sdr.gain = float(tuner.gain_db)
                    self._gain = float(tuner.gain_db)
                # tuner.agc=False with gain_db=None: leave gain unchanged.

            self._sample_rate_hz = rf.sample_rate_hz
            self._center_freq_hz = rf.center_freq_hz
            self._descriptor = IQDescriptor(
                sample_format=SampleFormat.FLOAT32,
                endianness=Endianness.LITTLE,
                layout=Layout.INTERLEAVED,
                sample_rate_hz=rf.sample_rate_hz,
                center_freq_hz=rf.center_freq_hz,
                normalize=True,
                dc_offset_remove=True,
            )
            self._retune_generation += 1

    def _emit(self, output: asyncio.Queue[bytes], buf: bytes) -> None:
        """Push ``buf`` into ``output`` without blocking the hardware loop.

        If the queue is full, drop the oldest pending item to make room
        (latest-frame-wins). The drop is bounded to a single retry in case
        of races between consumers and producers.
        """
        try:
            output.put_nowait(buf)
            return
        except asyncio.QueueFull:
            pass
        # Queue was full: drop the oldest, then enqueue. On the (very rare)
        # race where a consumer drained the queue between QueueFull and the
        # get_nowait below, we just fall through to put_nowait and succeed.
        try:
            output.get_nowait()
            self.frames_dropped += 1
        except asyncio.QueueEmpty:
            pass
        try:
            output.put_nowait(buf)
        except asyncio.QueueFull:
            # Another producer raced ahead. Count this as a drop and move on
            # rather than spin: the next chunk will get another shot.
            self.frames_dropped += 1
