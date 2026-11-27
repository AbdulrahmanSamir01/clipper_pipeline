from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
import re
import shutil
import tempfile
import time
import requests
import yt_dlp

DEFAULT_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

DEFAULT_TEST_URL = "https://www.youtube.com/shorts/sHPvCUbyJPs"
DEFAULT_DENO_PATH = "/home/codespace/.deno/deno"
UTC_HEADER_PREFIX = "# Generated (UTC): "
UTC_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S %Z"


class _QuietLogger:
  """Suppress yt-dlp console noise during bulk proxy checks."""

  def debug(self, msg: str) -> None:
    """Ignore debug messages."""

  def warning(self, msg: str) -> None:
    """Ignore warning messages."""

  def error(self, msg: str) -> None:
    """Ignore error messages."""


def _parse_utc_timestamp(lines: list[str]) -> float | None:
  """Extract UTC timestamp from header safely."""
  for line in lines:
    cleaned = line.strip()
    if cleaned.startswith(UTC_HEADER_PREFIX):
      raw_dt = cleaned.removeprefix(UTC_HEADER_PREFIX).strip()
      try:
        dt = datetime.strptime(raw_dt, UTC_DATETIME_FORMAT).replace(
            tzinfo=timezone.utc
        )
        return dt.timestamp()
      except ValueError:
        return None
  return None


def _parse_cached_proxy_line(line: str) -> tuple[str, float] | None:
  """Parse a non-comment line into (proxy, speed_kbps)."""
  cleaned = line.strip()
  if not cleaned or cleaned.startswith("#"):
    return None

  if "#" in cleaned:
    proxy_part, comment_part = cleaned.split("#", 1)
    proxy = proxy_part.strip()
    if not proxy:
      return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*KB/s", comment_part, re.IGNORECASE)
    speed = float(match.group(1)) if match else 0.0
    return (proxy, speed)

  return (cleaned, 0.0)


def _get_cached_benchmark_results(
    output_file: str,
    max_age_hours: float,
    top_n: int = 50,
) -> list[tuple[str, float]] | None:
  """Return cached (proxy, speed) tuples if valid UTC header is within max_age_hours."""
  if max_age_hours <= 0:
    return None

  if not os.path.exists(output_file):
    return None

  try:
    with open(output_file, "r", encoding="utf-8") as file:
      lines = file.read().splitlines()
  except OSError as exc:
    print(f"[-] Error reading benchmark cache file: {exc}")
    return None

  created_ts = _parse_utc_timestamp(lines)
  if created_ts is None:
    print(
        "[!] Missing or invalid UTC header in benchmark output file. Re-running..."
    )
    return None

  age_hours = (time.time() - created_ts) / 3600
  if age_hours < 0:
    print(
        "[!] Future UTC timestamp detected in benchmark cache header. Re-running..."
    )
    return None

  if age_hours >= max_age_hours:
    print(
        f"[!] Benchmark cache expired (age: {age_hours:.2f}h >= {max_age_hours}h). Re-running..."
    )
    return None

  cached_results: list[tuple[str, float]] = []
  seen_proxies: set[str] = set()
  for line in lines:
    parsed = _parse_cached_proxy_line(line)
    if parsed is None:
      continue
    proxy, speed = parsed
    if proxy not in seen_proxies:
      seen_proxies.add(proxy)
      cached_results.append((proxy, speed))

  if not cached_results:
    print(
        "[!] Benchmark cache file has no valid proxy entries. Re-running..."
    )
    return None

  limited_results = cached_results[:top_n]
  print(
      f"[!] Benchmark output file is fresh ({len(limited_results)} proxies, "
      f"age: {age_hours:.2f}h < {max_age_hours}h). Skipping benchmark."
  )
  return limited_results


def load_proxies(input_file: str) -> list[str]:
  """Load unique, non-comment proxy entries from input_file."""
  if not os.path.exists(input_file):
    raise FileNotFoundError(f"Input file not found: {input_file}")

  proxies: list[str] = []
  try:
    with open(input_file, "r", encoding="utf-8") as file:
      for line in file:
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
          proxy_addr = cleaned.split("#", 1)[0].strip()
          if proxy_addr:
            proxies.append(proxy_addr)
  except OSError as exc:
    raise OSError(f"Failed reading input file {input_file}: {exc}") from exc

  return list(dict.fromkeys(proxies))


def check_connectivity(
    proxy: str,
    timeout: tuple[float, float] = (2.5, 2.5),
    headers: dict[str, str] | None = None,
) -> str | None:
  """Verify proxy reaches YouTube within timeout using browser-like headers."""
  proxies_dict = {"http": proxy, "https": proxy}
  req_headers = {**DEFAULT_BROWSER_HEADERS, **(headers or {})}

  try:
    response = requests.get(
        "https://www.youtube.com/generate_204",
        proxies=proxies_dict,
        headers=req_headers,
        timeout=timeout,
    )
    if response.status_code in (200, 204):
      return proxy
  except Exception:
    return None
  return None


