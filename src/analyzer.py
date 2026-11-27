import json
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

DEFAULT_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
]

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_key_id(name: str) -> str:
    """Sanitize a string for safe checkpoint filenames without path traversal."""
    cleaned = _SAFE_NAME_RE.sub("_", Path(name).name)
    return cleaned[:80] or "default"


def load_api_keys(api_keys: list[str] | str | None = None) -> list[str]:
    """Load and deduplicate Gemini API keys while preserving order."""
    # Gather raw key strings from explicit arguments first, then environment variables.
    raw_keys: list[str] = []
    if isinstance(api_keys, list):
        raw_keys.extend(str(k) for k in api_keys if k)
    elif isinstance(api_keys, str) and api_keys.strip():
        raw_keys.extend(api_keys.split(","))

    if not raw_keys:
        for env_name in ("GEMINI_API_KEYS", "GEMINI_API_KEY"):
            val = os.getenv(env_name, "")
            if val.strip():
                raw_keys.extend(val.split(","))

    # Deduplicate keys after trimming surrounding whitespace.
    unique_keys: list[str] = []
    seen: set[str] = set()
    for key in raw_keys:
        cleaned = key.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_keys.append(cleaned)

    if not unique_keys:
        raise ValueError("No valid Gemini API keys provided.")
    return unique_keys


def resolve_models(
    model: str | list[str] | None = None,
    models: list[str] | str | None = None,
) -> list[str]:
    """Resolve and deduplicate candidate Gemini model names."""
    # Pick explicit models list first, then single model fallback, then env or defaults.
    raw_models: list[str] = []
    source = models if models is not None else model
    if isinstance(source, list):
        raw_models.extend(str(m) for m in source if m)
    elif isinstance(source, str) and source.strip():
        raw_models.extend(source.split(","))

    if not raw_models:
        env_models = os.getenv("GEMINI_MODELS", "").strip()
        raw_models.extend(env_models.split(",") if env_models else DEFAULT_MODELS)

    # Preserve order while removing duplicates and blank entries.
    unique_models: list[str] = []
    seen: set[str] = set()
    for m in raw_models:
        cleaned = m.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_models.append(cleaned)

    if not unique_models:
        raise ValueError("No valid Gemini models configured.")
    return unique_models


def mask_key(api_key: str) -> str:
    """Mask an API key for safe terminal logging."""
    # Expose only minimal boundary characters so secrets never leak in logs.
    if not api_key or len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def split_text_into_chunks(text: str, max_chars: int = 50000) -> list[str]:
    """Split text into line-aware chunks up to max_chars."""
    # Validate chunk size so slicing and accumulation always terminate cleanly.
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0.")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        # Hard-split single lines that exceed max_chars on their own.
        if len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
            continue

        # Flush current chunk before appending a line that would overflow max_chars.
        if len(current) + len(line) > max_chars:
            chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)
    return chunks


def _is_transient_error(exc: Exception) -> bool:
    """Check whether an error looks like a transient rate-limit or server overload (503/429)."""
    msg = str(exc).lower()
    return any(token in msg for token in ("503", "429", "unavailable", "resource_exhausted", "overloaded", "timeout"))


