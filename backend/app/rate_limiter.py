"""IP级滑动窗口限流器。"""

import time


class RateLimiter:
    """基于滑动窗口的内存限流器。"""

    def __init__(self, max_requests: int, window_seconds: int):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        timestamps = self._requests.get(ip, [])
        # 过滤窗口外的请求
        timestamps = [t for t in timestamps if now - t < self._window]
        if len(timestamps) >= self._max:
            self._requests[ip] = timestamps
            return False
        timestamps.append(now)
        self._requests[ip] = timestamps
        return True
