import os
import json
import time
from functools import wraps
from datetime import datetime

# استيراد وحدات إعداد الشبكة والكوكيز
from src.cookies_generator import update_cookies_sync
from src.proxy_tester import test_and_sort_proxies
from src.proxy_tester_yt_dlp import ProxyTester
from src.proxy_scraper import scrape_free_proxies

# استيراد وحدات تحميل الملفات والترجمات
from src.downloader import run_download
from src.srt_to_txt import run_conversion

# استيراد وحدة كشط قنوات يوتيوب
from src.ytchannelscraper import YouTubeChannelScraper

# استيراد وحدات التحليل والمعالجة وقطع الفيديو
from src.analyzer import process_text_files 
from src.ffmpeg_analyzer import process_clipper_data 
from src.markdown_clipper import process_markdown_clips 
from src.clip_downloader import extract_clips_from_markdown 

# استيراد وحدة التفريغ الصوتي (Groq)
from src.groq_transcriber import transcribe_media_folder_groq

# استيراد وحدات التحرير، المونتاج، وهندسة الصوت
from src.editor.md_processor import MarkdownProcessor
from src.editor.video_cutter import VideoCutter
from src.editor.video_editor import VideoPipelineAutomation
from src.editor.audio_mixer import process_audio_library

# استيراد وحدات الجدولة والرفع النهائي على يوتيوب
from src.add_time import add_datetime_to_txt_files


