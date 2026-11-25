from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import List
from bs4 import BeautifulSoup
import requests

TIMESTAMP_COMMENT_RE = re.compile(r"^#\s*Generated\s+UTC:\s*(\S+)\s*$")


def build_timestamp_header() -> str:
    """Build a UTC timestamp comment line representing when the proxy list was generated."""
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"# Generated UTC: {now_utc}"


def is_cache_fresh_by_comment(filepath: str | Path, max_age_hours: float) -> bool:
    """Return True only if the file exists, contains a valid timestamp comment, and is within max_age_hours."""
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
                f"[!] Proxy list file is fresh based on header (age: {round(age_hours, 2)}h < {max_age_hours}h). Skipping scraping."
            )
            return True

        print(f"[*] Proxy list expired based on header (age: {round(age_hours, 2)}h >= {max_age_hours}h).")
        return False
    except (OSError, ValueError) as exc:
        print(f"[-] Failed to read timestamp comment from {path}: {exc}")
        return False


def _read_proxies_from_file(filepath: str | Path) -> List[str]:
    """Read non-empty, non-comment proxy lines from a file."""
    path = Path(filepath)
    try:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError as exc:
        print(f"[-] Failed reading cached proxies from {path}: {exc}")
        return []


def fetch_free_proxy_list() -> List[str]:
    """Fetch HTTP proxies from free-proxy-list.net."""
    proxies = []
    url = "https://free-proxy-list.net/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    print("[*] Fetching from free-proxy-list.net...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if not table:
            return proxies
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip, port = cols[0].text.strip(), cols[1].text.strip()
                if ip and port.isdigit():
                    proxy = f"http://{ip}:{port}"
                    if proxy not in proxies:
                        proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from free-proxy-list.net.")
    except Exception as exc:
        print(f"[-] Error with free-proxy-list.net: {exc}")
    return proxies