def generate_with_rotation(
    prompt: str,
    models: list[str],
    api_keys: list[str],
    max_rounds: int = 3,
    retry_delay: int = 15,
) -> str:
    """Generate response text by rotating through models and API keys across rounds."""
    if not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
    last_error: Exception | None = None

    for round_num in range(1, max_rounds + 1):
        print(f"[*] Gemini round {round_num}/{max_rounds} ({len(models)} model(s), {len(api_keys)} key(s))...")
        saw_transient = False

        for model_name in models:
            for idx, key in enumerate(api_keys, start=1):
                masked = mask_key(key)
                try:
                    # Create a fresh client per key and call generate_content safely.
                    client = genai.Client(api_key=key)
                    response = client.models.generate_content(model=model_name, contents=prompt)
                    result_text = getattr(response, "text", None)
                    if not result_text or not result_text.strip():
                        raise RuntimeError("Empty response received from Gemini.")
                    return result_text
                except Exception as exc:
                    last_error = exc
                    if _is_transient_error(exc):
                        saw_transient = True
                    print(f"[!] Model '{model_name}' | Key #{idx} ({masked}) failed in round {round_num}: {exc}")

        if round_num < max_rounds:
            # Apply a slightly longer backoff when transient 503/429 errors occur.
            sleep_sec = retry_delay * (2 if saw_transient else 1)
            print(f"[!] Round {round_num} failed. Waiting {sleep_sec}s before next round...")
            time.sleep(sleep_sec)

    raise RuntimeError(f"All models and keys failed after {max_rounds} round(s). Last error: {last_error}")


def _load_checkpoint(checkpoint_path: Path) -> dict[str, str]:
    """Load completed chunk outputs keyed by 1-based chunk index string."""
    # Return empty progress if no checkpoint exists or if the file is unreadable.
    if not checkpoint_path.is_file():
        return {}
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[!] Warning: Could not read checkpoint '{checkpoint_path}': {exc}")
    return {}


def _save_checkpoint(checkpoint_path: Path, completed: dict[str, str]) -> None:
    """Persist completed chunk outputs atomically to avoid partial file corruption."""
    # Write to a temporary file first, then atomically replace the checkpoint file.
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = checkpoint_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, checkpoint_path)


def _process_chunks_resumable(
    chunks: list[str],
    prompt_template: str,
    models: list[str],
    api_keys: list[str],
    checkpoint_path: Path,
    max_rounds: int = 3,
    retry_delay: int = 15,
    delay_between_chunks: int = 2,
) -> list[str]:
    """Process chunks sequentially with checkpoint persistence so failed runs resume cleanly."""
    total_chunks = len(chunks)
    completed = _load_checkpoint(checkpoint_path)

    for index, chunk in enumerate(chunks, start=1):
        key_str = str(index)
        # Skip chunks already completed in an earlier run.
        if key_str in completed:
            print(f"    -> Skipping already completed part {index}/{total_chunks} (from checkpoint).")
            continue

        part_header = f"\n\n(Part {index} of {total_chunks})\n---\n\n" if total_chunks > 1 else "\n\n---\n\n"
        chunk_prompt = f"{prompt_template}{part_header}{chunk}"
        print(f"    -> Processing part {index}/{total_chunks}...")

        output_text = generate_with_rotation(
            prompt=chunk_prompt,
            models=models,
            api_keys=api_keys,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )
        completed[key_str] = output_text
        _save_checkpoint(checkpoint_path, completed)

        if index < total_chunks and delay_between_chunks > 0:
            time.sleep(delay_between_chunks)

    # Assemble ordered outputs only once all chunks succeed.
    return [completed[str(i)] for i in range(1, total_chunks + 1)]


def _read_prompt_and_files(input_dir: str, prompt_file_path: str) -> tuple[str, list[Path]]:
    """Validate and load prompt template and sorted .txt input files."""
    prompt_path = Path(prompt_file_path)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    input_path = Path(input_dir)
    if not input_path.is_dir():
        raise NotADirectoryError(f"Input directory not found: {input_path}")

    prompt_template = prompt_path.read_text(encoding="utf-8")
    if not prompt_template.strip():
        raise ValueError(f"Prompt file is empty: {prompt_path}")

    txt_files = sorted(p for p in input_path.glob("*.txt") if p.is_file())
    return prompt_template, txt_files


def _write_and_cleanup(output_file: Path, content: str, checkpoint_path: Path) -> None:
    """Write final Markdown output and remove its checkpoint file after completion."""
    # Ensure parent directory exists and write UTF-8 result cleanly.
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")
    print(f"[+] Saved result to: {output_file}")

    # Remove checkpoint once final output is written cleanly.
    if checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except OSError as exc:
            print(f"[!] Warning: Could not remove checkpoint '{checkpoint_path}': {exc}")


