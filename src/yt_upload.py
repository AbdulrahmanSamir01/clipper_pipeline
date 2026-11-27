import os
import re
import json
import time
import base64
import pickle
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pytz
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# --- Helper Functions ---

def clean_tags(tags: List[str]) -> List[str]:
    """Clean video tags and enforce YouTube character and total length limits."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )

    cleaned: List[str] = []
    for tag in tags:
        tag_text = emoji_pattern.sub("", tag).strip()[:100]
        if tag_text:
            cleaned.append(tag_text)

    valid_tags: List[str] = []
    current_length = 0
    for tag in cleaned:
        added_len = len(tag) + (1 if valid_tags else 0)
        if current_length + added_len < 500:
            valid_tags.append(tag)
            current_length += added_len
        else:
            break

    return valid_tags


def parse_markdown_metadata(md_path: Path) -> List[Dict[str, Any]]:
    """Read a Markdown metadata file and extract each video block."""
    if not md_path.exists():
        raise FileNotFoundError(f"Metadata markdown file not found: {md_path}")

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = content.split("---")
    videos: List[Dict[str, Any]] = []

    patterns = {
        "file_name": r"\[FILE_NAME\](.*?)\[/FILE_NAME\]",
        "title": r"\[TITLE\](.*?)\[/TITLE\]",
        "description": r"\[DESCRIPTION\](.*?)\[/DESCRIPTION\]",
        "tags": r"\[TAGS\](.*?)\[/TAGS\]",
        "schedule_time": r"\[SCHEDULE_TIME\](.*?)\[/SCHEDULE_TIME\]",
        "status": r"\[STATUS\](.*?)\[/STATUS\]",
        "reasoning": r"\[REASONING\](.*?)\[/REASONING\]",
    }

    for idx, block in enumerate(blocks):
        if not block.strip():
            continue

        data: Dict[str, Any] = {"block_index": idx}
        for key, pattern in patterns.items():
            match = re.search(pattern, block, re.DOTALL)
            data[key] = match.group(1).strip() if match else None

        if data.get("file_name"):
            videos.append(data)

    return videos


def mark_video_as_done_in_markdown(md_path: Path, file_name: str) -> None:
    """Update or insert [STATUS]done[/STATUS] for a single uploaded video."""
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = content.split("---")
    updated_blocks: List[str] = []

    for block in blocks:
        if f"[FILE_NAME]\n{file_name}" in block or f"[FILE_NAME] {file_name}" in block:
            if "[STATUS]" in block:
                block = re.sub(
                    r"\[STATUS\].*?\[/STATUS\]",
                    "[STATUS]done[/STATUS]",
                    block,
                    flags=re.DOTALL,
                )
            else:
                block = block.strip() + "\n\n[STATUS]done[/STATUS]\n"
        updated_blocks.append(block)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("---".join(updated_blocks))


# --- Models & Services ---

class VideoTask:
    """Represent a single video upload job parsed from metadata."""

    def __init__(self, video_path: Path, metadata: Dict[str, Any]):
        self.video_path = str(video_path)
        self.file_name = metadata.get("file_name") or ""
        self.title = metadata.get("title") or "Untitled Video"
        self.description = metadata.get("description") or ""
        self.raw_schedule_time = metadata.get("schedule_time") or "12:00"
        self.status = (metadata.get("status") or "").lower()

        tags_str = metadata.get("tags") or ""
        raw_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        self.tags = clean_tags(raw_tags)


class CollisionAvoidingScheduler:
    """Parse schedule slots and shift conflicting times by one hour."""

    def __init__(self, state_path: str):
        self.state_path = Path(state_path)
        self.timezone = pytz.timezone("Africa/Cairo")

    def _load_booked_slots(self) -> List[datetime.datetime]:
        """Load previously booked timestamps from JSON state."""
        if not self.state_path.exists():
            return []
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [
                datetime.datetime.fromisoformat(ts).astimezone(self.timezone)
                for ts in data.get("booked_slots", [])
            ]
        except Exception as exc:
            print(f"Warning: Failed to read schedule state ({exc}). Resetting list.")
            return []

    def _save_booked_slot(self, slot_dt: datetime.datetime) -> None:
        """Append a newly scheduled timestamp to the JSON state file."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        booked = self._load_booked_slots()
        booked.append(slot_dt)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump({"booked_slots": [dt.isoformat() for dt in booked]}, f, indent=4)

    def parse_slot(self, time_str: str) -> datetime.datetime:
        """Resolve date/time string into a conflict-free UTC publish timestamp."""
        now = datetime.datetime.now(self.timezone)
        cleaned = time_str.strip()
        slot_dt: Optional[datetime.datetime] = None

        full_match = re.search(
            r"(\d{4}[-/]\d{2}[-/]\d{2})[T\s]+(\d{1,2}:\d{2})", cleaned
        )
        if full_match:
            date_part, time_part = full_match.groups()
            date_part = date_part.replace("/", "-")
            hour, minute = map(int, time_part.split(":"))
            try:
                naive_dt = datetime.datetime.strptime(date_part, "%Y-%m-%d").replace(
                    hour=hour, minute=minute
                )
                slot_dt = self.timezone.localize(naive_dt)
            except ValueError:
                slot_dt = None

        if not slot_dt:
            time_match = re.search(r"(\d{1,2}:\d{2})", cleaned)
            if time_match:
                hour, minute = map(int, time_match.group(1).split(":"))
                target_date = now.date()
                slot_dt = self.timezone.localize(
                    datetime.datetime.combine(target_date, datetime.time(hour, minute))
                )
                if slot_dt <= now:
                    target_date += datetime.timedelta(days=1)
                    slot_dt = self.timezone.localize(
                        datetime.datetime.combine(target_date, datetime.time(hour, minute))
                    )
            else:
                slot_dt = now + datetime.timedelta(hours=1)

        booked_slots = self._load_booked_slots()
        while any(abs((slot_dt - booked).total_seconds()) < 60 for booked in booked_slots):
            print(
                f"Schedule conflict at {slot_dt.strftime('%Y-%m-%d %H:%M')}. "
                "Shifting by 1 hour..."
            )
            slot_dt += datetime.timedelta(hours=1)

        self._save_booked_slot(slot_dt)
        return slot_dt.astimezone(pytz.UTC)


