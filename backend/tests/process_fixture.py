"""Small stateful provider used to verify process isolation without loading models."""

import asyncio
import os


class Provider:
    def __init__(self):
        self.count = 0

    async def initialize(self):
        return os.getpid()

    async def detect(self, value):
        await asyncio.sleep(0.05)
        if value == "fail":
            raise ValueError("fixture failure")
        self.count += 1
        return {"pid": os.getpid(), "value": value, "count": self.count}

    async def reset(self):
        self.count = 0

    async def close(self):
        pass
