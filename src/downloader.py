"""
media_downloader.py
===================
وحدة تنزيل الوسائط والترجمة من روابط يوتيوب.

تعتمد على:
    - yt-dlp فقط

الهدف: التحميل حصراً — لا تحويل ولا استخراج نصوص.
تُعيد دالة download_media_from_md قاموساً بالمسارات الخام:
    {"media": "...", "subtitle": "..." | None}
"""

import json
import os
import glob
import re
import time
import random
import shutil

import yt_dlp


# ======================================================================
# دوال مساعدة داخلية
# ======================================================================
def _get_url_from_md(md_file_path: str) -> str:
    """قراءة رابط اليوتيوب من ملف Markdown بين [target_url] و [target_url/]."""
    if not os.path.exists(md_file_path):
        raise FileNotFoundError(f"Markdown file not found: {md_file_path}")
    with open(md_file_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'\[target_url\]\s*(https?://[^\s]+)\s*\[target_url/\]', content)
    if match:
        return match.group(1).strip()
    raise ValueError(f"Could not find a valid URL between [target_url] tags in {md_file_path}")


def _get_dimensions_from_json(config_json_path: str) -> tuple:
    """قراءة target_width/target_height من JSON، مع قيم افتراضية آمنة."""
    if config_json_path and os.path.exists(config_json_path):
        try:
            with open(config_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            width = data.get("target_width", 1080)
            height = data.get("target_height", 1920)
            print(f"[*] Loaded target dimensions: {width}x{height}")
            return width, height
        except Exception as e:
            print(f"[-] Error reading config JSON: {e}, using 1080x1920")
    return 1080, 1920


def _save_completed_url(url: str, done_urls_path: str):
    """تسجيل رابط منتهٍ في ملف التتبع."""
    if not done_urls_path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(done_urls_path)), exist_ok=True)
    with open(done_urls_path, "a", encoding="utf-8") as f:
        f.write(url + "\n")
    print(f"[*] Saved completed URL: {url}")


def _classify_error(error_msg: str) -> str:
    """تصنيف نوع الخطأ: rate_limit | bot_check | timeout | page_reload | subtitle_error | other."""
    msg = (error_msg or "").lower()
    if "429" in msg or "too many requests" in msg:
        return "rate_limit"
    if "sign in to confirm" in msg or ("bot" in msg and "confirm" in msg):
        return "bot_check"
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "page needs to be reloaded" in msg:
        return "page_reload"
    if "unable to download video subtitles" in msg:
        return "subtitle_error"
    return "other"


def _find_downloaded_subtitle(media_path: str, output_dir: str) -> str:
    """
    مساعد داخلي: البحث عن ملف الترجمة الذي نزّله yt-dlp لهذا الوسيط.

    ⚠️ هذه ليست دالة تحويل — هي فقط مسح للمجلد للعثور على .srt/.vtt
    المطابق لاسم الوسائط. تفضّل الترجمة العربية إن وُجدت.
    """
    if not media_path:
        return None
    base = os.path.splitext(os.path.basename(media_path))[0]
    candidates = []
    for fname in sorted(os.listdir(output_dir)):
        if fname.startswith(base) and fname.lower().endswith((".srt", ".vtt")):
            candidates.append(os.path.join(output_dir, fname))
    if not candidates:
        return None
    # تفضيل العربية
    for c in candidates:
        if ".ar." in os.path.basename(c).lower():
            return c
    return candidates[0]


