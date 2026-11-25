import os
import subprocess
import re

def time_to_seconds(time_str):
    """Convert timestamp format 00:00:04 or 00:00:23,660 to seconds (supports both dot and comma)"""
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    hours = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds

def cut_media_segment(input_media, start_time_str, end_time_str, output_path, padding_start=0, padding_end=0, reencode=True):
    """Cut a specific media segment using FFmpeg with frame accuracy, padding support, and optional re-encoding"""
    start_sec = time_to_seconds(start_time_str) - padding_start
    if start_sec < 0:
        start_sec = 0
        
    end_sec = time_to_seconds(end_time_str) + padding_end
    duration = end_sec - start_sec

    if duration <= 0:
        print(f"[!] Warning: Invalid duration for segment {start_time_str} to {end_time_str}. Skipping...")
        return False

    # بناء أمر FFmpeg بناءً على حالة إعادة الترميز
    if reencode:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-i', input_media,
            '-to', str(duration),
            '-c:v', 'libx264', '-c:a', 'aac',
            output_path
        ]
    else:
        # النسخ السريع بدون إعادة ترميز (قد لا يكون دقيقاً بنسبة 100% على مستوى الإطار ولكنه أسرع بكثير)
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-i', input_media,
            '-to', str(duration),
            '-c', 'copy',
            output_path
        ]

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8'
        )
        if result.returncode == 0:
            return True
        else:
            print(f"[!] FFmpeg error: {result.stderr}")
            return False
    except Exception as e:
        print(f"[!] Error executing FFmpeg: {str(e)}")
        return False

# =====================================================================
# 1. النمط الافتراضي
# =====================================================================
def parse_default_pattern(md_content, input_folder):
    pattern = re.compile(r'(\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?).*?(\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)')
    matches = pattern.findall(md_content)
    
    if not matches:
        return []

    media_file = None
    for f in os.listdir(input_folder):
        if f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.aac')):
            media_file = os.path.join(input_folder, f)
            break
            
    if not media_file:
        return []

    clips = []
    for idx, (start_time, end_time) in enumerate(matches, 1):
        ext = os.path.splitext(media_file)[1].lower()
        output_extension = '.mp3' if ext in ['.mp3', '.wav', '.m4a', '.aac'] else '.mp4'
        output_clip_name = f"clip_{idx}{output_extension}"
        
        clips.append({
            'name': output_clip_name,
            'media_path': media_file,
            'start': start_time,
            'end': end_time
        })
    return clips

# =====================================================================
# 2. النمط العربي المرن (يدعم الأجزاء المتعددة وملفات الميلي ثانية)
# =====================================================================
def parse_arabic_report_pattern(md_content, input_folder):
    """يستخرج أسماء المقاطع والتوقيتات بدقة مهما كانت صيغة التقرير أو وجود أجزاء متعددة"""
    blocks = re.split(r'\n(?=###\s*)', md_content)
    clips = []
    
    files = os.listdir(input_folder) if os.path.exists(input_folder) else []

    for idx, block in enumerate(blocks, 1):
        if not block.strip():
            continue
            
        file_match = re.search(r'(clip_\d+)', block, re.IGNORECASE)
        time_pattern = r'(\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)'
        time_match = re.search(rf'من\s*[`*]*\s*{time_pattern}\s*[`*]*\s*إلى\s*[`*]*\s*{time_pattern}\s*[`*]*', block)
        
        if file_match and time_match:
            base_name = file_match.group(1)
            start_time = time_match.group(1)
            end_time = time_match.group(2)
            
            matching_media = None
            for f in files:
                if f.lower().startswith(base_name.lower()) and f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.aac')):
                    matching_media = os.path.join(input_folder, f)
                    break
            
            if matching_media:
                ext = os.path.splitext(matching_media)[1].lower()
                output_extension = '.mp3' if ext in ['.mp3', '.wav', '.m4a', '.aac'] else '.mp4'
                
                clip_counter = len(clips) + 1
                output_clip_name = f"final_{base_name}_part{clip_counter}{output_extension}"

                clips.append({
                    'name': output_clip_name,
                    'media_path': matching_media,
                    'start': start_time,
                    'end': end_time
                })
            else:
                print(f"[!] Warning: Could not find media file starting with '{base_name}' for block {idx}.")
        
    return clips

# =====================================================================
# الدالة الرئيسية للتشغيل
# =====================================================================
def process_markdown_clips(input_folder, md_file_path, output_folder, padding_start=0, padding_end=0, pattern_type="default", reencode=True):
    if not os.path.exists(input_folder):
        print(f"[!] Error: Input folder '{input_folder}' does not exist.")
        return

    if not os.path.exists(md_file_path):
        print(f"[!] Error: Markdown file '{md_file_path}' does not exist.")
        return

    with open(md_file_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    print(f"[*] Using parser pattern: '{pattern_type}' | Re-encode: {reencode}")
    if pattern_type == "arabic_report":
        clips_to_process = parse_arabic_report_pattern(md_content, input_folder)
    else:
        clips_to_process = parse_default_pattern(md_content, input_folder)

    if not clips_to_process:
        print(f"[!] Error: No clips found using pattern '{pattern_type}'.")
        return

    os.makedirs(output_folder, exist_ok=True)
    print(f"[*] Found {len(clips_to_process)} clips to process. Verifying files one by one...")

    for idx, clip in enumerate(clips_to_process, 1):
        media_file = clip['media_path']
        start_time = clip['start']
        end_time = clip['end']
        output_clip_name = clip['name']
        output_clip_path = os.path.join(output_folder, output_clip_name)

        if os.path.exists(output_clip_path) and os.path.getsize(output_clip_path) > 0:
            print(f"[!] Skip: File '{output_clip_name}' already exists and is not empty. Skipping...")
            continue

        print(f"[*] Cutting Clip {idx} ({output_clip_name}) from {os.path.basename(media_file)}: [{start_time} --> {end_time}] -> Saving to {output_clip_path}")
        
        success = cut_media_segment(media_file, start_time, end_time, output_clip_path, padding_start, padding_end, reencode)
        if success:
            print(f"[+] Clip {idx} generated successfully.")
        else:
            print(f"[!] Failed to generate Clip {idx}.")

    print(f"[Success] All clipping tasks completed. Output saved in: {output_folder}")

if __name__ == "__main__":
    input_directory = "output/al_qesa/analyzer/output_clips" 
    markdown_file = "analyzed_subtitles.md"
    output_directory = "final_output_clips"
    
    my_padding_start = 1.0  
    my_padding_end = 1.0    

    process_markdown_clips(
        input_folder=input_directory, 
        md_file_path=markdown_file, 
        output_directory=output_directory,
        padding_start=my_padding_start,
        padding_end=my_padding_end,
        pattern_type="arabic_report",
        reencode=True  # اجعلها False إذا أردت القص السريع بدون إعادة ترميز
    )