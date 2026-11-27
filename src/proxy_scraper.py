from datetime import datetime, timezone
import os
import time
from typing import List
from bs4 import BeautifulSoup
import requests

UTC_HEADER_PREFIX = "# Generated (UTC): "


def _parse_utc_timestamp(lines: List[str]) -> float | None:
    """Extract UTC timestamp from header safely."""
    for line in lines:
        cleaned = line.strip()
        if cleaned.startswith(UTC_HEADER_PREFIX):
            raw_dt = cleaned.removeprefix(UTC_HEADER_PREFIX).strip()
            try:
                # Parse explicit UTC format so cache age check stays reliable.
                dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S %Z").replace(
                    tzinfo=timezone.utc
                )
                return dt.timestamp()
            except ValueError:
                return None
    return None


def _get_cached_proxies(output_file: str, max_age_hours: float) -> List[str] | None:
    """Return cached proxies only if a valid UTC header exists and is within max age."""
    if max_age_hours <= 0:
        return None

    if not os.path.exists(output_file):
        return None

    try:
        with open(output_file, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        print(f"[-] Error reading proxy cache file: {exc}")
        return None

    # Require UTC creation timestamp in header; rebuild if missing or invalid.
    created_ts = _parse_utc_timestamp(lines)
    if created_ts is None:
        print("[!] Missing or invalid UTC header in proxy file. Re-scraping...")
        return None

    age_hours = (time.time() - created_ts) / 3600
    if age_hours < 0:
        print("[!] Future UTC timestamp detected in cache header. Re-scraping...")
        return None

    if age_hours >= max_age_hours:
        print(
            f"[!] Proxy cache expired (age: {age_hours:.2f}h >= {max_age_hours}h). Re-scraping..."
        )
        return None

    proxies: List[str] = []
    seen = set()
    for line in lines:
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            proxies.append(cleaned)

    if not proxies:
        print("[!] Proxy cache file has no valid proxy entries. Re-scraping...")
        return None

    print(
        f"[!] Proxy list file is fresh ({len(proxies)} proxies, age: {age_hours:.2f}h < {max_age_hours}h). Skipping scraping."
    )
    return proxies


def _write_proxies_file(output_file: str, proxies: List[str]) -> None:
    """Write interleaved proxies along with a UTC creation timestamp header."""
    utc_now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    dir_name = os.path.dirname(output_file)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # Write UTC timestamp header first for consistent cache validation.
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"{UTC_HEADER_PREFIX}{utc_now_str}\n")
        f.write("# Interleaved Free Proxies (free-proxy-list -> ProxyScrape)\n")
        for proxy in proxies:
            f.write(f"{proxy}\n")


def fetch_free_proxy_list() -> List[str]:
    """Fetch proxies from free-proxy-list.net using BeautifulSoup."""
    proxies = []
    url = "https://free-proxy-list.net/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    print("[*] Fetching from free-proxy-list.net...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if not table:
            print("[-] Proxy table not found on free-proxy-list.net.")
            return proxies

        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip = cols[0].text.strip()
                port = cols[1].text.strip()
                if ip and port.isdigit():
                    proxy = f"http://{ip}:{port}"
                    if proxy not in proxies:
                        proxies.append(proxy)

        print(
            f"[+] Successfully fetched {len(proxies)} proxies from free-proxy-list.net."
        )
    except requests.exceptions.RequestException as exc:
        print(f"[-] Network error with free-proxy-list.net: {exc}")
    except Exception as exc:
        print(f"[-] Unexpected error parsing free-proxy-list.net: {exc}")

    return proxies


def fetch_proxyscrape() -> List[str]:
    """Fetch proxies directly from the ProxyScrape API."""
    proxies = []
    url = (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&proxy_format=protocolipport&format=text"
    )

    print("[*] Fetching from ProxyScrape API...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and proxy not in proxies:
                proxies.append(proxy)

        print(f"[+] Successfully fetched {len(proxies)} proxies from ProxyScrape.")
    except requests.exceptions.RequestException as exc:
        print(f"[-] Network error with ProxyScrape: {exc}")
    except Exception as exc:
        print(f"[-] Unexpected error with ProxyScrape: {exc}")

    return proxies


def _interleave_proxies(*lists: List[str]) -> List[str]:
    """Merge multiple proxy lists in round-robin order while preserving uniqueness."""
    combined: List[str] = []
    seen = set()
    max_len = max((len(lst) for lst in lists), default=0)

    for idx in range(max_len):
        for lst in lists:
            if idx < len(lst):
                candidate = lst[idx]
                if candidate not in seen:
                    seen.add(candidate)
                    combined.append(candidate)

    return combined


def scrape_free_proxies(
    output_file: str = "input/media/yt/proxies.txt",
    max_age_hours: float = 1,
    force: bool = False,
) -> List[str]:
    """Scrape free proxies from active sources, interleave them, and cache with UTC header."""
    if not force:
        cached = _get_cached_proxies(output_file, max_age_hours)
        if cached is not None:
            return cached

    list1 = fetch_free_proxy_list()
    list2 = fetch_proxyscrape()
    combined_proxies = _interleave_proxies(list1, list2)

    if combined_proxies:
        try:
            _write_proxies_file(output_file, combined_proxies)
            print(
                f"[+] Successfully saved a total of {len(combined_proxies)} unique interleaved proxies to: {output_file}"
            )
        except OSError as exc:
            print(f"[-] I/O error while saving proxies to file {output_file}: {exc}")
        except Exception as exc:
            print(f"[-] Unexpected error while saving file: {exc}")
    else:
        print("[!] No proxies were extracted from any source.")

    return combined_proxies


if __name__ == "__main__":
    scrape_free_proxies()