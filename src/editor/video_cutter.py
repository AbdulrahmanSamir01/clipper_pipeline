import os
import re
import random
import subprocess
from pathlib import Path


def time_string_to_seconds(time_str: str) -> float:
    """تحويل نص التوقيت إلى ثوانٍ مع دعم كامل للأجزاء العشرية والفاصلات"""
    try:
        time_str = time_str.strip()
        parts = time_str.split(":")
        
        if len(parts) == 3:
            hours = float(parts[0].replace(",", "."))
            minutes = float(parts[1].replace(",", "."))
            seconds = float(parts[2].replace(",", "."))
            return (hours * 3600) + (minutes * 60) + seconds
            
        elif len(parts) == 2:
            minutes = float(parts[0].replace(",", "."))
            seconds = float(parts[1].replace(",", "."))
            return (minutes * 60) + seconds
            
        elif len(parts) == 1:
            return float(parts[0].replace(",", "."))
            
        return 0.0
    except Exception as e:
        print(f"[!] Error parsing time string '{time_str}': {e}")
        return 0.0


def get_video_duration(video_path: str) -> float:
    """استخراج مدة الفيديو باستخدام ffprobe بدقة عالية"""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[!] Error getting duration for {video_path}: {e}")
        return 0.0


class VideoCutter:

    @classmethod
    def process_folder(
        cls,
        md_dir: str,
        aroll_dir: str,
        broll_dir: str,
        output_dir: str = "trimmed_clips",
        extract_audio: bool = False,
    ):
        md_path = Path(md_dir)
        if not md_path.exists():
            print(f"[!] Markdown directory not found: {md_dir}")
            return

        aroll_path = Path(aroll_dir)
        broll_path = Path(broll_dir)

        base_out_path = Path(output_dir)
        base_out_path.mkdir(parents=True, exist_ok=True)

        md_files = sorted(list(md_path.glob("*.md")))
        if not md_files:
            print(f"[!] No .md files found in directory: '{md_dir}'.")
            return

        print(f"[*] Starting Video Cutting Process with Precise Re-encoding for {len(md_files)} Markdown files...\n")

        for md_file in md_files:
            print(f"=== Processing File: {md_file.name} ===")

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[!] Error reading file {md_file.name}: {e}")
                continue

            stem_name = md_file.stem
            file_out_path = base_out_path / stem_name
            file_out_path.mkdir(parents=True, exist_ok=True)

            # --- استخراج الصوت المباشر إذا تم تفعيل الخيار (extract_audio=True) ---
            if extract_audio:
                source_aroll_video = aroll_path / f"{stem_name}.mp4"
                if not source_aroll_video.exists():
                    base_stem = stem_name.split("_part")[0]
                    source_aroll_video = aroll_path / f"{base_stem}.mp4"

                if source_aroll_video.exists():
                    audio_output_path = file_out_path / f"{stem_name}.wav"
                    if audio_output_path.exists():
                        print(f"  [✓] Audio file '{audio_output_path.name}' already exists. Skipping audio extraction.")
                    else:
                        print(f"  [*] Extracting audio from '{source_aroll_video.name}' -> '{audio_output_path.name}'...")
                        cmd_audio = [
                            "ffmpeg", "-y",
                            "-i", str(source_aroll_video),
                            "-vn",
                            "-acodec", "pcm_s16le",
                            "-ar", "44100",
                            "-ac", "2",
                            str(audio_output_path)
                        ]
                        try:
                            subprocess.run(cmd_audio, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                            print(f"  [✓] Saved Audio: {file_out_path.name}/{audio_output_path.name}")
                        except subprocess.CalledProcessError as e:
                            print(f"  [!] FFmpeg audio extraction failed: {e.stderr.decode()}")
                else:
                    print(f"  [!] A-Roll video not found in '{aroll_dir}' for audio extraction. Skipping audio.")

            # --- استخراج وتقسيم المقاطع ---
            clip_blocks = re.split(r"\[clip_\d+\]", content)
            clips = []

            for block in clip_blocks[1:]:
                clean_block = block.split("[/clips]")[0].strip()
                if clean_block:
                    clips.append(clean_block)

            if not clips:
                clips = re.findall(r"\[clip\](.*?)\[/clip\]", content, re.DOTALL)

            if not clips:
                print(f"[!] Warning: No clips found in {md_file.name}.")
                continue

            print(f"[*] Found {len(clips)} clips inside {md_file.name}.")

            for idx, clip_text in enumerate(clips, 1):
                output_clip_path = file_out_path / f"{idx}.mp4"

                # ميزة Skip إذا كان الفيديو مقصوصاً مسبقاً
                if output_clip_path.exists():
                    print(f"  [✓] Clip #{idx}: File '{output_clip_path.name}' already exists. Skipping.")
                    continue

                time_match = re.search(
                    r"\[start_time\]\s*(.*?)\s*-->\s*(.*?)\s*\[/start_time\]",
                    clip_text,
                )
                if not time_match:
                    print(f"  [!] Clip #{idx}: Missing [start_time]. Skipping.")
                    continue

                start_sec = time_string_to_seconds(time_match.group(1))
                end_sec = time_string_to_seconds(time_match.group(2))
                
                # حساب المدة بدقة تامة وتثبيتها بثلاث منازل عشرية
                required_duration = round(end_sec - start_sec, 3)

                if required_duration <= 0:
                    print(f"  [!] Clip #{idx}: Invalid duration ({required_duration}s). Skipping.")
                    continue

                type_match = re.search(r"\[type\]\s*(.*?)\s*\[/type\]", clip_text)
                clip_type = type_match.group(1).strip() if type_match else "A-Roll"

                # --- معالجة مشاهد الـ A-Roll (مع إعادة ترميز دقيقة وسريعة) ---
                if "A-Roll" in clip_type:
                    source_aroll_video = aroll_path / f"{stem_name}.mp4"
                    
                    if not source_aroll_video.exists():
                        base_stem = stem_name.split("_part")[0]
                        source_aroll_video = aroll_path / f"{base_stem}.mp4"

                    if not source_aroll_video.exists():
                        print(f"  [!] A-Roll video not found in '{aroll_dir}' for file '{md_file.name}. Skipping.")
                        continue

                    print(
                        f"  [✂] Precise Cutting A-Roll #{idx}: [{start_sec:.3f}s -> {end_sec:.3f}s | Duration: {required_duration}s]..."
                    )
                    
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_sec),
                        "-i", str(source_aroll_video),
                        "-t", str(required_duration),
                        "-c:v", "libx264",      # إعادة ترميز لضمان دقة القص المطلقة
                        "-preset", "ultrafast",  # إعدادات فائقة السرعة
                        "-crf", "18",            # جودة عالية وممتازة
                        "-an",
                        str(output_clip_path)
                    ]
                    
                    try:
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                        print(f"  [✓] Saved A-Roll: {file_out_path.name}/{output_clip_path.name}")
                    except subprocess.CalledProcessError as e:
                        print(f"  [!] FFmpeg failed to cut A-Roll #{idx}: {e.stderr.decode()}")

                # --- معالجة مشاهد الـ B-Roll (مع إعادة ترميز دقيقة وسريعة) ---
                elif "B-Roll" in clip_type:
                    keywords_match = re.search(r"\[keywords\]\s*(.*?)\s*\[/keywords\]", clip_text)
                    keywords = keywords_match.group(1).strip() if keywords_match else ""

                    source_broll_video = broll_path / f"{keywords}.mp4"

                    if not source_broll_video.exists():
                        print(f"  [!] B-Roll video not found in '{broll_dir}' for query '{keywords}'. Skipping.")
                        continue

                    total_duration = get_video_duration(str(source_broll_video))
                    if total_duration <= 0:
                        print(f"  [!] Could not read duration for B-Roll video: {source_broll_video.name}")
                        continue

                    if total_duration > required_duration:
                        max_start = total_duration - required_duration
                        random_start = round(random.uniform(0, max_start), 3)
                        print(
                            f"  [🎲] Precise Cutting B-Roll #{idx}: Random start at {random_start:.3f}s (Duration: {required_duration}s)..."
                        )
                    else:
                        print(
                            f"  [!] B-Roll total duration ({total_duration:.3f}s) <= required duration ({required_duration:.3f}s). Using full video."
                        )
                        random_start = 0
                        required_duration = total_duration

                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(random_start),
                        "-i", str(source_broll_video),
                        "-t", str(required_duration),
                        "-c:v", "libx264",      # إعادة ترميز لضمان دقة القص المطلقة
                        "-preset", "ultrafast",  # إعدادات فائقة السرعة
                        "-crf", "18",            # جودة عالية وممتازة
                        "-an",
                        str(output_clip_path)
                    ]

                    try:
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                        print(f"  [✓] Saved B-Roll: {file_out_path.name}/{output_clip_path.name}")
                    except subprocess.CalledProcessError as e:
                        print(f"  [!] FFmpeg failed to cut B-Roll #{idx}: {e.stderr.decode()}")

            print("-" * 40)