# ======================================================================
# الدالة الرئيسية (تحميل فقط)
# ======================================================================
def download_media_from_md(
    md_file_path: str,
    download_mode: str = "audio",
    output_dir: str = "output",
    cookies_dir: str = "input/media/yt/cookies",
    proxy_file: str = None,
    done_urls_path: str = "done_urls.txt",
    config_json_path: str = "config.json",
    max_retries_per_combo: int = 2,
    delay_between_attempts: int = 5,
    max_total_attempts: int = 60,
) -> dict:
    """
    تنزيل فيديو/صوت + الترجمة من رابط يوتيوب في ملف Markdown.

    Returns
    -------
    dict
        {"media": "...", "subtitle": "..." | None}
        - media:    مسار ملف الوسائط (.mp3/.mp4/...)
        - subtitle: مسار ملف الترجمة الخام (.srt أو .vtt) — أو None إن لم تُنزَّل
    """

    # ------------------------------------------------------------------
    # 1) فحص Skip
    # ------------------------------------------------------------------
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    existing_media = None
    existing_txt = None
    for file in os.listdir(output_dir):
        low = file.lower()
        if low.endswith((".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".webm")) and not existing_media:
            existing_media = os.path.join(output_dir, file)
        if low.endswith(".txt") and not existing_txt:
            existing_txt = os.path.join(output_dir, file)

    url = _get_url_from_md(md_file_path)

    if existing_media and existing_txt:
        print(f"[!] Media + TXT exist in '{output_dir}'. Skipping.")
        _save_completed_url(url, done_urls_path)
        return {
            "media": existing_media,
            "subtitle": _find_downloaded_subtitle(existing_media, output_dir),
        }

    print(f"[*] Processing URL: {url}")
    target_width, target_height = _get_dimensions_from_json(config_json_path)

    # ------------------------------------------------------------------
    # 2) البروكسيات والكوكيز
    # ------------------------------------------------------------------
    proxies = [None]
    if proxy_file and os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as pf:
            proxies.extend(
                line.strip() for line in pf
                if line.strip() and not line.startswith("#")
            )

    cookie_files = []
    if cookies_dir:
        if os.path.isdir(cookies_dir):
            cookie_files = sorted(glob.glob(os.path.join(cookies_dir, "*.txt")))
        elif os.path.exists(cookies_dir):
            cookie_files = [cookies_dir]
    if not cookie_files:
        cookie_files = [None]

    print(
        f"[*] Proxies: {len(proxies)} | Cookies: {len(cookie_files)} | "
        f"Retries/combo: {max_retries_per_combo}"
    )

    # ------------------------------------------------------------------
    # 3) تكوين yt-dlp الأساسي
    # ------------------------------------------------------------------
    base_ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ar', 'default'],
        'subtitlesformat': 'best',
        'skip_default_select': True,
        'skip_download': False,
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'ignoreerrors': False,

        'socket_timeout': 15,
        'retries': 3,
        'fragment_retries': 3,
        'extractor_retries': 5,
        'sleep_interval': 2,
        'max_sleep_interval': 5,
        'nocheckcertificate': True,

        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        },
    }

    deno_path = '/home/codespace/.deno/deno'
    if os.path.exists(deno_path):
        base_ydl_opts['js_runtimes'] = {'deno': {'path': deno_path}}

    # 🌟 Postprocessor: توحيد صيغة الترجمة إلى SRT
    #     هذا ليس تحويلاً نصياً — هو فقط يجعل yt-dlp ينتج Title.ar.srt بدل Title.ar.vtt
    #     حتى يكتشفه transcribe_media_folder_groq (الذي يفحص .srt).
    subtitle_pp = {'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'}

    # ------------------------------------------------------------------
    # 4) أوضاع التنزيل
    # ------------------------------------------------------------------
    if download_mode == "audio":
        mode_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [
                subtitle_pp,
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                },
            ],
        }
    elif download_mode == "high_quality":
        mode_opts = {
            'format': (
                f'bestvideo[height<={target_height}][vcodec^=av01]+bestaudio[acodec^=opus]/'
                f'bestvideo[height<={target_height}][vcodec^=vp09]+bestaudio[acodec^=opus]/'
                f'bestvideo[height<={target_height}]+bestaudio/'
                f'best[height<={target_height}]/best'
            ),
            'merge_output_format': 'mp4',
            'postprocessors': [subtitle_pp],
        }
    elif download_mode == "low_quality":
        mode_opts = {
            'format': 'worstvideo+worstaudio/worst',
            'merge_output_format': 'mp4',
            'postprocessors': [subtitle_pp],
        }
    else:
        raise ValueError("Invalid download_mode. Use: 'audio', 'high_quality', 'low_quality'")

    # ------------------------------------------------------------------
    # 5) الحلقة الرئيسية
    # ------------------------------------------------------------------
    downloaded_files = {}
    success = False
    failed_combos = set()
    total_attempts_done = 0

    for proxy_idx, current_proxy in enumerate(proxies):
        if success:
            break
        proxy_label = current_proxy if current_proxy else "Local IP (Direct)"

        for cookie_idx, current_cookie in enumerate(cookie_files):
            if success:
                break

            combo_key = (proxy_idx, cookie_idx)
            if combo_key in failed_combos:
                print(f"[*] Skipping hard-failed combo: proxy={proxy_label}")
                continue

            cookie_label = os.path.basename(current_cookie) if current_cookie else "No Cookies"

            for retry_round in range(max_retries_per_combo):
                if success:
                    break

                total_attempts_done += 1
                if total_attempts_done > max_total_attempts:
                    print(f"[!] Max total attempts reached ({max_total_attempts}).")
                    break

                print(
                    f"\n--- Attempt {total_attempts_done} | "
                    f"Proxy={proxy_label} | Cookie={cookie_label} | "
                    f"Retry={retry_round + 1}/{max_retries_per_combo} ---"
                )

                ydl_opts = {**base_ydl_opts, **mode_opts}
                if current_cookie and os.path.exists(current_cookie):
                    ydl_opts['cookiefile'] = current_cookie
                if current_proxy:
                    ydl_opts['proxy'] = current_proxy

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info_dict = ydl.extract_info(url, download=True)
                        filename = ydl.prepare_filename(info_dict)

                        if download_mode == "audio":
                            downloaded_files["media"] = os.path.splitext(filename)[0] + ".mp3"
                        else:
                            downloaded_files["media"] = filename

                        subtitle = _find_downloaded_subtitle(filename, output_dir)
                        downloaded_files["subtitle"] = subtitle

                        if subtitle:
                            print(f"[+] Subtitle: {os.path.basename(subtitle)}")
                        else:
                            print(f"[!] No subtitle downloaded for this video.")

                    success = True
                    print(f"[+] SUCCESS! Proxy={proxy_label} | Cookie={cookie_label}")
                    break

                except yt_dlp.utils.DownloadError as e:
                    error_type = _classify_error(str(e))
                    print(f"[-] DownloadError [{error_type}]: {e}")

                    if error_type == "bot_check":
                        print("[*] Bot check → hard-fail combo, switching proxy.")
                        failed_combos.add(combo_key)
                        break

                    if error_type == "rate_limit":
                        wait = delay_between_attempts * (retry_round + 2) + random.uniform(0, 2)
                        print(f"[*] Rate limit → wait {wait:.1f}s...")
                        time.sleep(wait)
                        if retry_round >= max_retries_per_combo - 1:
                            failed_combos.add(combo_key)
                            print("[*] Hard-fail combo (rate limit exhausted).")
                            break

                    elif error_type in ("timeout", "page_reload"):
                        wait = delay_between_attempts + random.uniform(0, 2)
                        print(f"[*] {error_type} → wait {wait:.1f}s...")
                        time.sleep(wait)
                    else:
                        time.sleep(delay_between_attempts)

                except Exception as e:
                    print(f"[-] Unexpected exception: {e}")
                    time.sleep(delay_between_attempts)

    if not success:
        raise Exception("[-] All proxy/cookie combos failed.")

    _save_completed_url(url, done_urls_path)
    print(f"[+] Done. Mode: {download_mode} | Saved to: {output_dir}")
    return downloaded_files