def _run_job_for_text(
    label: str,
    raw_text: str,
    output_file: Path,
    checkpoint_path: Path,
    prompt_template: str,
    models: list[str],
    api_keys: list[str],
    max_chunk_chars: int,
    max_rounds: int,
    retry_delay: int,
) -> None:
    """Run chunked analysis for a single logical text target (file or merged batch)."""
    if output_file.exists() and output_file.stat().st_size > 0:
        print(f"[!] Output '{output_file.name}' already exists. Skipping.")
        return

    print(f"[*] Analyzing {label} with models {models}...")
    chunks = split_text_into_chunks(raw_text, max_chars=max_chunk_chars)
    responses = _process_chunks_resumable(
        chunks=chunks,
        prompt_template=prompt_template,
        models=models,
        api_keys=api_keys,
        checkpoint_path=checkpoint_path,
        max_rounds=max_rounds,
        retry_delay=retry_delay,
    )
    if responses:
        _write_and_cleanup(output_file, "\n\n---\n\n".join(responses), checkpoint_path)


def process_text_files(
    input_dir: str,
    prompt_file_path: str,
    output_dir: str,
    model: str | list[str] | None = None,
    models: list[str] | str | None = None,
    api_keys: list[str] | str | None = None,
    api_key: str | list[str] | None = None,
    merge_files: bool = False,
    merged_output_name: str = "combined_output.md",
    output_file_name: str | None = None,
    max_chunk_chars: int = 50000,
    max_rounds: int = 3,
    retry_delay: int = 15,
) -> None:
    """Analyze .txt files via Gemini with model/key rotation, chunking, and resume support."""
    resolved_keys = load_api_keys(api_keys if api_keys is not None else api_key)
    resolved_models = resolve_models(model=model, models=models)
    prompt_template, txt_files = _read_prompt_and_files(input_dir, prompt_file_path)

    if not txt_files:
        print(f"[!] No .txt files found in '{input_dir}'.")
        return

    out_dir = Path(output_dir)
    ckpt_dir = out_dir / ".checkpoints"
    out_dir.mkdir(parents=True, exist_ok=True)

    if merge_files:
        safe_merged_name = _sanitize_key_id(merged_output_name) or "combined_output.md"
        output_file = out_dir / safe_merged_name
        ckpt_path = ckpt_dir / f"{_sanitize_key_id(safe_merged_name)}.ckpt.json"
        combined_text = "\n\n---\n\n".join(
            f"### File: {p.name}\n{p.read_text(encoding='utf-8')}" for p in txt_files
        )
        _run_job_for_text(
            label=f"merged {len(txt_files)} file(s)",
            raw_text=combined_text,
            output_file=output_file,
            checkpoint_path=ckpt_path,
            prompt_template=prompt_template,
            models=resolved_models,
            api_keys=resolved_keys,
            max_chunk_chars=max_chunk_chars,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )
        return

    for file_path in txt_files:
        custom_name = output_file_name if (output_file_name and len(txt_files) == 1) else f"{file_path.stem}.md"
        safe_name = _sanitize_key_id(custom_name) or f"{_sanitize_key_id(file_path.stem)}.md"
        output_file = out_dir / safe_name
        ckpt_path = ckpt_dir / f"{_sanitize_key_id(file_path.stem)}.ckpt.json"

        _run_job_for_text(
            label=f"'{file_path.name}'",
            raw_text=file_path.read_text(encoding="utf-8"),
            output_file=output_file,
            checkpoint_path=ckpt_path,
            prompt_template=prompt_template,
            models=resolved_models,
            api_keys=resolved_keys,
            max_chunk_chars=max_chunk_chars,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )


process_text_files_gemini = process_text_files