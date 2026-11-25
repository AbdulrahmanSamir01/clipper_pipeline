import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")


class PixabayDownloader:

    @staticmethod
    def _download_file(url: str, save_path: Path) -> bool:
        try:
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"[✓] Pixabay Downloaded: {save_path}")
                return True
        except Exception as e:
            print(f"[!] Pixabay download error: {e}")
        return False

    @classmethod
    def download(
        cls,
        query: str,
        target_width: int,
        target_height: int,
        min_duration: float,
        output_dir: str = "downloads",
    ) -> str | None:
        if not PIXABAY_KEY:
            print("[!] PIXABAY_API_KEY is missing in .env")
            return None

        current_query = query
        response = None

        # حلقة تكرارية لمعالجة خطأ 400 بتقصير الكلمات المفتاحية تدريجياً
        while True:
            # زيادة عدد النتائج إلى 20 للحصول على خيارات أكثر دقة وارتباطاً بالموضوع
            url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={requests.utils.quote(current_query)}&order=popular&per_page=20"

            try:
                response = requests.get(url, timeout=10)
                
                if response.status_code == 400:
                    words = current_query.split()
                    if len(words) <= 1:
                        print(f"[!] Pixabay API Error: Status 400 for query '{current_query}' and cannot shorten further.")
                        return None
                    current_query = " ".join(words[:-1])
                    print(f"[-] Pixabay returned status 400. Shortening query to: '{current_query}'")
                    continue

                if response.status_code != 200:
                    print(f"[!] Pixabay API Error: Status {response.status_code}")
                    return None
                
                break
            except Exception as e:
                print(f"[!] Pixabay exception: {e}")
                return None

        try:
            hits = response.json().get("hits", [])
            if not hits:
                print(f"[!] No videos found on Pixabay for: '{current_query}' (Original: '{query}')")
                return None

            save_folder = Path(output_dir)
            save_folder.mkdir(parents=True, exist_ok=True)

            print(
                f"[*] Evaluating {len(hits)} Pixabay results for '{current_query}' (Target: {target_width}x{target_height}, Min Duration >= {min_duration:.1f}s)..."
            )

            best_overall_video = None
            best_overall_file = None
            min_diff = float("inf")

            # فحص كافة النتائج لاختيار الفيديو الأقرب مطابقة للأبعاد المطلوبة بدقة
            for idx, hit in enumerate(hits, 1):
                duration = hit.get("duration", 0)

                if duration < min_duration:
                    continue

                videos_dict = hit.get("videos", {})
                for quality_key, vf in videos_dict.items():
                    if not isinstance(vf, dict):
                        continue
                    w = vf.get("width", 0)
                    h = vf.get("height", 0)

                    if not w or not h:
                        continue

                    # حساب الفرق المطلق للأبعاد
                    diff = abs(w - target_width) + abs(h - target_height)

                    if diff < min_diff:
                        min_diff = diff
                        best_overall_video = hit
                        best_overall_file = vf

            if best_overall_file and best_overall_file.get("url"):
                save_path = (
                    save_folder
                    / f"{query}.mp4"
                )
                print(
                    f"  [✓] Best Match Found -> Dimensions: ({best_overall_file.get('width')}x{best_overall_file.get('height')}) | Duration: {best_overall_video.get('duration')}s"
                )
                print(f"[*] Downloading selected Pixabay video...")
                if cls._download_file(best_overall_file["url"], save_path):
                    return str(save_path)

            print(
                "[!] All Pixabay results checked, but none matched the duration criteria."
            )
            return None
        except Exception as e:
            print(f"[!] Pixabay exception: {e}")
            return None