# ======================================================================
# دالة التشغيل الموحّدة (تُستخدم من main.py)
# ======================================================================
def run_download(
    md_file_path: str,
    download_mode: str = "high_quality",
    output_dir: str = "output",
    cookies_dir: str = "input/media/yt/cookies",
    proxy_file: str = None,
    done_urls_path: str = "done_urls.txt",
    config_json_path: str = "config.json",
) -> dict:
    """
    غلاف تشغيلي: ينزّل الوسائط والترجمة، ثم يوحّد اسم الترجمة ليطابق اسم
    الوسائط (Title.ar.srt → Title.srt) — ليعمل Skip في أدوات مثل transcribe.

    كل الأخطاء تُعالَج داخلياً، لذا يمكن استدعاؤها بسطر واحد في main.py
    دون try/except.

    Returns
    -------
    dict
        {
          "ok": bool,                 # نجح التنزيل؟
          "media": str | None,        # مسار الوسائط
          "subtitle": str | None,     # مسار الترجمة (الخام أو الموحّدة)
          "normalized": str | None,   # مسار الترجمة بعد التوحيد (إن تم)
          "error": str | None,        # رسالة الخطأ إن وُجدت
        }
    """
    result = {
        "ok": False,
        "media": None,
        "subtitle": None,
        "normalized": None,
        "error": None,
    }

    try:
        dl = download_media_from_md(
            md_file_path=md_file_path,
            download_mode=download_mode,
            output_dir=output_dir,
            cookies_dir=cookies_dir,
            proxy_file=proxy_file,
            done_urls_path=done_urls_path,
            config_json_path=config_json_path,
        )
        result["media"] = dl.get("media")
        result["subtitle"] = dl.get("subtitle")

        # ---------------------------------------------
        # توحيد اسم الترجمة (نسخ بسيط، ليس تحويلاً)
        # ---------------------------------------------
        if result["media"] and result["subtitle"]:
            media_base = os.path.splitext(os.path.basename(result["media"]))[0]
            sub_ext = os.path.splitext(result["subtitle"])[1].lower()
            normalized = os.path.join(output_dir, media_base + sub_ext)

            if not os.path.exists(normalized):
                shutil.copy2(result["subtitle"], normalized)
                print(
                    f"[*] Normalized subtitle: "
                    f"{os.path.basename(result['subtitle'])} → {media_base}{sub_ext}"
                )
            else:
                print(f"[*] Normalized subtitle already exists: {media_base}{sub_ext}")

            result["normalized"] = normalized
            result["subtitle"] = normalized  # نُعيد المسار الموحّد

        result["ok"] = True
        print("\n--- Download Summary ---")
        print(f"Media File:    {result['media']}")
        print(f"Subtitle File: {result['subtitle']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"[-] Download workflow failed: {e}")
        print("[*] Continuing with remaining steps (transcribe / convert)...")

    return result

