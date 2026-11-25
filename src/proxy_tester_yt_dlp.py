from collections import Counter
import concurrent.futures
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import re
import time
from urllib.error import URLError
import yt_dlp

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ProxyTester")

TIMESTAMP_COMMENT_RE = re.compile(r"^#\s*Generated\s+UTC:\s*(\S+)\s*$")


class YTDLPInterceptor:
    """Silent logger interceptor to suppress noisy yt-dlp terminal output."""

    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class ProxyTester:
    """Test proxy speed and throughput stability against YouTube using yt-dlp."""

    def __init__(
        self,
        proxy_file: str = "input/media/yt/fast_proxies.txt",
        output_proxies_only: str = "input/media/yt/dl_proxies.txt",
        output_with_score: str = "input/media/yt/dl_proxies_with_Score.txt",
        target_url: str = "https://www.youtube.com/shorts/1CP8RKL_Iqc",
        target_count: int = 20,
        max_workers: int = 10,
        timeout_seconds: int = 8,
        max_file_age_hours: float = 2.5,
        sample_bytes: int = 3 * 1024 * 1024,
        min_avg_kbps: float = 500.0,
        max_cv: float = 1.25,
    ):
        self.proxy_file = Path(proxy_file)
        self.output_proxies_only = Path(output_proxies_only)
        self.output_with_score = Path(output_with_score)
        self.target_url = target_url
        self.target_count = max(1, target_count)
        self.max_workers = max(1, min(max_workers, 20))
        self.timeout_seconds = max(3, timeout_seconds)
        self.max_file_age_hours = max(0.0, max_file_age_hours)
        self.sample_bytes = max(512 * 1024, sample_bytes)
        self.min_avg_kbps = max(50.0, min_avg_kbps)
        self.max_cv = max(0.2, max_cv)
        self._failure_stats = Counter()

        try:
            self.output_proxies_only.parent.mkdir(parents=True, exist_ok=True)
            self.output_with_score.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(f"Failed to create output directories: {exc}")
            raise

    @staticmethod
    def _build_timestamp_header() -> str:
        """Build a UTC timestamp comment line representing when the output file was generated."""
        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return f"# Generated UTC: {now_utc}"

    def _get_comment_age_hours(self, filepath: Path) -> float | None:
        """Parse creation UTC timestamp from the first comment line and return its age in hours."""
        if not filepath.exists():
            return None

        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
            if not lines:
                return None

            match = TIMESTAMP_COMMENT_RE.match(lines[0].strip())
            if not match:
                logger.info(f"Missing timestamp comment in {filepath}. Cache skip disabled for this file.")
                return None

            generated_at = datetime.fromisoformat(match.group(1))
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)

            return (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
        except (OSError, ValueError) as exc:
            logger.warning(f"Failed parsing timestamp comment from {filepath}: {exc}")
            return None

    def _validate_environment(self) -> bool:
        """Verify that yt_dlp is importable and ready."""
        try:
            version = getattr(yt_dlp, "__version__", "Installed")
            logger.info(f"yt-dlp library loaded successfully (version: {version})")
            return True
        except Exception as exc:
            logger.error(f"Unexpected error while validating yt-dlp environment: {exc}")
            return False

    def _is_cache_fresh(self) -> bool:
        """Return True only if both output files have valid timestamp comments younger than max_file_age_hours."""
        proxies_age = self._get_comment_age_hours(self.output_proxies_only)
        score_age = self._get_comment_age_hours(self.output_with_score)

        if proxies_age is None or score_age is None:
            return False

        if proxies_age < self.max_file_age_hours and score_age < self.max_file_age_hours:
            logger.info(
                f"Fresh output files detected by timestamp header "
                f"(Proxies age: {round(proxies_age * 60, 2)} mins, "
                f"Score age: {round(score_age * 60, 2)} mins). Skipping execution."
            )
            return True

        return False

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """Categorize failure reasons concisely for summary logging."""
        msg = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timed out" in msg or "timeout" in msg:
            return "timeout"
        if isinstance(exc, URLError) or "proxy" in msg or "connection" in msg or "reset" in msg:
            return "proxy_or_network"
        if "sign in" in msg or "bot" in msg or "captcha" in msg or "429" in msg:
            return "challenge_or_ratelimit"
        if "unstable_speed" in msg:
            return "unstable_speed"
        return "other"

    @staticmethod
    def _calc_speed_metrics(samples_kbps: list[float]) -> tuple[float, float]:
        """Compute mean speed (KB/s) and coefficient of variation (std / mean) for jitter evaluation."""
        if not samples_kbps:
            return 0.0, float("inf")
        mean_val = sum(samples_kbps) / len(samples_kbps)
        if mean_val <= 0:
            return 0.0, float("inf")
        variance = sum((x - mean_val) ** 2 for x in samples_kbps) / len(samples_kbps)
        cv = math.sqrt(variance) / mean_val
        return mean_val, cv

    def _test_single_proxy(self, proxy: str) -> tuple[str, float] | None:
        """Test proxy download speed and stability by streaming a bounded byte sample."""
        proxy = proxy.strip()
        if not proxy or proxy.startswith("#"):
            return None

        speed_samples_kbps: list[float] = []
        downloaded_bytes = 0
        stop_flag = {"stop": False}

        def progress_hook(d: dict):
            nonlocal downloaded_bytes
            if stop_flag["stop"]:
                raise RuntimeError("Sample quota reached")

            status = d.get("status")
            if status == "downloading":
                downloaded_bytes = d.get("downloaded_bytes", downloaded_bytes)
                spd = d.get("speed")
                if spd and spd > 0:
                    speed_samples_kbps.append(float(spd) / 1024.0)

                if downloaded_bytes >= self.sample_bytes:
                    stop_flag["stop"] = True
                    raise RuntimeError("Sample quota reached")

        ydl_opts = {
            "proxy": proxy,
            "socket_timeout": self.timeout_seconds,
            "retries": 1,
            "fragment_retries": 1,
            "skip_download": False,
            "quiet": True,
            "no_warnings": True,
            "logger": YTDLPInterceptor(),
            "progress_hooks": [progress_hook],
            "outtmpl": "-",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

        start_time = time.monotonic()
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.target_url])
        except RuntimeError as exc:
            if "Sample quota reached" not in str(exc):
                self._failure_stats[self._classify_error(exc)] += 1
                return None
        except Exception as exc:
            self._failure_stats[self._classify_error(exc)] += 1
            return None

        elapsed = max(time.monotonic() - start_time, 0.001)
        if downloaded_bytes <= 0:
            self._failure_stats["other"] += 1
            return None

        overall_kbps = (downloaded_bytes / 1024.0) / elapsed
        mean_kbps, cv = self._calc_speed_metrics(speed_samples_kbps)
        effective_kbps = mean_kbps if speed_samples_kbps else overall_kbps

        if effective_kbps < self.min_avg_kbps or cv > self.max_cv:
            self._failure_stats["unstable_speed"] += 1
            logger.debug(
                f"REJECTED: {proxy} (avg={effective_kbps:.1f}KB/s, cv={cv:.2f}, elapsed={elapsed:.2f}s)"
            )
            return None

        # Higher score for faster average speed and lower jitter (cv)
        stability_factor = 1.0 / (1.0 + cv)
        speed_score = round((effective_kbps / 100.0) * stability_factor, 2)
        logger.info(
            f"SUCCESS: {proxy} | avg={effective_kbps:.1f}KB/s | cv={cv:.2f} | Score={speed_score}"
        )
        return (proxy, speed_score)

    def run(self):
        """Run proxy speed and stability testing unless fresh timestamped outputs exist."""
        logger.info("Initializing Proxy Speed & Stability Tester...")
        self._failure_stats.clear()

        if not self._validate_environment():
            logger.error("Environment validation failed. Aborting execution.")
            return

        if self._is_cache_fresh():
            logger.info("Skipping proxy testing workflow because fresh timestamped output files exist.")
            return

        if not self.proxy_file.exists():
            logger.error(f"Proxy file not found at path: {self.proxy_file}")
            return

        try:
            seen = set()
            proxies = []
            for raw_line in self.proxy_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if line and not line.startswith("#") and line not in seen:
                    seen.add(line)
                    proxies.append(line)
        except OSError as exc:
            logger.error(f"Failed to read proxy file {self.proxy_file}: {exc}")
            return

        if not proxies:
            logger.warning(f"No valid proxies found in {self.proxy_file}")
            return

        logger.info(
            f"Loaded {len(proxies)} unique proxies. Starting test "
            f"(workers={self.max_workers}, timeout={self.timeout_seconds}s, sample={self.sample_bytes // 1024}KB)..."
        )

        fast_proxies: list[tuple[str, float]] = []
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_proxy = {
            executor.submit(self._test_single_proxy, proxy): proxy for proxy in proxies
        }

        try:
            for future in concurrent.futures.as_completed(future_to_proxy):
                try:
                    res = future.result(timeout=self.timeout_seconds + 6)
                    if res:
                        proxy, score = res
                        fast_proxies.append((proxy, score))

                        if len(fast_proxies) >= self.target_count:
                            logger.info(
                                f"Target count of {self.target_count} working proxies reached. Stopping early..."
                            )
                            for pending_future in future_to_proxy:
                                pending_future.cancel()
                            break
                except TimeoutError:
                    self._failure_stats["timeout"] += 1
                except concurrent.futures.CancelledError:
                    continue
                except Exception as exc:
                    self._failure_stats[self._classify_error(exc)] += 1
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if self._failure_stats:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(self._failure_stats.items()))
            logger.info(f"Failure breakdown: {summary}")

        if not fast_proxies:
            logger.warning("No working proxies met the speed and stability criteria. Output files not written.")
            return

        fast_proxies.sort(key=lambda x: x[1], reverse=True)
        header = self._build_timestamp_header()

        try:
            proxies_lines = [header, *[proxy for proxy, _ in fast_proxies]]
            self.output_proxies_only.write_text("\n".join(proxies_lines) + "\n", encoding="utf-8")
            logger.info(f"Successfully saved {len(fast_proxies)} proxies to: {self.output_proxies_only}")
        except OSError as exc:
            logger.error(f"Failed to write to {self.output_proxies_only}: {exc}")

        try:
            score_lines = [header, *[f"{proxy} | Score: {score}" for proxy, score in fast_proxies]]
            self.output_with_score.write_text("\n".join(score_lines) + "\n", encoding="utf-8")
            logger.info(f"Successfully saved {len(fast_proxies)} proxies with scores to: {self.output_with_score}")
        except OSError as exc:
            logger.error(f"Failed to write to {self.output_with_score}: {exc}")


if __name__ == "__main__":
    tester = ProxyTester(
        target_count=50,
        max_workers=50,
        timeout_seconds=5,
        max_file_age_hours=3.5,
        sample_bytes=3 * 1024 * 1024,
        min_avg_kbps=600.0,
        max_cv=1.15,
    )
    tester.run()