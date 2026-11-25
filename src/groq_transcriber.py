import os
import sys
import subprocess
import tempfile
from datetime import timedelta
from typing import Optional
from groq import Groq
from dotenv import load_dotenv

# تحميل متغيرات البيئة (مثل مفتاح Groq API)
load_dotenv()


def format_timestamp(seconds: float) -> str:
    """Convert float seconds into standard SRT timestamp format (HH:MM:SS,mmm)."""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def generate_txt_from_srt(srt_path: str, txt_path: str) -> bool:
    """Generate TXT file from an existing SRT file without calling the API."""
    try:
        if not os.path.exists(srt_path):
            return False
        
        with open(srt_path, "r", encoding="utf-8") as srt_file, \
             open(txt_path, "w", encoding="utf-8") as txt_file:
            
            content = srt_file.read()
            blocks = content.strip().split("\n\n")
            
            for block in blocks:
                lines = block.split("\n")
                if len(lines) >= 3:
                    timestamp_line = lines[1]
                    text_lines = " ".join(lines[2:])
                    txt_file.write(f"[{timestamp_line}]\n")
                    txt_file.write(f"{text_lines}\n\n")
        return True
    except Exception as e:
        print(f"[WARNING] Could not generate TXT from SRT: {str(e)}")
        return False


def extract_audio_with_ffmpeg(media_path: str, output_audio_path: str) -> bool:
    """Extract audio from video or media file using ffmpeg optimized for speech recognition."""
    try:
        command = [
            "ffmpeg",
            "-y",                     
            "-i", media_path,          
            "-vn",                     
            "-acodec", "pcm_s16le",    
            "-ar", "16000",            
            "-ac", "1",                
            output_audio_path
        ]
        
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode != 0:
            print(f"[ERROR] FFmpeg failed: {result.stderr}")
            return False
            
        return True
    except FileNotFoundError:
        print("[ERROR] 'ffmpeg' is not installed or not found in system PATH. Please install FFmpeg.")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error while running ffmpeg: {str(e)}")
        return False