def _sanitize_proxy_dirname(proxy: str) -> str:
  """Convert proxy URL/address into a safe directory name."""
  sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "_", proxy).strip("_")
  return sanitized or "proxy_tmp"


def build_ydl_opts(
    proxy: str,
    download_dir: str,
    progress_hook=None,
    cookies_file: str | None = None,
    headers: dict[str, str] | None = None,
    deno_path: str | None = None,
    node_path: str | None = None,
) -> dict:
  """Build yt-dlp options matching downloader.py configuration with format 160."""
  merged_headers = {**DEFAULT_BROWSER_HEADERS, **(headers or {})}

  opts: dict = {
      "proxy": proxy,
      "format": "160",
      "outtmpl": os.path.join(download_dir, "%(id)s.%(ext)s"),
      "skip_download": False,
      "ignoreerrors": False,
      "logger": _QuietLogger(),
      "noprogress": True,
      "nocheckcertificate": True,
      "socket_timeout": 15,
      "retries": 2,
      "fragment_retries": 2,
      "extractor_retries": 3,
      "http_headers": merged_headers,
  }

  if progress_hook is not None:
    opts["progress_hooks"] = [progress_hook]

  js_runtimes: dict[str, dict[str, str]] = {}
  resolved_deno = deno_path or (
      DEFAULT_DENO_PATH if os.path.exists(DEFAULT_DENO_PATH) else None
  )
  if resolved_deno and os.path.exists(resolved_deno):
    js_runtimes["deno"] = {"path": resolved_deno}
  if node_path and os.path.exists(node_path):
    js_runtimes["node"] = {"path": node_path}
  if js_runtimes:
    opts["js_runtimes"] = js_runtimes

  if cookies_file and os.path.isfile(cookies_file):
    opts["cookiefile"] = cookies_file

  return opts


def measure_yt_speed(
    proxy: str,
    test_url: str = DEFAULT_TEST_URL,
    base_temp_dir: str | None = None,
    cookies_file: str | None = None,
    headers: dict[str, str] | None = None,
    deno_path: str | None = None,
    node_path: str | None = None,
) -> tuple[str, float] | None:
  """
  Download format 160 in an isolated proxy-named folder via yt-dlp
  and calculate average download speed in KB/s.
  """
  owns_base_dir = False
  if base_temp_dir is None:
    base_temp_dir = tempfile.mkdtemp(prefix="yt_proxy_bench_")
    owns_base_dir = True

  proxy_dir = os.path.join(base_temp_dir, _sanitize_proxy_dirname(proxy))
  os.makedirs(proxy_dir, exist_ok=True)

  metrics = {
      "dl_start": None,
      "dl_end": None,
      "downloaded_bytes": 0,
      "speed_samples_kbps": [],
  }

  def _progress_hook(status: dict) -> None:
    state = status.get("status")
    now = time.perf_counter()

    if state == "downloading":
      if metrics["dl_start"] is None:
        metrics["dl_start"] = now
      downloaded = status.get("downloaded_bytes")
      if isinstance(downloaded, (int, float)) and downloaded > 0:
        metrics["downloaded_bytes"] = int(downloaded)
      speed_bps = status.get("speed")
      if isinstance(speed_bps, (int, float)) and speed_bps > 0:
        metrics["speed_samples_kbps"].append(speed_bps / 1024.0)

    elif state == "finished":
      metrics["dl_end"] = now
      total_bytes = status.get("total_bytes") or status.get("downloaded_bytes")
      if isinstance(total_bytes, (int, float)) and total_bytes > 0:
        metrics["downloaded_bytes"] = int(total_bytes)

  try:
    ydl_opts = build_ydl_opts(
        proxy=proxy,
        download_dir=proxy_dir,
        progress_hook=_progress_hook,
        cookies_file=cookies_file,
        headers=headers,
        deno_path=deno_path,
        node_path=node_path,
    )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
      info = ydl.extract_info(test_url, download=True)
      if not isinstance(info, dict):
        return None
      expected_path = ydl.prepare_filename(info)

    actual_size = 0
    if expected_path and os.path.isfile(expected_path):
      actual_size = os.path.getsize(expected_path)
    else:
      for fname in os.listdir(proxy_dir):
        fpath = os.path.join(proxy_dir, fname)
        if os.path.isfile(fpath) and not fname.endswith(".part"):
          actual_size = os.path.getsize(fpath)
          break

    if actual_size <= 0:
      return None

    avg_speed_kbps: float | None = None
    if metrics["speed_samples_kbps"]:
      avg_speed_kbps = sum(metrics["speed_samples_kbps"]) / len(
          metrics["speed_samples_kbps"]
      )
    elif metrics["dl_start"] is not None:
      end_t = metrics["dl_end"] or time.perf_counter()
      elapsed = max(end_t - metrics["dl_start"], 1e-3)
      avg_speed_kbps = (actual_size / 1024.0) / elapsed

    if avg_speed_kbps is None or avg_speed_kbps <= 0:
      return None

    return (proxy, round(avg_speed_kbps, 2))

  except Exception:
    return None

  finally:
    shutil.rmtree(proxy_dir, ignore_errors=True)
    if owns_base_dir:
      shutil.rmtree(base_temp_dir, ignore_errors=True)


