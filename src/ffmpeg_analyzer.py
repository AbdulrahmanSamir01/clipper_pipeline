import os
import re
import subprocess


def run_command(cmd):
    """Run an external CLI command and return (stdout + stderr, returncode)."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )
        return result.stderr + result.stdout, result.returncode
    except OSError as exc:
        return f"OS error running command: {exc}", 1


def get_audio_duration(audio_path):
    """Return total duration of an audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    output, code = run_command(cmd)
    if code != 0:
        return 0.0
    try:
        return max(0.0, float(output.strip()))
    except ValueError:
        return 0.0


def analyze_audio_window(audio_path, global_mean_db, start_sec=None, end_sec=None):
    """Analyze full audio or a [start_sec, end_sec] window for mean volume and silence."""
    cmd = ["ffmpeg"]
    if start_sec is not None:
        cmd.extend(["-ss", str(start_sec)])
    if end_sec is not None:
        cmd.extend(["-to", str(end_sec)])
    cmd.extend([
        "-i",
        audio_path,
        "-af",
        "volumedetect,silencedetect=n=-45dB:d=0.3",
        "-f",
        "null",
        "-",
    ])

    output, code = run_command(cmd)
    if code != 0:
        return {
            "mean_db": -50.0,
            "silence_sec": 0.0,
            "status": "Unknown",
            "score": 1.0,
            "error": True,
        }

    vol_match = re.search(r"mean_volume:\s*([-\d\.]+)\s*dB", output)
    mean_db = float(vol_match.group(1)) if vol_match else -50.0
    silence_sec = sum(float(s) for s in re.findall(r"silence_duration:\s*([-\d\.]+)", output))
    status = "Above Average" if mean_db > global_mean_db else "Below Average"
    score = round(max(1.0, min(10.0, (mean_db + 50.0) / 5.0)), 1)

    return {
        "mean_db": mean_db,
        "silence_sec": round(silence_sec, 2),
        "status": status,
        "score": score,
        "error": False,
    }


def parse_srt_time_to_sec(timestamp_str):
    """Convert HH:MM:SS,mmm SRT timestamp to seconds."""
    cleaned = timestamp_str.strip().replace(".", ",")
    hours, minutes, rest = cleaned.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")[:3]) / 1000.0
    )


def format_sec_to_srt_time(total_seconds):
    """Convert seconds to HH:MM:SS,mmm SRT timestamp format."""
    clamped = max(0.0, total_seconds)
    hours = int(clamped // 3600)
    minutes = int((clamped % 3600) // 60)
    seconds = int(clamped % 60)
    millis = int(round((clamped - int(clamped)) * 1000))
    if millis == 1000:
        seconds += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def extract_srt_entries(srt_text):
    """Parse SRT blocks into a list of (start_sec, end_sec, text) tuples."""
    pattern = re.compile(
        r"(?:\d+\s*\n)?(\d{2}:\d{2}:\d{2}[,\.]\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{1,3})\s*\n(.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    entries = []
    for start_str, end_str, body in pattern.findall(srt_text):
        entries.append((
            parse_srt_time_to_sec(start_str),
            parse_srt_time_to_sec(end_str),
            body.strip(),
        ))
    return entries


def build_inline_chunk_srt(entries, total_duration, audio_path, global_mean_db, chunk_duration):
    """Build SRT blocks where each minute/chunk embeds its audio analysis at its time window."""
    blocks = []
    block_index = 1
    start_time = 0.0
    minute_number = 1

    while start_time < total_duration:
        end_time = min(start_time + chunk_duration, total_duration)
        metrics = analyze_audio_window(audio_path, global_mean_db, start_time, end_time)

        matching_lines = [
            text for s_sec, _, text in entries if start_time <= s_sec < end_time
        ]
        spoken_preview = " / ".join(matching_lines) if matching_lines else "[No subtitle lines in this window]"

        start_srt = format_sec_to_srt_time(start_time)
        end_srt = format_sec_to_srt_time(end_time)

        analysis_note = (
            f"[Minute {minute_number} | {metrics['mean_db']} dB | {metrics['status']} "
            f"| Score {metrics['score']}/10 | Silence {metrics['silence_sec']}s]\n"
            f"{spoken_preview}"
        )

        blocks.append(f"{block_index}\n{start_srt} --> {end_srt}\n{analysis_note}\n")
        block_index += 1
        minute_number += 1
        start_time = end_time

    return "\n".join(blocks).strip() + "\n"


def find_media_and_subtitle(input_folder):
    """Locate first supported media file and subtitle file inside input_folder."""
    try:
        files = os.listdir(input_folder)
    except OSError as exc:
        print(f"[Error] Cannot list input folder '{input_folder}': {exc}")
        return None, None

    media_exts = (".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a", ".aac")
    sub_exts = (".srt", ".txt")

    media_file = next(
        (os.path.join(input_folder, f) for f in sorted(files) if f.lower().endswith(media_exts)),
        None,
    )
    txt_file = next(
        (os.path.join(input_folder, f) for f in sorted(files) if f.lower().endswith(sub_exts)),
        None,
    )
    return media_file, txt_file


def process_clipper_data(input_folder, output_file_path, mode="chunk", chunk_duration=60):
    """Process media and subtitle files into an annotated SRT output."""
    if os.path.exists(output_file_path) and os.path.getsize(output_file_path) > 0:
        print(f"[Info] Output file '{output_file_path}' already exists. Skipping.")
        return

    if chunk_duration <= 0:
        print("[Error] chunk_duration must be greater than 0.")
        return

    media_file, txt_file = find_media_and_subtitle(input_folder)
    if not media_file or not txt_file:
        print("[Error] Media file or subtitle file not found.")
        return

    os.makedirs(input_folder, exist_ok=True)
    temp_audio = os.path.join(input_folder, "temp_extracted_audio.wav")

    try:
        print(f"[Info] Extracting normalized WAV audio from: {media_file}")
        _, extract_code = run_command([
            "ffmpeg",
            "-y",
            "-i",
            media_file,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            temp_audio,
        ])
        if extract_code != 0 or not os.path.exists(temp_audio):
            print("[Error] Failed to extract audio track.")
            return

        global_metrics = analyze_audio_window(temp_audio, -20.0)
        global_mean_db = global_metrics["mean_db"]
        print(f"[Info] Global Audio Mean Volume: {global_mean_db} dB")

        with open(txt_file, "r", encoding="utf-8") as f:
            content = f.read().replace("\r\n", "\n")

        if mode == "chunk":
            total_duration = get_audio_duration(temp_audio)
            if total_duration <= 0:
                print("[Error] Could not determine audio duration.")
                return

            print(f"[Info] Analyzing audio in inline {chunk_duration}s blocks...")
            entries = extract_srt_entries(content)
            final_output = build_inline_chunk_srt(
                entries, total_duration, temp_audio, global_mean_db, chunk_duration
            )
        else:
            print(f"[Info] Unsupported or legacy mode '{mode}'. Keeping original content.")
            final_output = content

        out_dir = os.path.dirname(output_file_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        with open(output_file_path, "w", encoding="utf-8") as f:
            f.write(final_output)

        print(f"[Success] Results saved at: {output_file_path}")
    finally:
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except OSError as exc:
                print(f"[Warning] Could not remove temp audio '{temp_audio}': {exc}")


if __name__ == "__main__":
    input_directory = "./my_media_folder"
    output_srt = "./output_results/analyzed_subtitles.srt"
    process_clipper_data(input_directory, output_srt, mode="chunk", chunk_duration=120)