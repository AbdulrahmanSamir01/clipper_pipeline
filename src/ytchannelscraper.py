import json
import os
import subprocess
import glob


class YouTubeChannelScraper:
    def __init__(
        self,
        json_channels_path,
        blocked_urls_path,
        output_txt_path,
        cookies_dir=None,
        proxy_file=None,
        timeout_per_attempt=120,
        flat_playlist=True,          # 🔑 تفعيل الوضع السريع افتراضياً
    ):
        self.json_channels_path = json_channels_path
        self.blocked_urls_path = blocked_urls_path
        self.output_txt_path = output_txt_path
        self.cookies_dir = cookies_dir
        self.proxy_file = proxy_file
        self.timeout_per_attempt = timeout_per_attempt
        self.flat_playlist = flat_playlist

    # ------------------------------------------------------------------
    # دوال التحميل
    # ------------------------------------------------------------------
    def _load_blocked_urls(self):
        blocked = set()
        if self.blocked_urls_path and os.path.exists(self.blocked_urls_path):
            with open(self.blocked_urls_path, "r", encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if url and not url.startswith("#"):
                        blocked.add(url)
        return blocked

    def _load_channels(self):
        if not os.path.exists(self.json_channels_path):
            print(f"[!] Channels file not found: {self.json_channels_path}")
            return []
        try:
            with open(self.json_channels_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, list):
                        return value
        except Exception as e:
            print(f"[!] Error reading JSON channels file: {e}")
        return []

    def _load_proxies(self):
        proxies = [None]
        if self.proxy_file and os.path.exists(self.proxy_file):
            with open(self.proxy_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxies.append(line)
        return proxies

    def _load_cookie_files(self):
        cookie_files = []
        if self.cookies_dir:
            if os.path.isdir(self.cookies_dir):
                cookie_files = sorted(glob.glob(os.path.join(self.cookies_dir, "*.txt")))
            elif os.path.exists(self.cookies_dir):
                cookie_files = [self.cookies_dir]
        return cookie_files or [None]

    # ------------------------------------------------------------------
    # تنسيق البيانات (مع التعامل مع القيم المفقودة)
    # ------------------------------------------------------------------
    @staticmethod
    def _format_date(date_str):
        if date_str and len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        return date_str or "N/A"

    @staticmethod
    def _format_duration(seconds):
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return "N/A"
        seconds = int(seconds)
        h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def _normalize_url(url):
        if "playlist?list=" in url or "watch?v=" in url:
            return url
        for tab in ("/videos", "/shorts", "/streams", "/playlists"):
            if tab in url:
                return url
        return url.rstrip("/") + "/videos"

    # ------------------------------------------------------------------
    # تنفيذ محاولة واحدة (مع --flat-playlist اختياري)
    # ------------------------------------------------------------------
    def _run_attempt(self, url, proxy, cookie, limit):
        fetch_limit = (limit + 5) if limit else 20
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            "--ignore-errors",
            "--no-check-certificates",
            "--no-progress",
            "--socket-timeout", "20",
            "--retries", "2",
            "--fragment-retries", "2",
            "--playlist-end", str(fetch_limit),
        ]

        # 🔑 إضافة --flat-playlist إذا كان الوضع السريع مفعلاً
        if self.flat_playlist:
            cmd.append("--flat-playlist")

        if cookie and os.path.exists(cookie):
            cmd.extend(["--cookies", cookie])
        if proxy:
            cmd.extend(["--proxy", proxy])
        cmd.append(url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=self.timeout_per_attempt,
            )
        except subprocess.TimeoutExpired:
            print(f"    [-] Timeout after {self.timeout_per_attempt}s")
            return None
        except FileNotFoundError:
            print("    [-] yt-dlp executable not found in PATH")
            return None
        except Exception as e:
            print(f"    [-] Subprocess exception: {e}")
            return None

        if result.returncode != 0:
            err_lines = (result.stderr or "").strip().splitlines()
            reason = err_lines[0][:200] if err_lines else "unknown error"
            print(f"    [-] yt-dlp failed (code {result.returncode}): {reason}")
            return None

        if not (result.stdout or "").strip():
            print("    [-] yt-dlp returned no data")
            return None

        return result.stdout

    # ------------------------------------------------------------------
    # تحليل المخرجات (مرن مع الحقول المفقودة)
    # ------------------------------------------------------------------
    def _parse_output(self, stdout, limit, blocked_urls):
        items = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue

            video_id = info.get("id")
            title = info.get("title")
            if not video_id or not title:
                continue

            video_url = f"https://www.youtube.com/watch?v={video_id}"
            if video_url in blocked_urls:
                print(f"    [-] Blocked: {video_url}")
                continue

            # 🔑 استخدام .get() مع قيم افتراضية آمنة
            raw_date = info.get("upload_date") or info.get("release_date") or ""
            upload_date = self._format_date(raw_date)

            duration = self._format_duration(info.get("duration"))
            views = info.get("view_count", "N/A")

            items.append(
                f"Title: {title}\n"
                f"Upload Date: {upload_date}\n"
                f"Duration: {duration}\n"
                f"Views: {views}\n"
                f"URL: {video_url}\n"
                f"{'-' * 50}\n"
            )
            print(f"    [+] {title} ({duration})")

            if limit and len(items) >= limit:
                break

        return items

    # ------------------------------------------------------------------
    # الدالة الرئيسية
    # ------------------------------------------------------------------
    def fetch_and_save(self, limit_per_channel=10, force=False):
        if (
            not force
            and os.path.exists(self.output_txt_path)
            and os.path.getsize(self.output_txt_path) > 0
        ):
            print(f"[!] Output file exists and is non-empty: {self.output_txt_path}")
            print("[*] Skipping. Use force=True to override.")
            return

        blocked_urls = self._load_blocked_urls()
        channels = self._load_channels()
        proxies = self._load_proxies()
        cookies = self._load_cookie_files()

        if not channels:
            print("[!] No channels to process.")
            return

        mode_label = "FLAT (سريع)" if self.flat_playlist else "FULL (كامل)"
        print(
            f"[*] Mode: {mode_label} | "
            f"Targets: {len(channels)} | "
            f"Proxies: {len(proxies)} | "
            f"Cookie sets: {len(cookies)} | "
            f"Timeout: {self.timeout_per_attempt}s"
        )

        all_items = []
        last_working_proxy = 0
        last_working_cookie = 0

        for raw_target in channels:
            target = self._normalize_url(raw_target)
            print(f"\n[*] Processing: {target}")

            proxy_order = [last_working_proxy] + [
                i for i in range(len(proxies)) if i != last_working_proxy
            ]
            cookie_order = [last_working_cookie] + [
                i for i in range(len(cookies)) if i != last_working_cookie
            ]

            total_attempts = len(proxies) * len(cookies)
            attempt = 0
            success = False

            for p_idx in proxy_order:
                if success:
                    break
                proxy = proxies[p_idx]
                for c_idx in cookie_order:
                    if success:
                        break
                    attempt += 1
                    cookie = cookies[c_idx]

                    print(
                        f"  --- Attempt {attempt}/{total_attempts} | "
                        f"proxy={'direct' if not proxy else proxy} | "
                        f"cookie={os.path.basename(cookie) if cookie else 'none'}"
                    )

                    stdout = self._run_attempt(target, proxy, cookie, limit_per_channel)
                    if not stdout:
                        continue

                    items = self._parse_output(stdout, limit_per_channel, blocked_urls)
                    if not items:
                        print("    [-] 0 valid items from this attempt")
                        continue

                    all_items.extend(items)
                    last_working_proxy = p_idx
                    last_working_cookie = c_idx
                    success = True
                    print(f"    [+] Collected {len(items)} items")

            if not success:
                print(f"  [-] All attempts failed for: {target}")

        if not all_items:
            print("\n[-] No data collected. Output file NOT created.")
            return

        out_dir = os.path.dirname(self.output_txt_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        with open(self.output_txt_path, "w", encoding="utf-8") as f:
            f.writelines(all_items)

        print(f"\n[+] Done. Saved {len(all_items)} items to: {self.output_txt_path}")