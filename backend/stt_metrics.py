"""One-use receipts for browser release-to-final-transcript measurements."""

from collections import OrderedDict
from math import isfinite
from time import monotonic
import uuid

from diagnostics import measure, scope


STT_TIMING_BOUNDARY = "browser_release_to_final_transcript"


class TranscriptMeasurements:
    def __init__(self, *, limit=512, ttl_seconds=180, clock=monotonic):
        self.pending = OrderedDict()
        self.limit = limit
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def _expire(self):
        cutoff = self.clock() - self.ttl_seconds
        while self.pending and next(iter(self.pending.values()))[0] <= cutoff:
            self.pending.popitem(last=False)

    def issue(self, trace, **metadata):
        self._expire()
        while len(self.pending) >= self.limit:
            self.pending.popitem(last=False)
        identifier = uuid.uuid4().hex
        self.pending[identifier] = (self.clock(), dict(trace), metadata)
        return identifier

    def receive(self, identifier, elapsed_ms):
        if not isinstance(elapsed_ms, (int, float)) or isinstance(elapsed_ms, bool) or not isfinite(elapsed_ms) or elapsed_ms < 0:
            return False
        self._expire()
        pending = self.pending.pop(identifier, None)
        if pending is None:
            return False
        _, trace, metadata = pending
        with scope(**trace):
            measure("stt_completion_ms", elapsed_ms, timing_boundary=STT_TIMING_BOUNDARY,
                    measurement_source="browser", **metadata)
        return True
