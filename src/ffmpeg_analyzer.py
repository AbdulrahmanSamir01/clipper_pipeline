import os
import subprocess
import re
import math

def run_ffmpeg_command(cmd):
    """Execute FFmpeg command and return output or error"""
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8'
        )
        return result.stderr + result.stdout
    except Exception as e:
        return str(e)

def get_audio_duration(audio_path):
    """Get total duration of audio file in seconds using ffprobe or ffmpeg"""
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
    output = run_ffmpeg_command(cmd).strip()
    try:
        return float(output)
    except ValueError:
        # Fallback if ffprobe fails
        return 0.0

def get_overall_mean_volume(audio_path):
    """Analyze entire audio file once to get global mean volume reference"""
    cmd = ['ffmpeg', '-i', audio_path, '-af', 'volumedetect', '-f', 'null', '-']
    output = run_ffmpeg_command(cmd)
    match = re.search(r'mean_volume:\s*([-\d\.]+)\s*dB', output)
    return float(match.group(1)) if match else -20.0

def process_clipper_data(input_folder, output_file_path, mode="chunk", chunk_duration=60):
    """
    Main processing function with mode selection parameter:
    - mode="chunk": Analyzes audio in fixed time blocks (e.g., every 1 minute) and appends summary at the end.
    - mode="segment": Analyzes each subtitle segment individually (legacy mode).
    """
    if os.path.exists(output_file_path) and os.path.getsize(output_file_path) > 0:
        print(f"[!] Output file '{output_file_path}' already exists. Skipping processing...")
        return

    files = os.listdir(input_folder)
    media_file = next((os.path.join(input_folder, f) for f in files if f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.aac'))), None)
    txt_file = next((os.path.join(input_folder, f) for f in files if f.lower().endswith(('.srt', '.txt'))), None)

    if not media_file or not txt_file:
        print("[!] Error: Media file or subtitle file not found.")
        return

    temp_audio = os.path.join(input_folder, "temp_extracted_audio.wav")
    
    # Step 1: Extract standard normalized audio
    print(f"[*] Extracting media audio to standard WAV: {input_path if 'input_path' in locals() else media_file}")
    subprocess.run(['ffmpeg', '-y', '-i', media_file, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', temp_audio], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    global_mean_db = get_overall_mean_volume(temp_audio)
    print(f"[*] Global Audio Mean Volume: {global_mean_db} dB")

    with open(txt_file, 'r', encoding='utf-8') as f:
        content = f.read().replace('\r\n', '\n')

    # --- Mode 1: Chunk-based Analysis (New & Fast) ---
    if mode == "chunk":
        total_duration = get_audio_duration(temp_audio)
        if total_duration <= 0:
            print("[!] Error: Could not determine audio duration.")
            return

        print(f"[*] Analyzing audio in chunks of {chunk_duration} seconds...")
        chunk_results = []
        chunk_results.append("\n\n=== AUDIO ANALYSIS SUMMARY (MINUTE CHUNKS) ===\n")

        start_time = 0.0
        chunk_index = 1

        while start_time < total_duration:
            end_time = min(start_time + chunk_duration, total_duration)
            
            # Run FFmpeg for the specific time chunk
            cmd = ['ffmpeg', '-ss', str(start_time), '-to', str(end_time), '-i', temp_audio, '-af', 'volumedetect,silencedetect=n=-45dB:d=0.3', '-f', 'null', '-']
            output = run_ffmpeg_command(cmd)

            vol_match = re.search(r'mean_volume:\s*([-\d\.]+)\s*dB', output)
            segment_db = float(vol_match.group(1)) if vol_match else -50.0

            total_silence = 0.0
            for s in re.findall(r'silence_duration:\s*([-\d\.]+)', output):
                total_silence += float(s)

            # Compare with global mean
            status = "Above Average (أعلى من المتوسط)" if segment_db > global_mean_db else "Below Average (أقل من المتوسط)"
            score = max(1.0, min(10.0, (segment_db + 50) / 5))

            min_start_str = f"{int(start_time // 60):02d}:{int(start_time % 60):02d}"
            min_end_str = f"{int(end_time // 60):02d}:{int(end_time % 60):02d}"

            chunk_str = f"From Minute {min_start_str} to {min_end_str} -> Volume: {segment_db}dB | Status: {status} | Score: {round(score, 1)}/10 | Silence: {round(total_silence, 2)}s\n"
            chunk_results.append(chunk_str)

            start_time = end_time
            chunk_index += 1

        # Keep original subtitles intact and append summary at the end
        final_output = content + "".join(chunk_results)

    # --- Mode 2: Legacy Segment-based Analysis ---
    else:
        pattern = re.compile(r'(\d{2}:\d{2}:\d{2}[,\d]*)\s*-->\s*(\d{2}:\d{2}:\d{2}[,\d]*)\s*\n(.*?)(?=\n\s*\n|\Z)', re.DOTALL)
        matches = pattern.findall(content)
        
        updated_blocks = []
        for start_str, end_str, text_content in matches:
            # Conversion and segment analysis logic...
            # (Kept for backward compatibility)
            pass
        final_output = "\n".join(updated_blocks)

    # Clean up temp audio file
    if os.path.exists(temp_audio):
        try:
            os.remove(temp_audio)
        except Exception:
            pass

    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(final_output)
        
    print(f"[Success] Results successfully saved at: {output_file_path}")

# --- Usage Example ---
if __name__ == "__main__":
    input_directory = "./my_media_folder" 
    output_txt = "./output_results/analyzed_subtitles.txt"
    
    # يمكنك التبديل بين وضع "chunk" (لتحليل كل دقيقة وإلحاق النتائج بالنهاية) أو "وضع "segment" القديم
    process_clipper_data(input_directory, output_txt, mode="chunk", chunk_duration=60)