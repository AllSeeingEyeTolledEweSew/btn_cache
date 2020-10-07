from __future__ import annotations

import unittest.mock
from typing import List
from typing import Optional
from typing import SupportsFloat


class WaitForever(Exception):

    pass


class MockTime:
    """A class to assist with mocking time functions for testing.

    mock_time() returns an instance of TimeMocker as part of the context
    manager protocol.

    The attributes can be changed at any time to change the passage of time
    while time functions are mocked.

    Attributes:
        time: The current time, in seconds since epoch.
        autoincrement: The amount of time to automatically increment time
            whenever time functions are called.
    """
    def __init__(self, time: SupportsFloat, autoincrement: SupportsFloat = 0):
        self._time = float(time)
        self._monotonic = 0.0
        self._autoincrement = float(autoincrement)
        assert self._autoincrement >= 0
        self._patches: List[unittest.mock._patch] = []
        self._started = False

    def get_mock_time(self) -> float:
        return self._time

    def get_mock_monotonic(self) -> float:
        return self._monotonic

    def time(self) -> float:
        """Mock version of time.time()."""
        self.sleep(0)
        return self._time

    def monotonic(self) -> float:
        """Mock version of time.monotonic()."""
        self.sleep(0)
        return self._monotonic

    def wait(self, timeout=Optional[SupportsFloat]) -> bool:
        if timeout is None:
            raise WaitForever()
        self.sleep(timeout)
        return False

    def sleep(self, time: SupportsFloat) -> None:
        """Mock version of time.sleep()."""
        increment = float(time) + self._autoincrement
        self._time += increment
        self._monotonic += increment

    def patch(self, *args, **kwargs):
        return self.add_patch(unittest.mock.patch(*args, **kwargs))

    def patch_object(self, *args, **kwargs):
        return self.add_patch(unittest.mock.patch.object(*args, **kwargs))

    def patch_dict(self, *args, **kwargs):
        return self.add_patch(unittest.mock.patch.dict(*args, **kwargs))

    def add_patch(self, patch):
        if self._started:
            patch.start()
        self._patches.append(patch)
        return patch

    def __enter__(self) -> MockTime:
        """Returns itself after enabling all time function patches."""
        self._started = True
        self.patch("time.time", new=self.time)
        self.patch("time.monotonic", new=self.monotonic)
        self.patch("time.sleep", new=self.sleep)
        return self

    def __exit__(self, *exc_info) -> None:
        """Disables all time function patches."""
        for patch in reversed(self._patches):
            patch.stop()
        self._started = False