def _rank_top_proxies(
    results: list[tuple[str, float]], top_n: int = 50
) -> list[tuple[str, float]]:
  """Sort proxy speed tuples descending (fastest to slowest) and keep top N."""
  return sorted(results, key=lambda item: item[1], reverse=True)[:top_n]


def save_top_proxies(
    results: list[tuple[str, float]], output_file: str, top_n: int = 50
) -> None:
  """Save top ranked proxies sorted from fastest to slowest with a UTC timestamp header."""
  parent_dir = os.path.dirname(output_file)
  if parent_dir:
    os.makedirs(parent_dir, exist_ok=True)

  utc_now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
  top_proxies = _rank_top_proxies(results=results, top_n=top_n)
  try:
    with open(output_file, "w", encoding="utf-8") as file:
      file.write(f"{UTC_HEADER_PREFIX}{utc_now_str}\n")
      file.write("# Ranked YouTube Proxies (Fastest -> Slowest)\n")
      for proxy, speed in top_proxies:
        file.write(f"{proxy} \n")
  except OSError as exc:
    raise OSError(f"Failed writing output file {output_file}: {exc}") from exc


def filter_youtube_proxies(
    input_file: str = "proxies.txt",
    output_file: str = "dl_proxies.txt",
    top_n: int = 50,
    max_workers: int = 80,
    test_url: str = DEFAULT_TEST_URL,
    cookies_file: str | None = None,
    headers: dict[str, str] | None = None,
    deno_path: str | None = None,
    node_path: str | None = None,
    max_age_hours: float = 1.0,
    force: bool = False,
) -> list[tuple[str, float]]:
  """Run connectivity check, parallel yt-dlp -f 160 download benchmark, sorting, and export."""
  if not force:
    cached = _get_cached_benchmark_results(
        output_file=output_file,
        max_age_hours=max_age_hours,
        top_n=top_n,
    )
    if cached is not None:
      return cached

  print(f"[*] Loading proxies from {input_file}...")
  raw_proxies = load_proxies(input_file)
  print(f"[*] Total unique proxies loaded: {len(raw_proxies)}")

  # Step 1: فحص الاتصال المبدئي السريع
  print("[*] Step 1: Testing basic connectivity (max timeout: 5s)...")
  alive_proxies: list[str] = []
  total_step1 = len(raw_proxies)
  completed_step1 = 0

  if total_step1 > 0:
    workers = max(1, min(max_workers, total_step1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
      futures = [
          executor.submit(check_connectivity, p, (2.5, 2.5), headers)
          for p in raw_proxies
      ]
      for future in as_completed(futures):
        completed_step1 += 1
        try:
          proxy = future.result()
        except Exception:
          proxy = None
        if proxy:
          alive_proxies.append(proxy)
        if completed_step1 % 150 == 0 or completed_step1 == total_step1:
          rem_step1 = total_step1 - completed_step1
          print(
              f"[*] Step 1 | Done: {completed_step1}/{total_step1} | "
              f"Remaining: {rem_step1} | Alive: {len(alive_proxies)}"
          )

  print(f"[+] Alive proxies passed Step 1: {len(alive_proxies)}")

  # Step 2: تحميل فعلي متوازٍ عبر yt-dlp -f 160 داخل مجلد معزول لكل بروكسي
  print(
      f"[*] Step 2: Downloading format 160 via yt-dlp ({test_url}) in parallel..."
  )
  valid_results: list[tuple[str, float]] = []
  total_step2 = len(alive_proxies)
  completed_step2 = 0

  if total_step2 > 0:
    bench_root_dir = tempfile.mkdtemp(prefix="yt_proxy_step2_")
    try:
      stage2_workers = max(1, min(20, total_step2))
      with ThreadPoolExecutor(max_workers=stage2_workers) as executor:
        futures = {
            executor.submit(
                measure_yt_speed,
                proxy,
                test_url,
                bench_root_dir,
                cookies_file,
                headers,
                deno_path,
                node_path,
            ): proxy
            for proxy in alive_proxies
        }
        for future in as_completed(futures):
          completed_step2 += 1
          try:
            result = future.result()
          except Exception:
            result = None
          if result:
            valid_results.append(result)
            print(f"    -> Valid: {result[0]} | Speed: {result[1]} KB/s")

          remaining_step2 = total_step2 - completed_step2
          print(
              f"[*] Step 2 | Done: {completed_step2}/{total_step2} | "
              f"Remaining: {remaining_step2} | Passed: {len(valid_results)}"
          )
    finally:
      shutil.rmtree(bench_root_dir, ignore_errors=True)

  # Step 3: الترتيب من الأعلى سرعة إلى الأقل وحفظ النتيجة مع ترويسة UTC
  save_top_proxies(valid_results, output_file, top_n)
  sorted_top = _rank_top_proxies(results=valid_results, top_n=top_n)
  print(f"[*] Done! Saved top {len(sorted_top)} proxies to {output_file}.")
  return sorted_top