class SecretCredentialsProvider:
    """Load OAuth client secrets and tokens from env vars with file fallback."""

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    def __init__(
        self,
        client_secret_env: str = "YT_CLIENT_SECRET_JSON",
        token_b64_env: str = "YT_TOKEN_B64",
        fallback_secret_path: Optional[str] = None,
        fallback_token_path: Optional[str] = None,
    ):
        self.client_secret_env = client_secret_env
        self.token_b64_env = token_b64_env
        self.fallback_secret_path = Path(fallback_secret_path) if fallback_secret_path else None
        self.fallback_token_path = Path(fallback_token_path) if fallback_token_path else None

    def _load_client_config(self) -> Dict[str, Any]:
        """Parse OAuth client configuration from environment JSON or fallback file."""
        raw_env = os.environ.get(self.client_secret_env, "").strip()
        if raw_env:
            try:
                parsed = json.loads(raw_env)
                if "installed" in parsed or "web" in parsed:
                    return parsed
                raise ValueError("Missing 'installed' or 'web' key in client secret JSON.")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {self.client_secret_env}") from exc

        if self.fallback_secret_path and self.fallback_secret_path.exists():
            with open(self.fallback_secret_path, "r", encoding="utf-8") as f:
                return json.load(f)

        raise RuntimeError(
            f"Client secret not found in {self.client_secret_env} or fallback path."
        )

    def _load_saved_credentials(self) -> Optional[Credentials]:
        """Load credentials from Base64 env var or fallback pickle file."""
        raw_b64 = os.environ.get(self.token_b64_env, "").strip()
        if raw_b64:
            try:
                decoded_bytes = base64.b64decode(raw_b64, validate=True)
                creds = pickle.loads(decoded_bytes)
                if isinstance(creds, Credentials):
                    return creds
                raise ValueError("Decoded token is not a valid Credentials object.")
            except Exception as exc:
                raise ValueError(f"Failed to decode {self.token_b64_env}: {exc}") from exc

        if self.fallback_token_path and self.fallback_token_path.exists():
            with open(self.fallback_token_path, "rb") as f:
                creds = pickle.load(f)
                return creds if isinstance(creds, Credentials) else None

        return None

    def _save_credentials(self, creds: Credentials) -> None:
        """Persist refreshed credentials locally only if a fallback token path is set."""
        if not self.fallback_token_path:
            return
        self.fallback_token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.fallback_token_path, "wb") as f:
            pickle.dump(creds, f)

    def get_credentials(self) -> Credentials:
        """Return valid OAuth credentials, refreshing expired tokens automatically."""
        creds = self._load_saved_credentials()

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_credentials(creds)
            return creds

        client_config = self._load_client_config()
        flow = InstalledAppFlow.from_client_config(client_config, self.SCOPES)
        creds = flow.run_local_server(port=0)
        self._save_credentials(creds)
        return creds


