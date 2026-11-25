"""
subtitle_converter.py
=====================
وحدة مستقلة لتحويل ملفات الترجمة (.srt / .vtt) إلى ملفات نصية نظيفة (.txt).

الميزة الأساسية:
    convert_subtitles_folder(src_dir, out_dir)
        - تبحث في src_dir عن كل ملفات .srt و .vtt
        - تحوّل كل ملف إلى .txt بنفس الاسم في out_dir
        - تتجاوز أي ملف .txt موجود مسبقاً في out_dir (ميزة Skip)

لا تعتمد على أي مكتبة خارجية — Python قياسي فقط.

الاستخدام من سطر الأوامر:
    python subtitle_converter.py <src_dir> <out_dir>
    python subtitle_converter.py <src_dir> <out_dir> -r --overwrite
    python subtitle_converter.py <file.srt> [out_dir]
"""

import os
import re
import sys


# ======================================================================
# الأنماط المشتركة (تدعم SRT و VTT معاً)
# ======================================================================
# يدعم: 00:00:01,500 (SRT) — 00:00:01.500 (VTT) — 00:01.500 (VTT بدون ساعات)
_TIMESTAMP = r'\d{1,2}:\d{2}(?::\d{2})?[,\.]\d{3}'
_TIMESTAMP_LINE = re.compile(
    rf'({_TIMESTAMP})\s*-->\s*({_TIMESTAMP})[^\n]*\n(.*?)(?=\n(?:{_TIMESTAMP})\s*-->|\Z)',
    re.DOTALL,
)
_HTML_TAG = re.compile(r'<[^>]+>')
_VTT_HEADER = re.compile(r'^(WEBVTT|NOTE|STYLE|REGION)\b.*$', re.MULTILINE)


def _short_timestamp(ts: str) -> str:
    """إزالة الميلي ثانية من 'HH:MM:SS,mmm' أو 'HH:MM:SS.mmm'."""
    return re.split(r'[,\.]', ts, maxsplit=1)[0]


# ======================================================================
# 1) تحويل ملف واحد (يبقى كما هو — تستخدمه media_downloader)
# ======================================================================
def convert_subtitle_to_txt(
    sub_file_path: str,
    output_txt_path: str = None,
    remove_duplicate_lines: bool = True,
) -> str:
    """
    تحويل ملف ترجمة واحد (.srt أو .vtt) إلى ملف .txt منسق.

    Parameters
    ----------
    sub_file_path : str
        مسار ملف الترجمة.
    output_txt_path : str, optional
        مسار ملف الإخراج. إن لم يُحدد → نفس المسار بامتداد .txt.
    remove_duplicate_lines : bool, default True
        حذف الأسطر النصية المكررة (مفيد جداً مع الترجمة التلقائية من يوتيوب).

    Returns
    -------
    str or None
        مسار ملف TXT الناتج، أو None عند الفشل.
    """
    if not sub_file_path or not os.path.exists(sub_file_path):
        print(f"[-] Subtitle file not found: {sub_file_path}")
        return None

    if output_txt_path is None:
        output_txt_path = os.path.splitext(sub_file_path)[0] + ".txt"

    try:
        with open(sub_file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            content = f.read()

        # إزالة رؤوس VTT
        content = _VTT_HEADER.sub("", content)

        blocks, seen = [], set()
        for start_ts, end_ts, raw_text in _TIMESTAMP_LINE.findall(content):
            clean = _HTML_TAG.sub("", raw_text)
            clean = " ".join(clean.split())

            if not clean:
                continue
            if remove_duplicate_lines and clean in seen:
                continue

            seen.add(clean)
            blocks.append(
                f"{_short_timestamp(start_ts)} --> {_short_timestamp(end_ts)}\n{clean}\n"
            )

        if not blocks:
            print(f"[-] No cues parsed from: {sub_file_path}")
            return None

        os.makedirs(os.path.dirname(os.path.abspath(output_txt_path)), exist_ok=True)
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(blocks))

        print(f"[+] Converted: {os.path.basename(sub_file_path)} -> {output_txt_path} ({len(blocks)} cues)")
        return output_txt_path

    except Exception as e:
        print(f"[-] Failed to convert '{sub_file_path}': {e}")
        return None


