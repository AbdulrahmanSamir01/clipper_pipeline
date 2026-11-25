from datetime import datetime
from zoneinfo import ZoneInfo
import os


def add_datetime_to_txt_files(directory_path):
  """Takes a directory path, checks if the timestamp exists at the top of

  each .txt file, updates it if present, or adds it if missing.
  """
  if not os.path.exists(directory_path):
    print(f"Directory not found: {directory_path}")
    return

  # ضبط التوقيت بناءً على توقيت مصر (القاهرة)
  cairo_tz = ZoneInfo("Africa/Cairo")
  current_time = datetime.now(cairo_tz).strftime("%Y-%m-%d %H:%M:%S")
  
  # النمط الثابت لسطر التاريخ للبحث عنه
  header_prefix = "--- Current date and time:"
  new_header = f"{header_prefix} {current_time} ---\n\n"

  for filename in os.listdir(directory_path):
    if filename.endswith(".txt"):
      file_path = os.path.join(directory_path, filename)

      try:
        # قراءة المحتوى الحالي للملف
        with open(file_path, "r", encoding="utf-8") as file:
          content = file.read()

        # فحص ما إذا كان الملف يبدأ بسطر التاريخ القديم
        lines = content.splitlines(keepends=True)
        
        if lines and lines[0].startswith(header_prefix):
          # إذا كان السطر موجوداً، نقوم بتحديث السطر الأول فقط ونبقي باقي المحتوى كما هو
          lines[0] = new_header
          updated_content = "".join(lines)
          action_msg = f"Updated timestamp in: {filename}"
        else:
          # إذا لم يكن موجوداً، نضيفه في البداية
          updated_content = new_header + content
          action_msg = f"Added timestamp to: {filename}"

        # كتابة المحتوى المحدث للملف
        with open(file_path, "w", encoding="utf-8") as file:
          file.write(updated_content)

        print(action_msg)

      except Exception as e:
        print(f"An error occurred while processing {filename}: {e}")


# --- Example Usage ---
# target_directory = "path/to/your/folder"
# add_datetime_to_txt_files(target_directory)