"""
Adaptive Network-Aware AI Task Manager
"""

import time
import socket
import threading
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import Enum
from typing import List, Callable, Any, Tuple


class NetworkTier(Enum):
    OFFLINE = "offline"
    POOR = "poor"
    MODERATE = "moderate"
    GOOD = "good"
    EXCELLENT = "excellent"


@dataclass
class NetworkStats:
    latency_ms: float
    bandwidth_mbps: float
    tier: NetworkTier


class NetworkMonitor:
    def __init__(
        self,
        test_urls: List[str] = None,
        host_check: Tuple[str, int] = ("8.8.8.8", 53),
        timeout: float = 3.0,
    ):
        """
        :param test_urls: List of URLs to test bandwidth (first working one is used).
                         Defaults to a sensible list.
        :param host_check: (host, port) for connectivity and latency tests.
        :param timeout: Timeout in seconds for network operations.
        """
        if test_urls is None:
            test_urls = [
                "http://speedtest.ftp.otenet.gr/files/test1Mb.db",
                "http://ipv4.download.thinkbroadband.com/1MB.zip",
                "http://speedtest.tele2.net/1MB.zip",
            ]
        self.test_urls = test_urls
        self.host_check = host_check
        self.timeout = timeout

    def is_online(self) -> bool:
        """Check connectivity by opening a socket to the host_check."""
        try:
            with socket.create_connection(self.host_check, timeout=self.timeout):
                pass
            return True
        except OSError:
            return False

    def measure_latency(self, samples: int = 3) -> float:
        """Measure average round‑trip latency to host_check (milliseconds)."""
        latencies = []
        for _ in range(samples):
            try:
                start = time.perf_counter()
                with socket.create_connection(self.host_check, timeout=self.timeout):
                    pass
                latencies.append((time.perf_counter() - start) * 1000)
            except OSError:
                continue
        return statistics.mean(latencies) if latencies else float("inf")

    def measure_bandwidth(self) -> float:
        """Download from the first working test URL and return Mbps. Cached for 5 minutes."""
        now = time.time()
        if hasattr(self, '_last_bandwidth_time') and (now - self._last_bandwidth_time) < 300:
            return self._last_bandwidth_val
        
        for url in self.test_urls:
            try:
                start = time.perf_counter()
                with urllib.request.urlopen(url, timeout=self.timeout * 3) as resp:
                    # Read up to 2 MB – enough for a meaningful measurement
                    data = resp.read(2_000_000)
                elapsed = time.perf_counter() - start
                if elapsed <= 0:
                    continue
                size_bits = len(data) * 8
                mbps = (size_bits / elapsed) / 1_000_000
                # Ignore unrealistically low results (e.g., tiny files)
                if mbps > 0.01:  # at least 10 kbps
                    self._last_bandwidth_time = now
                    self._last_bandwidth_val = mbps
                    return mbps
            except Exception:
                continue
                
        self._last_bandwidth_time = now
        self._last_bandwidth_val = 0.0
        return 0.0

    def get_stats(self) -> NetworkStats:
        """Perform a full network check and return current statistics."""
        if not self.is_online():
            return NetworkStats(
                latency_ms=float("inf"), bandwidth_mbps=0.0, tier=NetworkTier.OFFLINE
            )
        latency = self.measure_latency()
        bandwidth = self.measure_bandwidth()
        tier = self._classify(latency, bandwidth)
        return NetworkStats(latency, bandwidth, tier)

    @staticmethod
    def _classify(latency_ms: float, bandwidth_mbps: float) -> NetworkTier:
        """
        Classify network quality based on latency and bandwidth.
        If bandwidth test failed (<=0) but latency is finite, assume MODERATE.
        """
        if latency_ms == float("inf"):
            return NetworkTier.OFFLINE
        if bandwidth_mbps <= 0:
            return NetworkTier.MODERATE
        if bandwidth_mbps < 1 or latency_ms > 600:
            return NetworkTier.POOR
        if bandwidth_mbps < 5 or latency_ms > 300:
            return NetworkTier.MODERATE
        if bandwidth_mbps < 20 or latency_ms > 100:
            return NetworkTier.GOOD
        return NetworkTier.EXCELLENT


@dataclass
class WorkProfile:
    concurrency: int
    batch_size: int
    request_timeout: int
    retry_attempts: int
    response_quality: str
    use_cache_first: bool


PROFILES = {
    NetworkTier.OFFLINE: WorkProfile(0, 1, 5, 0, "cache-only", True),
    NetworkTier.POOR: WorkProfile(1, 1, 20, 5, "text-only", True),
    NetworkTier.MODERATE: WorkProfile(2, 4, 12, 3, "compressed", True),
    NetworkTier.GOOD: WorkProfile(4, 8, 8, 2, "compressed", False),
    NetworkTier.EXCELLENT: WorkProfile(8, 16, 5, 1, "full", False),
}


class AdaptiveAIController:
    def __init__(self, poll_interval: float = 30.0):
        self.monitor = NetworkMonitor()
        self.poll_interval = poll_interval
        self._stats: NetworkStats = self.monitor.get_stats()
        self._profile: WorkProfile = PROFILES[self._stats.tier]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._executor = ThreadPoolExecutor(max_workers=8)  # for run_task timeouts

    def start(self):
        """Start the background network monitoring thread."""
        self._thread.start()

    def stop(self):
        """Stop the background thread and shut down the executor."""
        self._stop.set()
        self._executor.shutdown(wait=False)

    def _poll_loop(self):
        """Background loop: update network stats and profile periodically."""
        while not self._stop.is_set():
            try:
                stats = self.monitor.get_stats()
                with self._lock:
                    self._stats = stats
                    self._profile = PROFILES[stats.tier]
            except Exception as e:
                # Log error (print for simplicity; in production use logging)
                print(f"Network poll error: {e}")
            # Wait for next poll, but break immediately if stop is set
            self._stop.wait(self.poll_interval)

    def get_current_profile(self) -> WorkProfile:
        """Thread‑safe getter for the current work profile."""
        with self._lock:
            return self._profile

    def get_current_stats(self) -> NetworkStats:
        """Thread‑safe getter for the current network statistics."""
        with self._lock:
            return self._stats

    def run_task(self, fn: Callable, *args, **kwargs) -> Any:
        """
        Execute a task with the current network‑aware profile.

        The task is run with a timeout derived from the profile, and retried
        on failure. If the network is offline (concurrency == 0), raises
        ConnectionError.

        :param fn: Callable to execute.
        :param args: Positional arguments for fn.
        :param kwargs: Keyword arguments for fn.
        :return: The result of fn.
        :raises ConnectionError: if offline.
        :raises Exception: the last exception from retries.
        """
        profile = self.get_current_profile()
        if profile.concurrency == 0:
            raise ConnectionError("Network offline — task deferred to cache/queue.")

        timeout = profile.request_timeout
        last_err = None

        for attempt in range(1, profile.retry_attempts + 1):
            future = self._executor.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=timeout)
            except FutureTimeoutError as e:
                future.cancel()
                last_err = TimeoutError(f"Task timed out after {timeout}s")
            except Exception as e:
                last_err = e
                # For non‑timeout errors, do not cancel (already done)
            # Wait before retry with exponential backoff
            if attempt < profile.retry_attempts:
                time.sleep(min(2 ** attempt, 10))

        raise last_err or RuntimeError("Task failed without specific exception")