def transcribe_media_folder_groq(
    target_directory: str,
    output_dir: Optional[str] = None,
    model_name: str = "whisper-large-v3-turbo",
    skip_existing: bool = True,
    chunk_size: Optional[int] = None,
    api_key: Optional[str] = None,
    generate_txt: bool = True,  # 👈 البراميتر الجديد لتحديد ما إذا كنت تريد ملف TXT أم لا (الافتراضي True)
) -> None:
    """Scan a folder for media files, extract audio via ffmpeg, and transcribe them using Groq API."""
    
    # التحقق من مفتاح Groq API
    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        print("[ERROR] Groq API Key not found. Please set GROQ_API_KEY in your environment or .env file.")
        return

    client = Groq(api_key=key)

    if not output_dir:
        output_dir = target_directory

    if not os.path.exists(target_directory):
        print(f"[ERROR] Target directory '{target_directory}' does not exist.")
        return

    os.makedirs(output_dir, exist_ok=True)

    supported_extensions = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

    media_files = [
        f for f in os.listdir(target_directory)
        if f.lower().endswith(supported_extensions)
    ]

    if not media_files:
        print(f"[INFO] No supported media files found in '{target_directory}'.")
        return

    print(f"[INFO] Found {len(media_files)} media file(s) to process.")

    for index, file_name in enumerate(media_files, start=1):
        file_stem, _ = os.path.splitext(file_name)
        media_path = os.path.join(target_directory, file_name)

        srt_output_path = os.path.join(output_dir, f"{file_stem}.srt")
        txt_output_path = os.path.join(output_dir, f"{file_stem}.txt")

        # الحالة الأولى: ملف SRT موجود مسبقاً
        if skip_existing and os.path.exists(srt_output_path):
            # يتم توليد الـ TXT فقط إذا كان generate_txt مفعلاً ولم يكن موجوداً مسبقاً
            if generate_txt and not os.path.exists(txt_output_path):
                print(f"\n[{index}/{len(media_files)}] SRT exists, generating missing TXT for: {file_name}")
                if generate_txt_from_srt(srt_output_path, txt_output_path):
                    print(f"[SUCCESS] Generated missing TXT: {txt_output_path}")
            else:
                print(f"[SKIP] Transcripts already exist for: {file_name}")
            continue

        print(f"\n[{index}/{len(media_files)}] Processing file: {file_name}")

        # إنشاء ملف صوتي مؤقت بامتداد wav
        temp_audio_fd, temp_audio_path = tempfile.mkstemp(suffix=".wav")
        os.close(temp_audio_fd)

        try:
            print(f"[INFO] Extracting audio using ffmpeg...")
            if not extract_audio_with_ffmpeg(media_path, temp_audio_path):
                continue

            print(f"[INFO] Sending audio to Groq API...")
            with open(temp_audio_path, "rb") as file_obj:
                response = client.audio.transcriptions.create(
                    file=("audio.wav", file_obj.read()),
                    model=model_name,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                    prompt="اكتب التفريغ بالعامية بالضبط كما ينطق المتحدث دون تحويل للفصحى."
                )

            words = getattr(response, "words", None)
            if not words:
                print(f"[WARNING] No word timestamps returned by Groq for {file_name}")
                continue

            srt_counter = 1
            word_groups = []
            if chunk_size and chunk_size > 0:
                for i in range(0, len(words), chunk_size):
                    word_groups.append(words[i : i + chunk_size])
            else:
                word_groups.append(words)

            # 👈 الكتابة بناءً على قيمة البراميتر generate_txt
            if generate_txt:
                with open(srt_output_path, "w", encoding="utf-8") as srt_file, \
                     open(txt_output_path, "w", encoding="utf-8") as txt_file:
                    
                    for group in word_groups:
                        if not group:
                            continue
                            
                        first_word = group[0]
                        last_word = group[-1]
                        
                        start_time = float(first_word.get("start", 0.0) if isinstance(first_word, dict) else getattr(first_word, "start", 0.0) or 0.0)
                        end_time = float(last_word.get("end", 0.0) if isinstance(last_word, dict) else getattr(last_word, "end", 0.0) or 0.0)

                        text_items = []
                        for w in group:
                            w_text = w.get("word", "") if isinstance(w, dict) else getattr(w, "word", "")
                            if w_text and w_text.strip():
                                text_items.append(w_text.strip())

                        text = " ".join(text_items)
                        if not text:
                            continue

                        start_str = format_timestamp(start_time)
                        end_str = format_timestamp(end_time)

                        srt_file.write(f"{srt_counter}\n")
                        srt_file.write(f"{start_str} --> {end_str}\n")
                        srt_file.write(f"{text}\n\n")

                        txt_file.write(f"[{start_str} --> {end_str}]\n")
                        txt_file.write(f"{text}\n\n")

                        srt_counter += 1

                print(f"[SUCCESS] Saved SRT: {srt_output_path}")
                print(f"[SUCCESS] Saved TXT: {txt_output_path}")
            else:
                # إذا كان generate_txt يساوي False، اكتب ملف الـ SRT فقط
                with open(srt_output_path, "w", encoding="utf-8") as srt_file:
                    
                    for group in word_groups:
                        if not group:
                            continue
                            
                        first_word = group[0]
                        last_word = group[-1]
                        
                        start_time = float(first_word.get("start", 0.0) if isinstance(first_word, dict) else getattr(first_word, "start", 0.0) or 0.0)
                        end_time = float(last_word.get("end", 0.0) if isinstance(last_word, dict) else getattr(last_word, "end", 0.0) or 0.0)

                        text_items = []
                        for w in group:
                            w_text = w.get("word", "") if isinstance(w, dict) else getattr(w, "word", "")
                            if w_text and w_text.strip():
                                text_items.append(w_text.strip())

                        text = " ".join(text_items)
                        if not text:
                            continue

                        start_str = format_timestamp(start_time)
                        end_str = format_timestamp(end_time)

                        srt_file.write(f"{srt_counter}\n")
                        srt_file.write(f"{start_str} --> {end_str}\n")
                        srt_file.write(f"{text}\n\n")

                        srt_counter += 1

                print(f"[SUCCESS] Saved SRT (TXT skipped): {srt_output_path}")

        except Exception as e:
            print(f"[ERROR] Error processing '{file_name}' with Groq: {str(e)}")

        finally:
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except Exception:
                    pass


# --- مثال للاستخدام عند تشغيل السكربت مباشرة ---
if __name__ == "__main__":
    media_directory = "./my_media_folder" 
    output_directory = "./output_results"
    
    transcribe_media_folder_groq(
        target_directory=media_directory,
        output_dir=output_directory,
        model_name="whisper-large-v3-turbo",
        skip_existing=True,
        chunk_size=5,
        generate_txt=True  # اتركها True إذا كنت تريد ملفات TXT، أو اجعلها False إذا كنت تريد ملفات SRT فقط
    )