def fetch_proxyscrape() -> List[str]:
    """Fetch proxies from ProxyScrape API."""
    proxies = []
    url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text"
    print("[*] Fetching from ProxyScrape API...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and proxy not in proxies:
                proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from ProxyScrape.")
    except Exception as exc:
        print(f"[-] Error with ProxyScrape: {exc}")
    return proxies


def fetch_geonode() -> List[str]:
    """Fetch proxies from Geonode API."""
    proxies = []
    url = "https://proxies.geonode.com/api/proxy-list?limit=100&page=1&sort_by=lastChecked&sort_type=desc"
    print("[*] Fetching from Geonode API...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        for item in data.get("data", []):
            protocols = item.get("protocols", ["http"])
            protocol = protocols[0] if protocols else "http"
            ip, port = item.get("ip"), item.get("port")
            if ip and port:
                proxy = f"{protocol}://{ip}:{port}"
                if proxy not in proxies:
                    proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from Geonode.")
    except Exception as exc:
        print(f"[-] Error with Geonode: {exc}")
    return proxies


def fetch_spys_me() -> List[str]:
    """Fetch HTTP proxies from Spys.me."""
    proxies = []
    url = "https://spys.me/proxy.txt"
    print("[*] Fetching from Spys.me...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                ip_port = line.split()[0]
                proxy = f"http://{ip_port}"
                if proxy not in proxies:
                    proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from Spys.me.")
    except Exception as exc:
        print(f"[-] Error with Spys.me: {exc}")
    return proxies


def fetch_spys_socks() -> List[str]:
    """Fetch SOCKS5 proxies from Spys.me."""
    proxies = []
    url = "https://spys.me/socks.txt"
    print("[*] Fetching from Spys.me SOCKS...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                ip_port = line.split()[0]
                proxy = f"socks5://{ip_port}"
                if proxy not in proxies:
                    proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from Spys.me SOCKS.")
    except Exception as exc:
        print(f"[-] Error with Spys.me SOCKS: {exc}")
    return proxies


def fetch_proxy_list_download_http() -> List[str]:
    """Fetch HTTP proxies from Proxy-List.download."""
    proxies = []
    url = "https://www.proxy-list.download/api/v1/get?type=http"
    print("[*] Fetching from Proxy-List.download HTTP...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and ":" in proxy and proxy not in proxies:
                proxies.append(f"http://{proxy}")
        print(f"[+] Successfully fetched {len(proxies)} from Proxy-List.download HTTP.")
    except Exception as exc:
        print(f"[-] Error with Proxy-List.download HTTP: {exc}")
    return proxies


def fetch_proxy_list_download_socks() -> List[str]:
    """Fetch SOCKS4 proxies from Proxy-List.download."""
    proxies = []
    url = "https://www.proxy-list.download/api/v1/get?type=socks4"
    print("[*] Fetching from Proxy-List.download SOCKS4...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and ":" in proxy and proxy not in proxies:
                proxies.append(f"socks4://{proxy}")
        print(f"[+] Successfully fetched {len(proxies)} from Proxy-List.download SOCKS4.")
    except Exception as exc:
        print(f"[-] Error with Proxy-List.download SOCKS4: {exc}")
    return proxies


def fetch_pubproxy() -> List[str]:
    """Fetch HTTP proxies from PubProxy API."""
    proxies = []
    url = "http://pubproxy.com/api/proxy?format=txt&limit=20"
    print("[*] Fetching from PubProxy...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and ":" in proxy and proxy not in proxies:
                proxies.append(f"http://{proxy}")
        print(f"[+] Successfully fetched {len(proxies)} from PubProxy.")
    except Exception as exc:
        print(f"[-] Error with PubProxy: {exc}")
    return proxies


def fetch_sslproxies() -> List[str]:
    """Fetch HTTPS proxies from SSLProxies."""
    proxies = []
    url = "https://www.sslproxies.org/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    print("[*] Fetching from SSLProxies...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if table:
            tbody = table.find("tbody")
            rows = tbody.find_all("tr") if tbody else table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    ip, port = cols[0].text.strip(), cols[1].text.strip()
                    if ip and port.isdigit():
                        proxy = f"https://{ip}:{port}"
                        if proxy not in proxies:
                            proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from SSLProxies.")
    except Exception as exc:
        print(f"[-] Error with SSLProxies: {exc}")
    return proxies


def fetch_usproxy() -> List[str]:
    """Fetch HTTP proxies from US-Proxy."""
    proxies = []
    url = "https://www.us-proxy.org/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    print("[*] Fetching from USProxy...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if table:
            tbody = table.find("tbody")
            rows = tbody.find_all("tr") if tbody else table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    ip, port = cols[0].text.strip(), cols[1].text.strip()
                    if ip and port.isdigit():
                        proxy = f"http://{ip}:{port}"
                        if proxy not in proxies:
                            proxies.append(proxy)
        print(f"[+] Successfully fetched {len(proxies)} from USProxy.")
    except Exception as exc:
        print(f"[-] Error with USProxy: {exc}")
    return proxies


def fetch_monosans() -> List[str]:
    """Fetch HTTP proxies from Monosans repository on GitHub."""
    proxies = []
    url = "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"
    print("[*] Fetching from Monosans GitHub...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        for line in response.text.splitlines():
            proxy = line.strip()
            if proxy and ":" in proxy and proxy not in proxies:
                proxies.append(f"http://{proxy}")
        print(f"[+] Successfully fetched {len(proxies)} from Monosans.")
    except Exception as exc:
        print(f"[-] Error with Monosans: {exc}")
    return proxies


def scrape_free_proxies(
    output_file: str = "input/media/yt/proxies.txt",
    max_age_hours: float = 1,
    force: bool = False,
) -> List[str]:
    """Scrape proxies from multiple sources unless the output file has a valid fresh timestamp comment."""
    if not force and is_cache_fresh_by_comment(output_file, max_age_hours):
        cached = _read_proxies_from_file(output_file)
        if cached:
            return cached
        print("[!] Timestamp comment was fresh, but no proxies were found inside cache. Re-scraping...")

    sources = [
        fetch_free_proxy_list(),
        fetch_proxyscrape(),
        fetch_geonode(),
        fetch_spys_me(),
        fetch_spys_socks(),
        fetch_proxy_list_download_http(),
        fetch_proxy_list_download_socks(),
        fetch_pubproxy(),
        fetch_sslproxies(),
        fetch_usproxy(),
        fetch_monosans(),
    ]

    # Interleave proxies round-robin so no single source dominates the top results.
    combined_proxies = []
    seen = set()
    max_len = max((len(src) for src in sources), default=0)

    for i in range(max_len):
        for src in sources:
            if i < len(src):
                proxy = src[i]
                if proxy not in seen:
                    seen.add(proxy)
                    combined_proxies.append(proxy)

    if combined_proxies:
        try:
            dir_name = os.path.dirname(output_file)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            content_lines = [
                build_timestamp_header(),
                "# Interleaved Multi-Source Free Proxies (11 Sources)",
                *combined_proxies,
            ]
            Path(output_file).write_text("\n".join(content_lines) + "\n", encoding="utf-8")
            print(f"[+] Successfully saved {len(combined_proxies)} unique interleaved proxies to: {output_file}")
        except OSError as exc:
            print(f"[-] I/O Error while saving proxies: {exc}")
    else:
        print("[!] No proxies were extracted from any source.")

    return combined_proxies


if __name__ == "__main__":
    scrape_free_proxies()