# ======================================================================
# 2) الدالة الجديدة: تحويل مجلد كامل مع ميزة Skip
# ======================================================================
def convert_subtitles_folder(
    src_dir: str,
    out_dir: str,
    extensions: tuple = (".srt", ".vtt"),
    recursive: bool = False,
    remove_duplicate_lines: bool = True,
    overwrite: bool = False,
) -> dict:
    """
    تحويل كل ملفات الترجمة داخل مجلد إلى .txt في مجلد إخراج منفصل.

    - يُحفظ كل ملف .txt بنفس اسم الملف الأصلي (مثلاً video.ar.srt -> video.ar.txt).
    - إذا وُجد ملف .txt مسبقاً في out_dir بنفس الاسم → يُتجاوز (Skip) ما لم
      يكن overwrite=True.
    - مع recursive=True: يتم الحفاظ على هيكل المجلدات الفرعية داخل out_dir
      (لتفادي تصادم الأسماء بين مجلدات مختلفة).

    Parameters
    ----------
    src_dir : str
        مجلد المصدر الذي يحتوي على ملفات .srt و/أو .vtt.
    out_dir : str
        مجلد الإخراج الذي ستُحفظ فيه ملفات .txt.
    extensions : tuple, default ('.srt', '.vtt')
        الامتدادات التي يُبحث عنها.
    recursive : bool, default False
        البحث داخل المجلدات الفرعية أيضاً.
    remove_duplicate_lines : bool, default True
        حذف الأسطر النصية المكررة.
    overwrite : bool, default False
        إن كان True، يعيد التحويل حتى لو كان .txt موجوداً (يعطّل ميزة Skip).

    Returns
    -------
    dict
        {
            "converted": [قائمة مسارات .txt الجديدة],
            "skipped":   [قائمة مسارات .srt/.vtt التي تم تجاوزها],
            "failed":    [قائمة مسارات .srt/.vtt التي فشل تحويلها],
        }
    """
    result = {"converted": [], "skipped": [], "failed": []}

    # التحقق من المدخلات
    if not os.path.isdir(src_dir):
        print(f"[-] Source directory not found: {src_dir}")
        return result

    os.makedirs(out_dir, exist_ok=True)

    # جمع ملفات الترجمة
    subtitle_files = []
    if recursive:
        for root, _, files in os.walk(src_dir):
            for fname in files:
                if fname.lower().endswith(extensions):
                    subtitle_files.append(os.path.join(root, fname))
    else:
        for fname in os.listdir(src_dir):
            full = os.path.join(src_dir, fname)
            if os.path.isfile(full) and fname.lower().endswith(extensions):
                subtitle_files.append(full)

    subtitle_files.sort()

    if not subtitle_files:
        print(f"[!] No .srt / .vtt files found in: {src_dir}")
        return result

    print(f"[*] Found {len(subtitle_files)} subtitle file(s) in '{src_dir}'")
    print(f"[*] Output directory: '{out_dir}'")
    print(f"[*] Overwrite existing .txt: {overwrite}")
    print("-" * 60)

    for sub_path in subtitle_files:
        # تحديد مسار الإخراج
        if recursive:
            rel = os.path.relpath(sub_path, src_dir)
            rel_dir = os.path.dirname(rel)
            target_dir = os.path.join(out_dir, rel_dir) if rel_dir else out_dir
        else:
            target_dir = out_dir

        os.makedirs(target_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(sub_path))[0]
        txt_path = os.path.join(target_dir, base_name + ".txt")

        # 🔑 ميزة Skip
        if os.path.exists(txt_path) and not overwrite:
            print(f"[~] Skip (exists): {os.path.basename(sub_path)} -> {txt_path}")
            result["skipped"].append(sub_path)
            continue

        # التحويل
        out = convert_subtitle_to_txt(
            sub_path,
            output_txt_path=txt_path,
            remove_duplicate_lines=remove_duplicate_lines,
        )
        if out:
            result["converted"].append(out)
        else:
            result["failed"].append(sub_path)

    # ملخص نهائي
    print("-" * 60)
    print(
        f"[*] Summary: "
        f"{len(result['converted'])} converted, "
        f"{len(result['skipped'])} skipped, "
        f"{len(result['failed'])} failed"
    )
    return result


