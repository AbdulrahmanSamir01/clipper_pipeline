import os
import sys
from datetime import timedelta
from typing import Optional
from faster_whisper import WhisperModel

os.environ["ORT_LOGGING_LEVEL"] = "3"


def format_timestamp(seconds: float) -> str:
    """Convert float seconds into standard SRT timestamp format (HH:MM:SS,mmm)."""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def transcribe_media_folder(
    target_directory: str,
    output_dir: Optional[str] = None,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    skip_existing: bool = True,
    chunk_size: Optional[int] = None,
) -> None:
    """Main function to scan a folder for video/audio files and transcribe them into SRT & TXT formats."""
    
    # إذا لم يتم تحديد مجلد مخرجات منفصل، سيتم حفظ النتائج في نفس مجلد الملفات الأصلية
    if not output_dir:
        output_dir = target_directory

    if not os.path.exists(target_directory):
        print(f"[ERROR] Target directory '{target_directory}' does not exist.")
        return

    os.makedirs(output_dir, exist_ok=True)

    # دعم ملفات الفيديو والصوت معاً
    supported_extensions = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

    media_files = [
        f for f in os.listdir(target_directory)
        if f.lower().endswith(supported_extensions)
    ]

    if not media_files:
        print(f"[INFO] No supported media files found in '{target_directory}'.")
        return

    print(f"[INFO] Found {len(media_files)} media file(s) for transcription.")

    files_to_process = []
    for file_name in media_files:
        file_stem, _ = os.path.splitext(file_name)
        srt_path = os.path.join(output_dir, f"{file_stem}.srt")
        txt_path = os.path.join(output_dir, f"{file_stem}.txt")

        if skip_existing and os.path.exists(srt_path) and os.path.exists(txt_path):
            print(f"[SKIP] Transcripts already exist for: {file_name}")
        else:
            files_to_process.append(file_name)

    if not files_to_process:
        print("[INFO] All media files have already been transcribed.")
        return

    print(f"[INFO] Loading Faster-Whisper model '{model_size}'...")

    try:
        model = WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
    except Exception as e:
        print(f"[ERROR] Failed to load Whisper model: {str(e)}")
        sys.exit(1)

    PROBABILITY_THRESHOLD = 0.10

    for index, file_name in enumerate(files_to_process, start=1):
        file_stem, _ = os.path.splitext(file_name)
        media_path = os.path.join(target_directory, file_name)

        srt_output_path = os.path.join(output_dir, f"{file_stem}.srt")
        txt_output_path = os.path.join(output_dir, f"{file_stem}.txt")

        print(f"\n[{index}/{len(files_to_process)}] Processing: {file_name}")

        try:
            segments_generator, info = model.transcribe(
                media_path,
                beam_size=5,
                word_timestamps=True,
                condition_on_previous_text=True,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.10,
                    min_speech_duration_ms=100,
                    min_silence_duration_ms=200,
                    speech_pad_ms=500,
                ),
                no_speech_threshold=0.6,
                log_prob_threshold=None,
                hallucination_silence_threshold=2.0
            )

            print(f"[INFO] Detected language '{info.language}' with probability {info.language_probability:.2f}")

            segments = list(segments_generator)
            srt_counter = 1

            # فتح ملف الـ SRT وملف الـ TXT معاً للكتابة المباشرة
            with open(srt_output_path, "w", encoding="utf-8") as srt_file, \
                 open(txt_output_path, "w", encoding="utf-8") as txt_file:
                
                for segment in segments:
                    clean_words = []

                    if segment.words:
                        clean_words = [
                            w for w in segment.words
                            if w.probability >= PROBABILITY_THRESHOLD
                        ]

                    if not clean_words:
                        continue

                    word_groups = []
                    if chunk_size and chunk_size > 0:
                        for i in range(0, len(clean_words), chunk_size):
                            word_groups.append(clean_words[i : i + chunk_size])
                    else:
                        word_groups.append(clean_words)

                    for group in word_groups:
                        start_time = group[0].start
                        end_time = group[-1].end
                        text = " ".join([w.word.strip() for w in group if w.word.strip()])

                        if not text:
                            continue

                        start_str = format_timestamp(start_time)
                        end_str = format_timestamp(end_time)

                        # كتابة ملف الـ SRT بالطريقة التقليدية
                        srt_file.write(f"{srt_counter}\n")
                        srt_file.write(f"{start_str} --> {end_str}\n")
                        srt_file.write(f"{text}\n\n")

                        # كتابة التوقيت داخل ملف الـ TXT بجانب النص
                        txt_file.write(f"[{start_str} --> {end_str}] {text}\n")

                        srt_counter += 1

            print(f"[SUCCESS] Saved SRT: {srt_output_path}")
            print(f"[SUCCESS] Saved TXT: {txt_output_path}")

        except Exception as e:
            print(f"[ERROR] Error processing '{file_name}': {str(e)}")


# --- مثال للاستخدام عند تشغيل السكربت مباشرة ---
if __name__ == "__main__":
    # مسار المجلد الذي يحتوي على الفيديوهات أو الملفات الصوتية
    media_directory = "./my_media_folder" 
    
    # مسار حفظ ملفات الـ SRT والـ TXT الناتجة (يمكن جعله نفس المجلد أو مجلد مستقل)
    output_directory = "./output_results"
    
    # تشغيل الدالة
    transcribe_media_folder(
        target_directory=media_directory,
        output_dir=output_directory,
        model_size="small",
        device="cpu",
        compute_type="int8",
        skip_existing=True
    )