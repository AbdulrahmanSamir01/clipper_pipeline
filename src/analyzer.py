import os
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


def load_api_keys(api_keys: list[str] | str | None = None) -> list[str]:
    """
    Loads and deduplicates Gemini API keys from arguments or environment variables.
    1. Checks explicit argument (list or comma-separated string).
    2. Falls back to GEMINI_API_KEYS or GEMINI_API_KEY in environment.
    3. Strips whitespace and removes empty/duplicate entries while preserving order.
    """
    raw_keys: list[str] = []

    if isinstance(api_keys, list):
        raw_keys.extend(str(k) for k in api_keys if k)
    elif isinstance(api_keys, str) and api_keys.strip():
        raw_keys.extend(api_keys.split(","))

    if not raw_keys:
        env_multi = os.getenv("GEMINI_API_KEYS", "")
        env_single = os.getenv("GEMINI_API_KEY", "")
        if env_multi.strip():
            raw_keys.extend(env_multi.split(","))
        if env_single.strip():
            raw_keys.extend(env_single.split(","))

    unique_keys: list[str] = []
    seen: set[str] = set()
    for key in raw_keys:
        cleaned = key.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_keys.append(cleaned)

    if not unique_keys:
        raise ValueError("No valid Gemini API keys provided via arguments or environment variables.")

    return unique_keys