class YouTubeUploader:
    """Handle authenticated YouTube Data API v3 video uploads."""

    def __init__(self, creds_provider: SecretCredentialsProvider):
        credentials = creds_provider.get_credentials()
        self.service = build("youtube", "v3", credentials=credentials)

    def _execute_upload(
        self, task: VideoTask, publish_time: datetime.datetime, current_tags: List[str]
    ) -> Dict[str, Any]:
        """Send a single resumable video upload request to YouTube."""
        safe_title = task.title.strip()[:100]
        body = {
            "snippet": {
                "title": safe_title,
                "description": task.description,
                "tags": current_tags,
                "categoryId": "25",
                "defaultLanguage": "ar",
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(task.video_path, chunksize=-1, resumable=True)
        return (
            self.service.videos()
            .insert(part="snippet,status", body=body, media_body=media)
            .execute()
        )

    def upload(self, task: VideoTask, publish_time: datetime.datetime) -> Dict[str, Any]:
        """Upload video with automatic fallback tag trimming on invalid tag errors."""
        tags_to_try = list(task.tags)
        try:
            return self._execute_upload(task, publish_time, tags_to_try)
        except HttpError as err:
            if "invalidTags" not in str(err) and "invalidVideoKeywords" not in str(err):
                raise
            print("Warning: Invalid tags error. Retrying with fewer tags...")

        while len(tags_to_try) > 1:
            tags_to_try.pop()
            try:
                print(f"Retrying upload with {len(tags_to_try)} tags...")
                return self._execute_upload(task, publish_time, tags_to_try)
            except HttpError as err:
                if "invalidTags" not in str(err) and "invalidVideoKeywords" not in str(err):
                    raise

        print("Warning: Uploading video without tags...")
        return self._execute_upload(task, publish_time, [])


# --- Main Logic ---

def upload_videos_from_markdown(
    video_dir: str,
    metadata_md_path: str,
    state_json: str,
    creds_provider: SecretCredentialsProvider,
    channel_label: str,
) -> None:
    """Process pending videos from Markdown metadata and upload scheduled items."""
    print(f"Starting upload pipeline for channel: {channel_label}")

    md_path = Path(metadata_md_path)
    try:
        videos_metadata = parse_markdown_metadata(md_path)
    except Exception as exc:
        print(f"Error loading metadata: {exc}")
        return

    if not videos_metadata:
        print("No video metadata blocks found.")
        return

    try:
        uploader = YouTubeUploader(creds_provider)
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return

    scheduler = CollisionAvoidingScheduler(state_json)

    for meta_data in videos_metadata:
        file_name = meta_data.get("file_name")
        if not file_name:
            continue

        video_file_mp4 = Path(file_name).with_suffix(".mp4")
        task = VideoTask(Path(video_dir) / video_file_mp4, meta_data)

        if task.status == "done":
            print(f"Skipping already uploaded video: {file_name}")
            continue

        if not Path(task.video_path).exists():
            print(f"Skipping missing video file: {task.video_path}")
            continue

        try:
            publish_dt = scheduler.parse_slot(task.raw_schedule_time)
            print(f"Uploading '{task.title}' ({file_name})...")
            uploader.upload(task, publish_dt)
            mark_video_as_done_in_markdown(md_path, file_name)
            print(f"Uploaded and marked done. Scheduled UTC: {publish_dt}")
            time.sleep(2)
        except Exception as exc:
            print(f"Failed processing {file_name}: {exc}")

    print("Batch processing finished.")


if __name__ == "__main__":
    provider = SecretCredentialsProvider(
        client_secret_env="YT_CLIENT_SECRET_JSON",
        token_b64_env="YT_TOKEN_B64",
    )
    upload_videos_from_markdown(
        video_dir="outputs/almowaten-news/Final",
        metadata_md_path="outputs/almowaten-news/scripts_details/metadata_plan.md",
        state_json="cc/almowaten-news/db/yt_upload/last_state.json",
        creds_provider=provider,
        channel_label="AlMowatenChannel",
    )