# ======================================================================
# 3) دالة مساعدة: البحث عن ترجمة لملف وسائط (تستخدمها media_downloader)
# ======================================================================
def find_subtitle_for_media(media_path: str, search_dir: str = None) -> str:
    """
    البحث عن ملف ترجمة (.srt/.vtt) يطابق اسم ملف الوسائط في المجلد.
    """
    if not media_path:
        return None

    if search_dir is None:
        search_dir = os.path.dirname(os.path.abspath(media_path))

    if not os.path.isdir(search_dir):
        return None

    base_name = os.path.splitext(os.path.basename(media_path))[0]

    for fname in sorted(os.listdir(search_dir)):
        if fname.startswith(base_name) and fname.lower().endswith((".srt", ".vtt")):
            return os.path.join(search_dir, fname)
    return None


# ======================================================================
# 4) نقطة دخول CLI
# ======================================================================
def _main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert .srt / .vtt subtitles to .txt",
    )
    parser.add_argument("src", help="Source file (.srt/.vtt) or directory")
    parser.add_argument(
        "out",
        nargs="?",
        help="Output directory. For a single file: optional folder. "
             "For a folder: required (defaults to src).",
    )
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="Recurse into subdirectories (folder mode only)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-convert even if .txt already exists")
    parser.add_argument("--keep-duplicates", action="store_true",
                        help="Keep duplicate text lines")

    args = parser.parse_args(argv[1:])
    remove_dups = not args.keep_duplicates

    # وضع المجلد
    if os.path.isdir(args.src):
        out_dir = args.out or args.src
        result = convert_subtitles_folder(
            src_dir=args.src,
            out_dir=out_dir,
            recursive=args.recursive,
            overwrite=args.overwrite,
            remove_duplicate_lines=remove_dups,
        )
        return 0 if not result["failed"] else 2

    # وضع الملف الواحد
    if os.path.isfile(args.src):
        out_txt = None
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            base = os.path.splitext(os.path.basename(args.src))[0]
            out_txt = os.path.join(args.out, base + ".txt")

        # ميزة Skip في وضع الملف الواحد أيضاً
        if out_txt and os.path.exists(out_txt) and not args.overwrite:
            print(f"[~] Skip (exists): {out_txt}")
            return 0

        result = convert_subtitle_to_txt(
            args.src, out_txt, remove_duplicate_lines=remove_dups
        )
        return 0 if result else 2

    print(f"[-] Not found: {args.src}")
    return 2


# ======================================================================
# دالة التشغيل الموحّدة (تُستخدم من main.py)
# ======================================================================
def run_conversion(
    src_dir: str,
    out_dir: str = None,
    **kwargs,
) -> dict:
    """
    غلاف تشغيلي: يحوّل كل ملفات .srt/.vtt في مجلد إلى .txt.
    يتجاوز أي ملف .txt موجود مسبقاً (Skip تلقائي).
    كل الأخطاء تُعالَج داخلياً، لذا يمكن استدعاؤها بسطر واحد.

    Parameters
    ----------
    src_dir : str
        مجلد المصدر (الذي يحتوي على ملفات الترجمة).
    out_dir : str, optional
        مجلد الإخراج. افتراضياً = src_dir (in-place).
    **kwargs
        تُمرَّر إلى convert_subtitles_folder
        (recursive, overwrite, remove_duplicate_lines).

    Returns
    -------
    dict
        {
          "ok": bool,                 # نجحت العملية بدون فشل؟
          "converted": [...],
          "skipped":   [...],
          "failed":    [...],
          "error":     str | None,
        }
    """
    if out_dir is None:
        out_dir = src_dir

    result = {
        "ok": False,
        "converted": [],
        "skipped": [],
        "failed": [],
        "error": None,
    }

    try:
        print(f"\n[*] Converting subtitles to TXT in: {src_dir}")
        r = convert_subtitles_folder(src_dir=src_dir, out_dir=out_dir, **kwargs)
        result["converted"] = r.get("converted", [])
        result["skipped"] = r.get("skipped", [])
        result["failed"] = r.get("failed", [])
        result["ok"] = len(result["failed"]) == 0

        print(
            f"[*] Post-conversion: "
            f"{len(result['converted'])} new, "
            f"{len(result['skipped'])} skipped, "
            f"{len(result['failed'])} failed"
        )
        for f in result["failed"]:
            print(f"    [!] Failed: {f}")

    except Exception as e:
        result["error"] = str(e)
        print(f"[-] Subtitle conversion workflow failed: {e}")

    return result