import os
import json
import re
from pathlib import Path

from .pexels_downloader import PexelsDownloader
from .pixabay_downloader import PixabayDownloader


def time_string_to_seconds(time_str: str) -> float:
    try:
        time_str = time_str.strip()
        
        # فصل الجزء الخاص بالثواني (مع أجزائها) عن الساعات والدقائق
        parts = time_str.split(":")
        
        if len(parts) == 3:
            hours = float(parts[0].replace(",", "."))
            minutes = float(parts[1].replace(",", "."))
            
            # التعامل مع الثواني وأجزائها سواء احتوت على نقطة أو فاصلة
            seconds_part = parts[2].replace(",", ".")
            seconds = float(seconds_part)
            
            return (hours * 3600) + (minutes * 60) + seconds
            
        elif len(parts) == 2: # في حال كانت الصيغة دقيقة:ثانية (MM:SS.ms)
            minutes = float(parts[0].replace(",", "."))
            seconds = float(parts[1].replace(",", "."))
            return (minutes * 60) + seconds
            
        elif len(parts) == 1: # في حال كانت ثواني فقط
            return float(parts[0].replace(",", "."))
            
        return 0.0
    except Exception as e:
        print(f"[!] Error parsing time string '{time_str}': {e}")
        return 0.0

class MarkdownProcessor:

    @classmethod
    def process_folder(
        cls, md_dir: str, config_path: str, output_downloads_dir: str = "downloads"
    ):
        md_path = Path(md_dir)
        if not md_path.exists():
            print(f"[!] Markdown directory not found: {md_dir}")
            return

        # 1. إدارة خطأ قراءة ملف الإعدادات
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"[!] Failed to read or parse config json at '{config_path}': {e}")
            return

        target_w = config.get("target_width", 1080)
        target_h = config.get("target_height", 1920)

        md_files = list(md_path.glob("*.md"))
        if not md_files:
            print(f"[!] No .md files found in directory: '{md_dir}'.")
            return

        print(f"[*] Found {len(md_files)} Markdown files. Target Dimensions: {target_w}x{target_h}\n")

        # التأكد من إنشاء مجلد التحميلات إن لم يكن موجوداً
        output_dir_path = Path(output_downloads_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)

        for md_file in md_files:
            print(f"--- Processing File: {md_file.name} ---")
            
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[!] Error reading markdown file {md_file.name}: {e}")
                continue

            # استخراج المقاطع بناءً على الهيكل الفعلي (تبدأ بـ [clip_X] وتستمر حتى المقطع التالي أو نهاية [/clips])
            clip_blocks = re.split(r"\[clip_\d+\]", content)
            clips = []
            
            for block in clip_blocks[1:]:
                # تنظيف البلوك وإزالة وسم الإغلاق العام [/clips] إن وجد
                clean_block = block.split("[/clips]")[0].strip()
                if clean_block:
                    clips.append(clean_block)

            # نمط احتياطي في حال تم استخدام الوسم المفرد [clip] ... [/clip]
            if not clips:
                clips = re.findall(r"\[clip\](.*?)\[/clip\]", content, re.DOTALL)

            if not clips:
                print(f"[!] Warning: No clips found in {md_file.name} using regex patterns. Check markdown format.")
                continue

            print(f"[*] Found {len(clips)} clips inside {md_file.name}.")

            for idx, clip_text in enumerate(clips, 1):
                time_match = re.search(r"\[start_time\]\s*(.*?)\s*-->\s*(.*?)\s*\[/start_time\]", clip_text)
                clip_duration = 5.0
                if time_match:
                    start_sec = time_string_to_seconds(time_match.group(1))
                    end_sec = time_string_to_seconds(time_match.group(2))
                    
                    # حساب الفارق بدقة عشرية، مع ضمان ألا يقل عن الحد الأدنى (مثل 2.0 ثانية)
                    calculated_duration = end_sec - start_sec
                    clip_duration = max(calculated_duration, 2.0)
                    
                    # (اختياري) إذا كنت ترغب في رؤية المدة الفعلية بالمللي ثانية في السجل:
                    print(f"    [⏱️] Calculated Duration: {clip_duration:.3f}s (Start: {start_sec}s, End: {end_sec}s)")
                else:
                    print(f"    [-] Clip #{idx}: [start_time] tag not found, using default duration (5.0s).")

                # فحص نوع المشهد (A-Roll أو B-Roll)
                type_match = re.search(r"\[type\]\s*(.*?)\s*\[/type\]", clip_text)
                if not type_match:
                    print(f"    [-] Clip #{idx}: [type] tag not found. Skipping.")
                    continue
                
                clip_type = type_match.group(1).strip()
                if "B-Roll" not in clip_type:
                    print(f"    [-] Clip #{idx}: Type is '{clip_type}' (Skipping download).")
                    continue

                # استخراج المنصة وكلمات البحث
                platform_match = re.search(r"\[platform\]\s*(.*?)\s*\[/platform\]", clip_text)
                keywords_match = re.search(r"\[keywords\]\s*(.*?)\s*\[/keywords\]", clip_text)

                if not platform_match or not keywords_match:
                    print(f"    [!] Clip #{idx}: Missing [platform] or [keywords] tags.")
                    continue

                platform = platform_match.group(1).strip()
                keywords = keywords_match.group(1).strip()

                if keywords.lower() == "none" or not keywords:
                    print(f"    [-] Clip #{idx}: Keywords are '{keywords}' (Skipping).")
                    continue

                # ميزة الـ Skip: التحقق مما إذا كان الفيديو موجوداً مسبقاً في مجلد الـ output
                expected_file_path = output_dir_path / f"{keywords}.mp4"
                if expected_file_path.exists():
                    print(f"    [✓] Clip #{idx}: Video for query '{keywords}' already exists. Skipping download.")
                    continue

                print(f"    [->] Clip #{idx} Request: Platform=[{platform}], Keywords=['{keywords}'], Min Duration={clip_duration:.1f}s")

                # تنفيذ التحميل بناءً على المنصة
                if "pexels" in platform.lower():
                    PexelsDownloader.download(keywords, target_w, target_h, clip_duration, output_downloads_dir)
                elif "pixabay" in platform.lower():
                    PixabayDownloader.download(keywords, target_w, target_h, clip_duration, output_downloads_dir)
                else:
                    print(f"    [!] Unknown platform specified: '{platform}'")

            print("-" * 30)