# =========================================================================
# نظام مراقبة الأداء وقياس الوقت (Performance Monitor)
# =========================================================================
class ExecutionMonitor:
    def __init__(self):
        self.timings = {}

    def time_it(self, name: str):
        """مُزيّن (Decorator) لقياس وقت تنفيذ الدوال وتخزينها"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    elapsed = time.time() - start_time
                    self.timings[name] = self.timings.get(name, 0) + elapsed
            return wrapper
        return decorator

    def record_step(self, name: str, elapsed: float):
        """تسجيل وقت الخطوات اليدوية أو الكتل البرمجية مباشرة"""
        self.timings[name] = self.timings.get(name, 0) + elapsed

    def print_report(self):
        """طباعة تقرير ملخص ومنظم لأكثر الخطوات استهلاكاً للوقت"""
        if not self.timings:
            print("\n[*] No timing data recorded.")
            return

        sorted_timings = sorted(self.timings.items(), key=lambda item: item[1], reverse=True)
        total_time = sum(self.timings.values())

        print("\n" + "=" * 60)
        print(" ⏱️  PERFORMANCE & EXECUTION TIME REPORT")
        print("=" * 60)
        print(f"Total Pipeline Execution Time: {total_time:.2f} seconds ({total_time / 60:.2f} minutes)\n")
        print(f"{'Step / Function Name':<40} | {'Time (s)':<10} | {'Percentage':<10}")
        print("-" * 60)

        for name, duration in sorted_timings:
            percentage = (duration / total_time) * 100 if total_time > 0 else 0
            print(f"{name:<40} | {duration:<10.2f} | {percentage:<6.1f}%")
        print("=" * 60 + "\n")


# إنشاء كائن عام للمراقبة
monitor = ExecutionMonitor()


@monitor.time_it("should_run_channel")
def should_run_channel(state_json_path: str) -> bool:
    """
    دالة لفحص حالة القناة وتحديد ما إذا كان يجب تشغيل البايبلاين أم تخطيه.
    الهدف: توفير الموارد وتقليل العمليات المتكررة إذا كانت المواعيد مغطاة مسبقاً.
    """
    if not os.path.exists(state_json_path):
        print(f"[*] State JSON file not found at '{state_json_path}'. Proceeding with pipeline execution.")
        return True

    try:
        with open(state_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        booked_slots = data.get("booked_slots", [])
        if not booked_slots:
            print(f"[*] No booked slots found in '{state_json_path}'. Proceeding with pipeline execution.")
            return True

        last_slot_str = booked_slots[-1]
        last_date = datetime.fromisoformat(last_slot_str).date()
        current_date = datetime.now().date()

        print(f"[*] Checking schedule for channel...")
        print(f"    - Last booked slot date: {last_date}")
        print(f"    - Current system date:    {current_date}")
        
        if current_date <= last_date:
            print(f"[!] SKIP: Current date ({current_date}) is <= last scheduled date ({last_date}). Channel pipeline aborted.")
            return False

    except Exception as e:
        print(f"[-] Error reading state JSON file ({e}). Proceeding with pipeline execution as fallback.")
        return True

    return True


def run_pipeline(channel: str):
    """
    الدالة الرئيسية لتشغيل البايبلاين بالكامل للقناة المحددة خطوة بخطوة.
    """
    pipeline_start = time.time()
    print(f"\n==============================================")
    print(f"[*] Starting Pipeline for Channel: [{channel}]")
    print(f"==============================================\n")

    input_base = f"input/{channel}"
    output_base = f"output/{channel}"
    base_db = f"{input_base}/yt"
    state_json_path = f"{base_db}/last_state.json"

    # =========================================================================
    # الخطوة 1: التحقق السريع من المواعيد وتخطي القناة إذا كان التاريخ مغطى
    # =========================================================================
    if not should_run_channel(state_json_path):
        print(f"[*] Pipeline for channel [{channel}] finished (Skipped).\n")
        return

    # =========================================================================
    # الخطوة 2: إعداد الشبكة، البروكسيات، والكوكيز لتجنب الحظر
    # =========================================================================
    t0 = time.time()
    print(f"[*] Initializing network, cookies, and proxies...")
    update_cookies_sync()
    scrape_free_proxies()
    test_and_sort_proxies()


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

    monitor.record_step("Step 2: Network & Proxies Setup", time.time() - t0)

    # =========================================================================
    # الخطوة 3: كشط وحلب البيانات والفيديوهات من قناة يوتيوب المستهدفة
    # =========================================================================
    t0 = time.time()
    print(f"[*] Scraping channel data from YouTube...")
    scraper = YouTubeChannelScraper(
        json_channels_path=f"{input_base}/channels.json",
        blocked_urls_path=f"{input_base}/blocked_urls.txt",
        output_txt_path=f"{output_base}/json/channels_results.txt",
        cookies_dir="input/media/yt/cookies",
        proxy_file="input/media/yt/dl_proxies.txt"
    )
    scraper.fetch_and_save(limit_per_channel=10)

    process_text_files(
        input_dir=f"{output_base}/json",
        prompt_file_path=f"{input_base}/target_video/prompts/target_video_prompt.md",
        output_dir=f"{output_base}/target_video",
    )
    monitor.record_step("Step 3: Scraping & Target Video AI", time.time() - t0)

    # =========================================================================
    # الخطوة 4: تنزيل الوسائط + transcribe بديل + تحويل الترجمات إلى TXT
    # =========================================================================
    t0 = time.time()
    run_download(
        md_file_path=f"{output_base}/target_video/channels_results.md",
        download_mode="high_quality",
        output_dir=f"{output_base}/downloader",
        proxy_file="input/media/yt/dl_proxies.txt",
        done_urls_path=f"{input_base}/blocked_urls.txt",
        config_json_path=f"{input_base}/editor/video_setting.json",
    )

    transcribe_media_folder_groq(
        target_directory=f"{output_base}/downloader",
        output_dir=f"{output_base}/downloader",
        model_name="whisper-large-v3",
        skip_existing=True,
        chunk_size=5,
        generate_txt=False,
    )

    run_conversion(src_dir=f"{output_base}/downloader")
    monitor.record_step("Step 4: Download & Initial Transcription", time.time() - t0)

    # =========================================================================
    # الخطوة 5: تحليل ملفات الترجمة، استخراج المقاطع الأولية، والقص (Step 2)
    # =========================================================================
    t0 = time.time()
    print(f"[*] Analyzing subtitles and cutting initial clips...")
    downloader_dir = f"{output_base}/downloader/" 
    analyzed_subtitles_txt = f"{output_base}/downloader/ffmpeg_analyzer/analyzed_subtitles.txt"
    
    process_clipper_data(downloader_dir, analyzed_subtitles_txt)

    process_text_files(
        input_dir=f"{output_base}/downloader/ffmpeg_analyzer",
        prompt_file_path=f"{input_base}/analyzer/prompts/analyzer_prompt.md",
        output_dir=f"{output_base}/analyzer",
    )

    analyzed_subtitles_md = f"{output_base}/analyzer/analyzed_subtitles.md"
    output_clips_dir = f"{output_base}/analyzer/output_clips"
    
    process_markdown_clips(
        downloader_dir, 
        analyzed_subtitles_md, 
        output_clips_dir,
        padding_start=5, 
        padding_end=10,
        reencode=False
    )
    monitor.record_step("Step 5: Subtitle Analysis & Initial Clips Cutting", time.time() - t0)

    # =========================================================================
    # الخطوة 6: التفريغ الصوتي للمقاطع باستخدام نموذج Groq Whisper (Step 3)
    # =========================================================================
    t0 = time.time()
    print(f"[*] Transcribing clips using Groq Whisper...")
    transcribe_media_folder_groq(
        target_directory=output_clips_dir,
        output_dir=output_clips_dir,
        model_name="whisper-large-v3",
        skip_existing=True,
        chunk_size=1
    )
    monitor.record_step("Step 6: Clips Transcription (Whisper Large V3)", time.time() - t0)

    # =========================================================================
    # الخطوة 7: التحرير المتقدم، المونتاج، تركيب المؤثرات، وإنشاء الفيديو النهائي (Step 4)
    # =========================================================================
    t0 = time.time()
    print(f"[*] Starting advanced editing and rendering pipeline...")
    
    process_text_files(
        input_dir=output_clips_dir,  
        prompt_file_path=f"{input_base}/editor/prompts/cuttor_prompt.md",  
        output_dir=f"{output_base}/editor/",
        merge_files=True
    )

    process_markdown_clips(
        input_folder=f"{output_base}/analyzer/output_clips/", 
        md_file_path=f"{output_base}/editor/combined_output.md", 
        output_folder=f"{output_base}/editor/clips", 
        pattern_type="arabic_report",
    )

    transcribe_media_folder_groq(
        target_directory=f"{output_base}/editor/clips",
        output_dir=f"{output_base}/editor/clips",
        model_name="whisper-large-v3-turbo",
        skip_existing=True,
        chunk_size=2
    )

    process_text_files(
        input_dir=f"{output_base}/editor/clips",  
        prompt_file_path=f"{input_base}/editor/prompts/editor_prompt.md",  
        output_dir=f"{output_base}/editor/instructor",
    )

    MarkdownProcessor.process_folder(
        f"{output_base}/editor/instructor", 
        f"{input_base}/editor/video_setting.json", 
        f"{output_base}/editor/dl"
    )

    VideoCutter.process_folder(
        md_dir=f"{output_base}/editor/instructor",
        aroll_dir=f"{output_base}/editor/clips/",
        broll_dir=f"{output_base}/editor/dl",
        output_dir=f"{output_base}/editor/cutter_clips/",
        extract_audio=True
    )

    effects_dir = "input/media/sound_effects/islam_style"
    process_audio_library(
        f"{output_base}/editor/instructor/", 
        f"{output_base}/editor/cutter_clips/", 
        effects_dir
    )

    pipeline = VideoPipelineAutomation(
        source_parent_dir=f"{output_base}/editor/cutter_clips",
        srt_dir=f"{output_base}/editor/clips/",
        md_dir=f"{output_base}/editor/instructor/",
        json_config_path=f"{input_base}/editor/video_setting.json",
        output_dir=f"{output_base}/Final"
    )
    pipeline.process_all_folders()
    monitor.record_step("Step 7: Advanced Editing & Video Rendering Pipeline", time.time() - t0)

    # =========================================================================
    # الخطوة 8: تجهيز البيانات الوصفية والرفع التلقائي على يوتيوب (Step 5)
    # =========================================================================
    t0 = time.time()
    print(f"[*] Preparing metadata and uploading videos to YouTube...")
    
    add_datetime_to_txt_files(f"{output_base}/editor/clips/")

    process_text_files(
        input_dir=f"{output_base}/editor/clips/",
        prompt_file_path=f"{input_base}/script_details/prompts/script_details.md",  
        output_dir=f"{output_base}/Final/",
        merge_files=True
    )


    print(f"\n[+] Pipeline execution completed successfully for channel: [{channel}]\n")
    
    # طباعة تقرير الأداء النهائي للوقت
    monitor.print_report()