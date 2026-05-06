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
            self._cleanup(now)
            return False
        timestamps.append(now)
        self._requests[ip] = timestamps
        self._cleanup(now)
        return True

    def _cleanup(self, now: float) -> None:
        """定期清理空列表和过期IP条目，防止内存无限增长。"""
        if len(self._requests) <= 256:
            return
        # 清理空列表
        empty = [ip for ip, ts in self._requests.items() if not ts]
        for ip in empty:
            del self._requests[ip]
        # 如果仍然过大，清理最旧的条目
        if len(self._requests) > 1024:
            # 按最新时间戳排序，保留最近的512个
            sorted_ips = sorted(
                self._requests.items(),
                key=lambda x: x[1][-1] if x[1] else 0,
                reverse=True,
            )
            self._requests = dict(sorted_ips[:512])
