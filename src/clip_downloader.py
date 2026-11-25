import os
import re
import json
import glob
import time
import yt_dlp

# ======================================================================
# دوال مساعدة داخلية
# ======================================================================
def parse_time_to_seconds(time_str: str) -> int:
    """تحويل التوقيت من صيغة HH:MM:SS إلى ثوانٍ لـ yt-dlp"""
    parts = list(map(int, time_str.split(':')))
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]

def _resolve_target_url(target_input: str) -> str:
    """
    دالة ذكية: إذا كان المدخل رابطاً مباشراً يعيده،
    وإذا كان مسار ملف، يفتح الملف ويستخرج رابط اليوتيوب بداخله بين وسوم [target_url].
    """
    if not target_input:
        raise ValueError("[-] target_url is empty!")
    
    if target_input.startswith("http://") or target_input.startswith("https://"):
        return target_input.strip()
    
    if os.path.exists(target_input):
        with open(target_input, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'\[target_url\]\s*(https?://[^\s]+)\s*\[target_url/\]', content)
        if match:
            return match.group(1).strip()
            
    raise ValueError(f"[-] Could not extract a valid YouTube URL from: {target_input}")


# ======================================================================
# الدالة الرئيسية للتقطيع والتحميل المباشر عبر yt-dlp
# ======================================================================
def extract_clips_from_markdown(
    markdown_path: str,
    output_clips_dir: str,
    target_url: str = None,
    config_json_path: str = "input/editor/video_setting.json",
    cookies_dir: str = "input/media/yt/cookies",
    proxy_file: str = None,
    padding_start: int = 5,
    padding_end: int = 10
):
    """
    تقرأ ملف analyzed_subtitles.md لاستخراج التوقيتات وأرقام المقاطع،
    ثم تقوم بتحميل كل مقطع على حدة بالنطاق الزمني المحدد مباشرة من يوتيوب 
    مع الحفاظ على أسماء فريدة وصحيحة لكل مقطع.
    """
    if not os.path.exists(output_clips_dir):
        os.makedirs(output_clips_dir, exist_ok=True)

    if not os.path.exists(markdown_path):
        raise FileNotFoundError(f"[-] Analyzed subtitles markdown not found: {markdown_path}")

    with open(markdown_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. تحديد الرابط الحقيقي (سواء أُعطي كمسار ملف، رابط مباشر، أو تم استخراجه من الملف)
    resolved_url = None
    if target_url:
        resolved_url = _resolve_target_url(target_url)
    else:
        match = re.search(r'\[target_url\]\s*(https?://[^\s]+)\s*\[target_url/\]', content)
        if match:
            resolved_url = match.group(1).strip()

    if not resolved_url:
        raise ValueError("[-] Could not resolve a valid YouTube URL. Please check your target_url parameter.")

    print(f"[+] Resolved YouTube URL: {resolved_url}")

    # 2. قراءة الأبعاد المطلوبة من video_setting.json
    target_height = 1920
    if config_json_path and os.path.exists(config_json_path):
        try:
            with open(config_json_path, "r", encoding="utf-8") as f:
                target_height = json.load(f).get("target_height", 1920)
        except Exception as e:
            print(f"[-] Warning reading config JSON: {e}, using default height 1920")

    # 3. استخراج المقاطع والتوقيتات من ملف analyzed_subtitles.md
    pattern = r'\[مقطع رقم (\d+)\]:[^\n]*\n\s*-\s*\*\*التوقيت:\*\*\s*من\s*`(\d{2}:\d{2}:\d{2})`\s*إلى\s*`(\d{2}:\d{2}:\d{2})`'
    clips_matches = re.findall(pattern, content)

    if not clips_matches:
        print("[-] No clips timing found with the expected pattern in Markdown.")
        return

    print(f"[+] Found {len(clips_matches)} clips to download.")

    # 4. إعداد البروكسيات والكوكيز
    proxies = [None]
    if proxy_file and os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as pf:
            proxies.extend(line.strip() for line in pf if line.strip() and not line.startswith("#"))

    cookie_files = []
    if cookies_dir and os.path.isdir(cookies_dir):
        cookie_files = sorted(glob.glob(os.path.join(cookies_dir, "*.txt")))
    elif cookies_dir and os.path.exists(cookies_dir):
        cookie_files = [cookies_dir]
    if not cookie_files:
        cookie_files = [None]

    # 5. المرور على كل مقطع وتحميله برقم تسلسلي فريد لمنع تداخل الأسماء
    for clip_idx, start_str, end_str in clips_matches:
        start_sec = max(0, parse_time_to_seconds(start_str) - padding_start)
        end_sec = parse_time_to_seconds(end_str) + padding_end
        
        print(f"\n[*] Processing Clip {clip_idx} | Range: {start_str} -> {end_str} (Seconds: {start_sec}s to {end_sec}s)")
        
        # 🌟 قالب اسم فريد يضمن عدم حدوث Overwrite وتجنب مشكلة الـ NA
        output_template = os.path.join(output_clips_dir, f"clip_{clip_idx}_%(title)s.%(ext)s")

        success = False
        for current_proxy in proxies:
            if success:
                break
            for current_cookie in cookie_files:
                if success:
                    break

                ydl_opts = {
                    'format': f'bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]',
                    'merge_output_format': 'mp4',
                    'download_ranges': yt_dlp.utils.download_range_func(None, [(start_sec, end_sec)]),
                    'outtmpl': output_template,
                    'nocheckcertificate': True,
                    'socket_timeout': 15,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    }
                }

                if current_cookie and os.path.exists(current_cookie):
                    ydl_opts['cookiefile'] = current_cookie
                if current_proxy:
                    ydl_opts['proxy'] = current_proxy

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([resolved_url])
                    success = True
                    print(f"[+] Successfully downloaded Clip {clip_idx}")
                except Exception as e:
                    print(f"[-] Failed with proxy/cookie combination: {e}")
                    time.sleep(2)

        if not success:
            print(f"[-] Error: Could not download Clip {clip_idx} after trying all proxies/cookies.")