def resolve_models(model: str | list[str] | None = None, models: list[str] | str | None = None) -> list[str]:
    """
    Resolves an ordered, deduplicated list of Gemini models to rotate through.
    1. Prefers explicit `models` list/CSV, then `model` argument if passed.
    2. Falls back to GEMINI_MODELS env var or DEFAULT_MODELS.
    3. Ensures at least one valid non-empty model name is returned.
    """
    raw_models: list[str] = []
    source = models if models is not None else model

    if isinstance(source, list):
        raw_models.extend(str(m) for m in source if m)
    elif isinstance(source, str) and source.strip():
        raw_models.extend(source.split(","))

    if not raw_models:
        env_models = os.getenv("GEMINI_MODELS", "")
        if env_models.strip():
            raw_models.extend(env_models.split(","))
        else:
            raw_models.extend(DEFAULT_MODELS)

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
    """
    Masks an API key for safe terminal logging without exposing secrets.
    1. Returns '***' for short keys.
    2. Shows only the first 4 and last 4 characters otherwise.
    """
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def split_text_into_chunks(text: str, max_chars: int = 100000) -> list[str]:
    """
    Splits large text into line-safe chunks without exceeding max_chars.
    1. Returns text directly when within max_chars.
    2. Accumulates lines until adding the next line exceeds max_chars.
    3. Hard-splits oversized single lines so no chunk exceeds max_chars.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0.")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current_chunk = ""

    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue

        if len(current_chunk) + len(line) > max_chars:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def generate_with_rotation(
    prompt: str,
    models: list[str],
    api_keys: list[str],
    max_rounds: int = 3,
    retry_delay: int = 15,
) -> str:
    """
    Generates content by rotating across models and API keys over multiple rounds.
    1. Iterates through up to max_rounds full passes over (model, api_key) pairs.
    2. Returns immediately on the first non-empty response text.
    3. Waits retry_delay seconds between failed rounds.
    """
    last_error: Exception | None = None

    for round_num in range(1, max_rounds + 1):
        print(f"[*] Gemini round {round_num}/{max_rounds} ({len(models)} model(s), {len(api_keys)} key(s))...")

        for model_name in models:
            for idx, key in enumerate(api_keys, start=1):
                masked = mask_key(key)
                try:
                    client = genai.Client(api_key=key)
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    result_text = getattr(response, "text", None)
                    if not result_text or not result_text.strip():
                        raise RuntimeError("Empty response received from Gemini.")
                    return result_text
                except Exception as exc:
                    last_error = exc
                    print(f"[!] Model '{model_name}' | Key #{idx} ({masked}) failed in round {round_num}: {exc}")

        if round_num < max_rounds:
            print(f"[!] All model/key combinations failed in round {round_num}. Waiting {retry_delay}s...")
            time.sleep(retry_delay)

    raise RuntimeError(
        f"All {len(models)} model(s) and {len(api_keys)} key(s) failed after {max_rounds} round(s). Last error: {last_error}"
    )


def _process_chunks(
    chunks: list[str],
    prompt_template: str,
    models: list[str],
    api_keys: list[str],
    max_rounds: int = 3,
    retry_delay: int = 15,
    delay_between_chunks: int = 2,
) -> list[str]:
    """
    Processes text chunks sequentially using multi-model and multi-key fallback.
    1. Adds part numbering when a file splits into multiple chunks.
    2. Calls generate_with_rotation for each chunk.
    3. Sleeps briefly between chunks to reduce burst rate limits.
    """
    responses: list[str] = []
    total_chunks = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        if total_chunks > 1:
            print(f"    -> Processing part {index}/{total_chunks}...")
            chunk_prompt = f"{prompt_template}\n\n(Part {index} of {total_chunks})\n---\n\n{chunk}"
        else:
            chunk_prompt = f"{prompt_template}\n\n---\n\n{chunk}"

        output_text = generate_with_rotation(
            prompt=chunk_prompt,
            models=models,
            api_keys=api_keys,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )
        responses.append(output_text)

        if index < total_chunks and delay_between_chunks > 0:
            time.sleep(delay_between_chunks)

    return responses


def _read_prompt_and_files(input_dir: str, prompt_file_path: str) -> tuple[str, list[Path]]:
    """
    Loads and validates prompt template and sorted .txt input files.
    1. Verifies prompt_file_path exists as a file.
    2. Verifies input_dir exists as a directory and gathers *.txt files.
    """
    prompt_path = Path(prompt_file_path)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    input_path = Path(input_dir)
    if not input_path.is_dir():
        raise NotADirectoryError(f"Input directory not found: {input_path}")

    prompt_template = prompt_path.read_text(encoding="utf-8")
    txt_files = sorted(input_path.glob("*.txt"))
    return prompt_template, txt_files


def _write_output(output_file: Path, content: str) -> None:
    """
    Writes generated Markdown output safely to disk.
    1. Creates parent directories if missing.
    2. Writes UTF-8 content and logs destination path.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")
    print(f"[+] Saved result to: {output_file}")


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
    max_chunk_chars: int = 100000,
    max_rounds: int = 3,
    retry_delay: int = 15,
) -> None:
    """
    Analyzes .txt files using Gemini with model rotation, key rotation, and chunking.
    1. Resolves API keys and candidate models.
    2. Reads prompt and input text files.
    3. Processes merged or per-file outputs into Markdown.
    """
    resolved_keys = load_api_keys(api_keys if api_keys is not None else api_key)
    resolved_models = resolve_models(model=model, models=models)
    prompt_template, txt_files = _read_prompt_and_files(input_dir, prompt_file_path)

    if not txt_files:
        print(f"[!] No .txt files found in '{input_dir}'.")
        return

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if merge_files:
        output_file = out_dir / merged_output_name
        if output_file.exists() and output_file.stat().st_size > 0:
            print(f"[!] Output '{output_file.name}' already exists. Skipping.")
            return

        print(f"[*] Merging and analyzing {len(txt_files)} file(s) with models {resolved_models}...")
        combined_parts = [
            f"### File: {file_path.name}\n{file_path.read_text(encoding='utf-8')}"
            for file_path in txt_files
        ]
        chunks = split_text_into_chunks("\n\n---\n\n".join(combined_parts), max_chars=max_chunk_chars)
        responses = _process_chunks(
            chunks=chunks,
            prompt_template=prompt_template,
            models=resolved_models,
            api_keys=resolved_keys,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )
        _write_output(output_file, "\n\n---\n\n".join(responses))
        return

    for file_path in txt_files:
        target_name = output_file_name if (output_file_name and len(txt_files) == 1) else f"{file_path.stem}.md"
        output_file = out_dir / target_name

        if output_file.exists() and output_file.stat().st_size > 0:
            print(f"[!] Output '{output_file.name}' already exists. Skipping.")
            continue

        print(f"[*] Analyzing '{file_path.name}' with models {resolved_models}...")
        content = file_path.read_text(encoding="utf-8")
        chunks = split_text_into_chunks(content, max_chars=max_chunk_chars)
        responses = _process_chunks(
            chunks=chunks,
            prompt_template=prompt_template,
            models=resolved_models,
            api_keys=resolved_keys,
            max_rounds=max_rounds,
            retry_delay=retry_delay,
        )
        if responses:
            _write_output(output_file, "\n\n---\n\n".join(responses))


process_text_files_gemini = process_text_files