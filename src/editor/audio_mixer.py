import os
import re
import shutil
import subprocess
from pathlib import Path


def extract_audio_tags(md_file_path):
  """Reads a Markdown file and extracts the text inside [audio] ... [/audio] tags,

  handling spaces, newlines, and case-insensitivity.
  """
  try:
    with open(md_file_path, "r", encoding="utf-8") as f:
      content = f.read()
      # استخدام التعبير الجيني (Regex) ليكون أكثر مرونة مع المسافات والأسطر الجديدة
      match = re.search(r"\[audio\]\s*(.*?)\s*\[/audio\]", content, re.IGNORECASE | re.DOTALL)
      if match:
        tag_value = match.group(1).strip()
        if tag_value:
          return tag_value
  except Exception as e:
    print(f"[-] [ERROR] Failed to read file {md_file_path}: {e}")
  return None


def process_audio_library(md_dir, folders_dir, effects_dir):
  """Main function to process audio files, mix background effects with improved logging and error handling."""

  md_path = Path(md_dir)
  folders_path = Path(folders_dir)
  effects_path = Path(effects_dir)

  print(f"\n[*] Starting audio mixing pipeline...")
  print(f"    - Markdown Directory: {md_path.resolve()}")
  print(f"    - Target Folders Dir: {folders_path.resolve()}")
  print(f"    - Effects Directory:  {effects_path.resolve()}")

  if not md_path.exists():
    print(f"[-] [ERROR] Markdown directory does not exist: {md_path}")
    return

  if not folders_path.exists():
    print(f"[-] [ERROR] Folders directory does not exist: {folders_path}")
    return

  if not effects_path.exists():
    print(f"[-] [ERROR] Effects directory does not exist: {effects_path}")
    return

  md_files = list(md_path.glob("*.md"))
  print(f"[*] Found {len(md_files)} markdown files to process.\n")

  # Iterate through all .md files
  for md_file in md_files:
    base_name = md_file.stem
    print(f"----------------------------------------")
    print(f"[PROCESSING] Markdown File: {md_file.name} (Base: {base_name})")

    # 1. Extract audio effect name from [audio] tag
    audio_filename = extract_audio_tags(md_file)
    if not audio_filename:
      print(f"[-] [SKIP] No valid [audio] tag found in: {md_file.name}")
      continue
    
    print(f"[+] [FOUND] Audio tag value: '{audio_filename}'")

    # 2. Locate target folder with matching name
    target_folder = folders_path / base_name
    if not target_folder.exists() or not target_folder.is_dir():
      print(f"[-] [WARNING] Target directory not found for '{base_name}': {target_folder}")
      continue

    print(f"[+] [FOUND] Target folder: {target_folder}")

    # Find AUDIO files inside the target folder
    audio_extensions = [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]
    audio_files = [
        f for f in target_folder.glob("*.*") if f.suffix.lower() in audio_extensions
    ]

    if not audio_files:
      print(f"[-] [WARNING] No audio files found inside target folder: {target_folder}")
      continue

    original_file = audio_files[0]
    print(f"[+] [FOUND] Original audio file to process: {original_file.name}")

    # Check if the file was already processed (exists in .trash folder)
    trash_path = target_folder / ".trash"
    trash_destination = trash_path / original_file.name
    if trash_destination.exists():
      print(f"[*] [SKIP] File '{original_file.name}' is already processed (found in .trash).")
      continue

    # 3. Search for the background audio effect in the effects directory
    nasheed_file = None
    
    # البحث بالاسم المباشر مع الامتدادات المختلفة
    for ext in [".mp3", ".wav", ".m4a", ".aac", ".ogg"]:
      potential_path = effects_path / f"{audio_filename}{ext}"
      if potential_path.exists():
        nasheed_file = potential_path
        break

    # البحث الجزئي إذا لم يتم العثور بالاسم المطابق تماماً
    if not nasheed_file:
      for f in effects_path.iterdir():
        if audio_filename.lower() in f.name.lower():
          nasheed_file = f
          break

    if not nasheed_file:
      print(
          f"[-] [ERROR] Audio effect matching '{audio_filename}' not found in effects"
          f" directory: {effects_path}"
      )
      continue

    print(f"[+] [FOUND] Background effect file: {nasheed_file.name}")

    temp_output_file = target_folder / f"temp_{original_file.name}"
    final_output_file = target_folder / original_file.name

    # 4. Construct FFmpeg command (مع إضافة تكرار للمؤثر الصوتي حتى نهاية الملف الأصلي)
    command = [
        "ffmpeg",
        "-i",
        str(original_file),
        "-stream_loop",
        "-1",  # تكرار ملف المؤثر الصوتي بلا حدود حتى ينتهي الملف الأصلي
        "-i",
        str(nasheed_file),
        "-filter_complex",
        (
            "[0:a]volume=1.5[a1];"
            "[1:a]volume=0.4[a2];"
            "[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map",
        "[aout]",
        "-c:a",
        "pcm_s16le",
        str(temp_output_file),
        "-y",
    ]


    print("[EXEC] Mixing and re-encoding audio files via FFmpeg...")
    try:
      result = subprocess.run(
          command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
      )

      if result.returncode != 0:
        print(f"[-] [ERROR] FFmpeg execution failed for {original_file.name}")
        print(f"[-] [ERROR DETAILS]:\n{result.stderr.strip()}")
        if temp_output_file.exists():
          temp_output_file.unlink()
        continue

      # 5. Create hidden .trash folder and move original
      trash_path.mkdir(parents=True, exist_ok=True)
      if trash_destination.exists():
        trash_destination.unlink()

      shutil.move(str(original_file), str(trash_destination))
      temp_output_file.rename(final_output_file)

      print(f"[SUCCESS] Updated audio saved in place: {final_output_file.name}")
      print(f"[MOVED] Original file moved to: {trash_destination}")

    except Exception as e:
      print(f"[-] [ERROR] An unexpected error occurred during FFmpeg execution: {e}")
      if temp_output_file.exists():
        temp_output_file.unlink()