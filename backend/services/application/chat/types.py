import asyncio
from collections.abc import Callable

TrackTask = Callable[[asyncio.Task], None]
