import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
PEXELS_KEY = os.getenv("PEXELS_API_KEY")


class PexelsDownloader:

    @staticmethod
    def _download_file(url: str, save_path: Path) -> bool:
        try:
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"[✓] Pexels Downloaded: {save_path}")
                return True
        except Exception as e:
            print(f"[!] Pexels download error: {e}")
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
        if not PEXELS_KEY:
            print("[!] PEXELS_API_KEY is missing in .env")
            return None

        url = f"https://api.pexels.com/videos/search?query={requests.utils.quote(query)}&per_page=5"
        headers = {"Authorization": PEXELS_KEY}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"[!] Pexels API Error: Status {response.status_code}")
                return None

            videos = response.json().get("videos", [])
            if not videos:
                print(f"[!] No videos found on Pexels for: '{query}'")
                return None

            save_folder = Path(output_dir)
            save_folder.mkdir(parents=True, exist_ok=True)

            print(
                f"[*] Searching Pexels for '{query}' (Target Duration >= {min_duration:.1f}s)..."
            )

            for idx, video in enumerate(videos, 1):
                duration = video.get("duration", 0)

                # طباعة توضيحية تظهر مقارنة مدة الفيديو الفعلي بالمدة المحسوبة من الـ start_time
                if duration < min_duration:
                    print(
                        f"  [-] Video #{idx}: duration ({duration}s) is shorter than required ({min_duration:.1f}s) -> Skipped."
                    )
                    continue
                else:
                    print(
                        f"  [+] Video #{idx}: duration ({duration}s) is longer than or equal to required ({min_duration:.1f}s) -> Valid."
                    )



                best_file = None
                min_diff = float("inf")
                
                # ترتيب أو فحص جميع الملفات المتاحة بالفيديو لاختيار الأقرب للـ target_width و target_height
                for vf in video.get("video_files", []):
                    w = vf.get("width", 0)
                    h = vf.get("height", 0)
                    
                    if not w or not h:
                        continue
                        
                    # حساب الفرق المطلق للأبعاد (العرض والارتفاع)
                    diff = abs(w - target_width) + abs(h - target_height)
                    
                    if diff < min_diff:
                        min_diff = diff
                        best_file = vf

                if best_file and best_file.get("link"):
                    save_path = (
                        save_folder
                        / f"{query}.mp4"
                    )
                    print(
                        f"  [*] Selecting closest dimensions ({best_file.get('width')}x{best_file.get('height')}) for target ({target_width}x{target_height})."
                    )
                    print(
                        f"[*] Downloading Pexels video #{idx} (Duration: {duration}s)..."
                    )
                    if cls._download_file(best_file["link"], save_path):
                        return str(save_path)

            print(
                "[!] All Pexels results checked, but none matched the duration criteria."
            )
            return None
        except Exception as e:
            print(f"[!] Pexels exception: {e}")
            return None