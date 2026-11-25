from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
import requests

TIMESTAMP_COMMENT_RE = re.compile(r"^#\s*Generated\s+UTC:\s*(\S+)\s*$")


def build_timestamp_header() -> str:
    """Build a UTC timestamp comment line representing when the tested proxies file was generated."""
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"# Generated UTC: {now_utc}"


def is_cache_fresh_by_comment(filepath: str | Path, max_age_hours: float) -> bool:
    """Return True only if the file exists, has a valid timestamp comment, and is younger than max_age_hours."""
    path = Path(filepath)
    if not path.exists():
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return False

        match = TIMESTAMP_COMMENT_RE.match(lines[0].strip())
        if not match:
            print(f"[!] Missing timestamp comment in {path}. Skipping cache hit.")
            return False

        generated_at = datetime.fromisoformat(match.group(1))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)

        age_hours = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
        if age_hours < max_age_hours:
            print(
                f"[!] Fast proxies file is fresh based on header (age: {round(age_hours, 2)}h < {max_age_hours}h). Skipping proxy testing."
            )
            return True

        print(f"[*] Fast proxies file expired based on header (age: {round(age_hours, 2)}h >= {max_age_hours}h).")
        return False
    except (OSError, ValueError) as exc:
        print(f"[-] Failed to read timestamp comment from {path}: {exc}")
        return False


def _read_proxy_lines(filepath: str | Path) -> list[str]:
    """Read non-empty, non-comment lines from a proxy file."""
    path = Path(filepath)
    try:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError as exc:
        print(f"[-] Failed reading proxies from {path}: {exc}")
        return []


def test_single_proxy(proxy_str: str, test_url: str = "https://www.youtube.com", timeout: float = 5):
    """Test a single proxy against test_url and return its latency if within timeout."""
    if not proxy_str.startswith(("http://", "https://", "socks4://", "socks5://")):
        full_proxy = f"http://{proxy_str}"
    else:
        full_proxy = proxy_str

    proxies = {
        "http": full_proxy,
        "https": full_proxy,
    }

    start_time = time.time()
    try:
        response = requests.head(test_url, proxies=proxies, timeout=timeout)
        if response.status_code < 400:
            latency = time.time() - start_time
            if latency <= timeout:
                return {"proxy": proxy_str, "latency": round(latency, 3)}
    except Exception:
        pass

    return None


def test_and_sort_proxies(
    input_file: str = "input/media/yt/proxies.txt",
    output_file: str = "input/media/yt/fast_proxies.txt",
    max_workers: int = 250,
    timeout: float = 5,
    target_count: int = 500,
    max_age_hours: float = 2.5,
    force: bool = False,
):
    """Test proxies in small batches, stop early once target_count is met, and skip if header timestamp is fresh."""
    if not force and is_cache_fresh_by_comment(output_file, max_age_hours):
        cached_proxies = _read_proxy_lines(output_file)
        if cached_proxies:
            return cached_proxies[:target_count]
        print("[!] Timestamp comment was fresh, but no fast proxies found inside file. Re-testing...")

    proxies_list = list(dict.fromkeys(_read_proxy_lines(input_file)))
    if not proxies_list:
        print("[!] No proxies found to test.")
        return []

    workers = max(1, min(max_workers, len(proxies_list)))
    print(f"[*] Testing proxies in batches of {workers}... Target: Get first {target_count} working proxies.")
    valid_proxies = []
    target_reached = False

    try:
        for i in range(0, len(proxies_list), workers):
            batch = proxies_list[i : i + workers]
            executor = ThreadPoolExecutor(max_workers=len(batch))
            try:
                future_to_proxy = {
                    executor.submit(test_single_proxy, proxy, "https://www.youtube.com", timeout): proxy
                    for proxy in batch
                }

                for future in as_completed(future_to_proxy):
                    if len(valid_proxies) >= target_count:
                        target_reached = True
                        break

                    try:
                        result = future.result()
                    except Exception:
                        result = None

                    if result:
                        valid_proxies.append(result)
                        print(
                            f"[+] Working Proxy Found: {result['proxy']} | Speed: {result['latency']}s "
                            f"(Total: {len(valid_proxies)}/{target_count})"
                        )

                        if len(valid_proxies) >= target_count:
                            print(f"\n[✨] Reached target of {target_count} fast proxies! Stopping test early.")
                            target_reached = True
                            break
            finally:
                # لا ننتظر المهام المتبقية بعد الوصول للهدف لتوفير الوقت وموارد الـ runner
                executor.shutdown(wait=not target_reached, cancel_futures=True)

            if target_reached:
                break
    except Exception as exc:
        print(f"[-] Unexpected error during proxy batch testing: {exc}")

    if not valid_proxies:
        print("[!] No working proxies met the criteria.")
        return []

    valid_proxies.sort(key=lambda x: x["latency"])

    try:
        dir_name = os.path.dirname(output_file)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        lines = [
            build_timestamp_header(),
            "# Sorted Fast Proxies (Batched & Early Stopped)",
            f"# Total collected working proxies: {len(valid_proxies)}",
            "",
        ]
        for item in valid_proxies:
            p = item["proxy"]
            if not p.startswith(("http://", "https://", "socks4://", "socks5://")):
                p = f"http://{p}"
            lines.append(p)

        Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[+] Saved {len(valid_proxies)} fast proxies to: {output_file}")
    except OSError as exc:
        print(f"[-] I/O Error while saving fast proxies: {exc}")

    return [item["proxy"] for item in valid_proxies]


if __name__ == "__main__":
    test_and_sort_proxies()
