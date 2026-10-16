from pathlib import Path
import shutil
import json
import time


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OBARDO_TEMP_ROOT = PROJECT_ROOT / "storage" / "obardo_temp"
OBARDO_PREVIEWS_DIR = OBARDO_TEMP_ROOT / "previews"
OBARDO_CLIPS_DIR = OBARDO_TEMP_ROOT / "clips"
OBARDO_FRAMES_DIR = OBARDO_TEMP_ROOT / "frames"
OBARDO_METADATA_DIR = OBARDO_TEMP_ROOT / "metadata"

OBARDO_LIBRARY_DIR = PROJECT_ROOT / "storage" / "obardo_library"


# Preview used only for analysis.
PREVIEW_MAX_HEIGHT = 360

# Final selected clip.
FINAL_CLIP_PREFERRED_HEIGHT = 720
FINAL_CLIP_MAX_HEIGHT = 1080

# Keep individual final clips short.
FINAL_CLIP_MIN_SECONDS = 2.0
FINAL_CLIP_MAX_SECONDS = 6.0

# First analysis pass.
PREVIEW_SAMPLE_SECONDS = 1.0

# Prevent the temporary workspace from growing indefinitely.
TEMP_MAX_GB = 3.0

# Default behavior after a finished render.
CLEANUP_PREVIEWS_AFTER_SELECTION = True
CLEANUP_FRAMES_AFTER_RENDER = True
CLEANUP_CLIPS_AFTER_RENDER = True


def ensure_obardo_media_dirs() -> None:
    """
    Ensure all O Bardo temporary and library directories exist.
    """
    for directory in (
        OBARDO_PREVIEWS_DIR,
        OBARDO_CLIPS_DIR,
        OBARDO_FRAMES_DIR,
        OBARDO_METADATA_DIR,
        OBARDO_LIBRARY_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def directory_size_bytes(directory: Path) -> int:
    """
    Return the total size of files below one directory.
    """
    if not directory.exists():
        return 0

    total = 0

    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue

    return total


def temp_size_gb() -> float:
    """
    Return current O Bardo temporary storage usage in GiB.
    """
    size_bytes = directory_size_bytes(OBARDO_TEMP_ROOT)
    return size_bytes / (1024 ** 3)


def cleanup_directory(directory: Path) -> int:
    """
    Delete all files and subdirectories below directory.

    Returns the number of filesystem entries removed.
    """
    if not directory.exists():
        return 0

    removed = 0

    for item in list(directory.iterdir()):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

            removed += 1

        except OSError:
            continue

    return removed


def cleanup_preview_files() -> int:
    return cleanup_directory(OBARDO_PREVIEWS_DIR)


def cleanup_frame_files() -> int:
    return cleanup_directory(OBARDO_FRAMES_DIR)


def cleanup_clip_files() -> int:
    return cleanup_directory(OBARDO_CLIPS_DIR)


def cleanup_all_temp() -> dict:
    """
    Clear all ephemeral O Bardo media.

    Metadata is intentionally preserved because it can help debugging,
    licensing records and future semantic indexing.
    """
    return {
        "previews_removed": cleanup_preview_files(),
        "frames_removed": cleanup_frame_files(),
        "clips_removed": cleanup_clip_files(),
    }


def write_metadata(name: str, payload: dict) -> Path:
    """
    Save JSON metadata for one analyzed source or selected clip.
    """
    ensure_obardo_media_dirs()

    safe_name = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in str(name)
    ).strip("._")

    if not safe_name:
        safe_name = f"obardo-{int(time.time())}"

    if not safe_name.lower().endswith(".json"):
        safe_name += ".json"

    path = OBARDO_METADATA_DIR / safe_name

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


def get_media_status() -> dict:
    """
    Small diagnostic snapshot used by CLI/tests.
    """
    ensure_obardo_media_dirs()

    return {
        "temp_root": str(OBARDO_TEMP_ROOT),
        "preview_dir": str(OBARDO_PREVIEWS_DIR),
        "clips_dir": str(OBARDO_CLIPS_DIR),
        "frames_dir": str(OBARDO_FRAMES_DIR),
        "metadata_dir": str(OBARDO_METADATA_DIR),
        "library_dir": str(OBARDO_LIBRARY_DIR),
        "preview_max_height": PREVIEW_MAX_HEIGHT,
        "final_preferred_height": FINAL_CLIP_PREFERRED_HEIGHT,
        "final_max_height": FINAL_CLIP_MAX_HEIGHT,
        "clip_min_seconds": FINAL_CLIP_MIN_SECONDS,
        "clip_max_seconds": FINAL_CLIP_MAX_SECONDS,
        "temp_max_gb": TEMP_MAX_GB,
        "current_temp_gb": round(temp_size_gb(), 4),
    }


ensure_obardo_media_dirs()

def _safe_stem(value: str, fallback: str = "video") -> str:
    value = "".join(
        c if c.isalnum() or c in "-_."
        else "_"
        for c in str(value or "")
    ).strip("._")

    return value or fallback


def download_preview(
    url: str,
    *,
    max_height: int = PREVIEW_MAX_HEIGHT,
    max_size_mb: float = 100.0,
    max_duration_seconds: float = 2700.0,
) -> dict:
    """
    Download one lightweight preview for semantic analysis.

    Policy:
        - video only whenever possible
        - max 360p by default
        - max estimated file size 100 MB
        - reject sources longer than 45 minutes
        - no unrestricted "best" fallback
    """
    import time
    from pathlib import Path
    import yt_dlp

    ensure_obardo_media_dirs()

    url = str(url or "").strip()

    if not url:
        raise ValueError(
            "A source URL is required."
        )

    max_height = max(
        144,
        min(int(max_height), 360),
    )

    max_size_bytes = int(
        float(max_size_mb)
        * 1024
        * 1024
    )

    # -----------------------------------------------------
    # Fast local cache path.
    #
    # Avoid invoking yt-dlp when a valid preview for the
    # same YouTube video already exists locally.
    # -----------------------------------------------------
    import re

    cached_video_id = ""

    youtube_id_patterns = [
        r"[?&]v=([A-Za-z0-9_-]{6,})",
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{6,})",
    ]

    for pattern in youtube_id_patterns:
        match = re.search(
            pattern,
            url,
        )

        if match:
            cached_video_id = (
                match.group(1)
            )
            break

    if cached_video_id:
        cached_candidates = [
            item
            for item in OBARDO_PREVIEWS_DIR.glob(
                f"{_safe_stem(cached_video_id)}"
                "-preview-*"
            )
            if (
                item.exists()
                and item.is_file()
                and not item.name.endswith(".part")
                and item.stat().st_size > 0
                and item.stat().st_size <= max_size_bytes
            )
        ]

        if cached_candidates:
            cached_path = max(
                cached_candidates,
                key=lambda item: (
                    item.stat().st_mtime
                ),
            )

            cached_size = (
                cached_path.stat().st_size
            )

            print(
                "O Bardo YouTube: preview cache hit | "
                f"video_id={cached_video_id} | "
                f"file={cached_path}"
            )

            return {
                "type": "preview",
                "source_url": url,
                "webpage_url": url,
                "extractor": "youtube",
                "id": cached_video_id,
                "title": None,
                "uploader": None,
                "duration": 0.0,
                "width": None,
                "height": None,
                "fps": None,
                "format_id": None,
                "ext": cached_path.suffix.lstrip("."),
                "preview_max_height": max_height,
                "preview_max_size_mb": max_size_mb,
                "video_only": None,
                "filepath": str(cached_path),
                "file_size_bytes": cached_size,
                "file_size_mb": round(
                    cached_size / (1024 ** 2),
                    2,
                ),
                "download_seconds": 0.0,
                "downloaded_at_unix": int(
                    cached_path.stat().st_mtime
                ),
                "cache_hit": True,
            }

    before_gb = temp_size_gb()

    if before_gb >= TEMP_MAX_GB:
        raise RuntimeError(
            f"O Bardo temporary storage limit reached: "
            f"{before_gb:.2f} GiB / "
            f"{TEMP_MAX_GB:.2f} GiB"
        )

    common_opts = {
        "noplaylist": True,
        "restrictfilenames": True,
        "js_runtimes": {
            "deno": {},
        },
        "remote_components": {
            "ejs:github": [],
        },
    }

    # -----------------------------------------------------
    # Metadata-only inspection before any download
    # -----------------------------------------------------

    inspect_opts = {
        **common_opts,
        "quiet": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(
        inspect_opts
    ) as ydl:
        info = ydl.extract_info(
            url,
            download=False,
        )

    if info is None:
        raise RuntimeError(
            "yt-dlp returned no metadata."
        )

    duration = float(
        info.get("duration") or 0.0
    )

    if (
        duration > 0
        and duration > float(
            max_duration_seconds
        )
    ):
        raise RuntimeError(
            "Preview source rejected: "
            f"duration {duration:.0f}s exceeds "
            f"{float(max_duration_seconds):.0f}s."
        )

    formats = []

    for fmt in (
        info.get("formats") or []
    ):
        vcodec = str(
            fmt.get("vcodec") or "none"
        )

        if vcodec == "none":
            continue

        try:
            height = int(
                fmt.get("height") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            height = 0

        if not height:
            continue

        if height > max_height:
            continue

        filesize = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
        )

        try:
            size_bytes = (
                int(filesize)
                if filesize
                else 0
            )
        except (
            TypeError,
            ValueError,
        ):
            size_bytes = 0

        if (
            not size_bytes
            and duration > 0
        ):
            try:
                tbr = float(
                    fmt.get("tbr") or 0.0
                )
            except (
                TypeError,
                ValueError,
            ):
                tbr = 0.0

            if tbr > 0:
                size_bytes = int(
                    (
                        tbr
                        * 1000
                        / 8
                    )
                    * duration
                )

        # Unknown size is intentionally rejected.
        if not size_bytes:
            continue

        if size_bytes > max_size_bytes:
            continue

        acodec = str(
            fmt.get("acodec") or "none"
        )

        formats.append(
            {
                "format_id": str(
                    fmt.get("format_id")
                    or ""
                ),
                "height": height,
                "width": fmt.get("width"),
                "fps": fmt.get("fps"),
                "vcodec": vcodec,
                "acodec": acodec,
                "video_only": (
                    acodec == "none"
                ),
                "ext": fmt.get("ext"),
                "size_bytes": size_bytes,
                "size_mb": round(
                    size_bytes
                    / (1024 ** 2),
                    2,
                ),
            }
        )

    if not formats:
        raise RuntimeError(
            "No preview format survived limits: "
            f"height<={max_height}, "
            f"size<={max_size_mb} MB."
        )

    # Prefer:
    # 1. video-only
    # 2. highest resolution up to 360p
    # 3. smaller file when otherwise equivalent
    formats.sort(
        key=lambda item: (
            int(item["video_only"]),
            item["height"],
            int(item["fps"] or 0),
            -item["size_bytes"],
        ),
        reverse=True,
    )

    selected = formats[0]

    format_id = selected[
        "format_id"
    ]

    if not format_id:
        raise RuntimeError(
            "Selected preview format has no format_id."
        )

    # -----------------------------------------------------
    # Download only the pre-approved lightweight format
    # -----------------------------------------------------

    output_template = str(
        OBARDO_PREVIEWS_DIR
        / (
            "%(id)s-preview-"
            "%(format_id)s.%(ext)s"
        )
    )

    download_opts = {
        **common_opts,
        "quiet": False,
        "format": format_id,
        "outtmpl": output_template,
        "overwrites": False,
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 3,
    }

    started = time.time()

    with yt_dlp.YoutubeDL(
        download_opts
    ) as ydl:
        downloaded_info = (
            ydl.extract_info(
                url,
                download=True,
            )
        )

        requested = (
            downloaded_info.get(
                "requested_downloads"
            )
            or []
        )

        candidate_paths = []

        for entry in requested:
            filepath = entry.get(
                "filepath"
            )

            if filepath:
                candidate_paths.append(
                    Path(filepath)
                )

        prepared = ydl.prepare_filename(
            downloaded_info
        )

        if prepared:
            candidate_paths.append(
                Path(prepared)
            )

    video_id = str(
        downloaded_info.get("id")
        or info.get("id")
        or ""
    )

    existing_paths = [
        candidate
        for candidate in candidate_paths
        if (
            candidate.exists()
            and candidate.is_file()
        )
    ]

    if (
        not existing_paths
        and video_id
    ):
        existing_paths = list(
            OBARDO_PREVIEWS_DIR.glob(
                f"{_safe_stem(video_id)}"
                "-preview-*"
            )
        )

    existing_paths = [
        item
        for item in existing_paths
        if (
            item.exists()
            and item.is_file()
            and not item.name.endswith(
                ".part"
            )
        )
    ]

    if not existing_paths:
        raise RuntimeError(
            "Preview download completed "
            "but output file was not found."
        )

    preview_path = max(
        existing_paths,
        key=lambda item: (
            item.stat().st_mtime
        ),
    )

    actual_size = (
        preview_path.stat().st_size
    )

    if actual_size > max_size_bytes:
        try:
            preview_path.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Downloaded preview exceeded "
            f"{max_size_mb} MB."
        )

    elapsed = (
        time.time()
        - started
    )

    metadata = {
        "type": "preview",
        "source_url": url,
        "webpage_url": (
            downloaded_info.get(
                "webpage_url"
            )
            or url
        ),
        "extractor": (
            downloaded_info.get(
                "extractor"
            )
        ),
        "id": video_id,
        "title": downloaded_info.get(
            "title"
        ),
        "uploader": downloaded_info.get(
            "uploader"
        ),
        "duration": duration,
        "width": selected.get(
            "width"
        ),
        "height": selected.get(
            "height"
        ),
        "fps": selected.get(
            "fps"
        ),
        "format_id": format_id,
        "ext": preview_path.suffix.lstrip(
            "."
        ),
        "preview_max_height": (
            max_height
        ),
        "preview_max_size_mb": (
            max_size_mb
        ),
        "video_only": selected.get(
            "video_only"
        ),
        "filepath": str(
            preview_path
        ),
        "file_size_bytes": (
            actual_size
        ),
        "file_size_mb": round(
            actual_size
            / (1024 ** 2),
            2,
        ),
        "download_seconds": round(
            elapsed,
            2,
        ),
        "downloaded_at_unix": int(
            time.time()
        ),
        "temporary": True,
    }

    metadata_name = (
        "preview-"
        + _safe_stem(
            video_id or "video"
        )
    )

    metadata_path = write_metadata(
        metadata_name,
        metadata,
    )

    metadata[
        "metadata_path"
    ] = str(metadata_path)

    metadata[
        "temp_size_gb"
    ] = round(
        temp_size_gb(),
        4,
    )

    return metadata

def delete_preview(preview_path: str | Path) -> bool:
    """
    Delete one preview file after analysis/selection.
    """
    path = Path(preview_path)

    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except OSError:
        return False

    return False

def extract_preview_frames(
    preview_path: str | Path,
    *,
    video_id: str | None = None,
    sample_seconds: float = PREVIEW_SAMPLE_SECONDS,
    jpeg_quality: int = 5,
) -> dict:
    """
    Extract lightweight JPEG frames from a low-resolution preview.

    Default:
        one frame every 1 second

    These frames are temporary and intended only for semantic analysis.
    """
    import subprocess

    ensure_obardo_media_dirs()

    preview_path = Path(preview_path)

    if not preview_path.exists():
        raise FileNotFoundError(
            f"Preview not found: {preview_path}"
        )

    sample_seconds = max(float(sample_seconds), 0.2)

    if video_id:
        folder_name = _safe_stem(video_id)
    else:
        folder_name = _safe_stem(preview_path.stem)

    frame_dir = OBARDO_FRAMES_DIR / folder_name
    frame_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale frames from an earlier analysis of the same source.
    for old_frame in frame_dir.glob("*.jpg"):
        try:
            old_frame.unlink()
        except OSError:
            pass

    fps_value = 1.0 / sample_seconds

    output_pattern = frame_dir / "frame-%06d.jpg"

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(preview_path),
        "-vf",
        f"fps={fps_value}",
        "-q:v",
        str(int(jpeg_quality)),
        str(output_pattern),
    ]

    started_at = time.time()

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg frame extraction failed:\n"
            + (process.stderr or process.stdout or "")
        )

    frames = sorted(frame_dir.glob("frame-*.jpg"))

    elapsed = time.time() - started_at

    total_bytes = sum(
        frame.stat().st_size
        for frame in frames
        if frame.exists()
    )

    result = {
        "preview_path": str(preview_path),
        "video_id": video_id,
        "frame_dir": str(frame_dir),
        "sample_seconds": sample_seconds,
        "fps_analysis": fps_value,
        "frame_count": len(frames),
        "frames_size_mb": round(
            total_bytes / (1024 ** 2),
            2,
        ),
        "elapsed_seconds": round(elapsed, 2),
        "first_frame": (
            str(frames[0])
            if frames
            else None
        ),
        "last_frame": (
            str(frames[-1])
            if frames
            else None
        ),
    }

    return result

# ---------------------------------------------------------------------------
# O BARDO - EXTERNAL SOURCE AUDIO POLICY
# ---------------------------------------------------------------------------

# External material is always silent by default.
SOURCE_AUDIO_DEFAULT_ENABLED = False

# Very short narration gaps must never trigger external audio.
SOURCE_AUDIO_MIN_PAUSE_SECONDS = 1.5

# Safer threshold for automatically considering ambience.
SOURCE_AUDIO_PREFERRED_PAUSE_SECONDS = 2.0

# Original dialogue from another video must never be enabled automatically.
SOURCE_AUDIO_ALLOW_DIALOGUE_AUTOMATICALLY = False

# Audio normalization target when original ambience is deliberately preserved.
SOURCE_AUDIO_TARGET_LUFS = -20.0
SOURCE_AUDIO_TRUE_PEAK_DB = -2.0
SOURCE_AUDIO_LRA = 7.0

# Standard internal delivery settings.
OBARDO_OUTPUT_FPS = 30
OBARDO_OUTPUT_AUDIO_RATE = 48000


def decide_source_audio_policy(
    *,
    pause_seconds: float = 0.0,
    allow_source_audio: bool = False,
    source_has_dialogue: bool = False,
    audio_is_narratively_relevant: bool = False,
) -> dict:
    """
    Decide whether audio from an external source may be used.

    Hard rules:
    - narration wins;
    - source audio is disabled by default;
    - short narration pauses never enable source audio;
    - dialogue from another narrator/video is never enabled automatically;
    - ambience/effects may be used only when explicitly relevant.
    """
    pause_seconds = max(float(pause_seconds or 0.0), 0.0)

    result = {
        "available": None,
        "use": False,
        "pause_seconds": pause_seconds,
        "allow_source_audio": bool(allow_source_audio),
        "source_has_dialogue": bool(source_has_dialogue),
        "audio_is_narratively_relevant": bool(
            audio_is_narratively_relevant
        ),
        "minimum_pause_seconds": SOURCE_AUDIO_MIN_PAUSE_SECONDS,
        "preferred_pause_seconds": SOURCE_AUDIO_PREFERRED_PAUSE_SECONDS,
        "target_lufs": SOURCE_AUDIO_TARGET_LUFS,
        "true_peak_db": SOURCE_AUDIO_TRUE_PEAK_DB,
        "reason": "",
    }

    if not allow_source_audio:
        result["reason"] = "source_audio_disabled_by_default"
        return result

    if pause_seconds < SOURCE_AUDIO_MIN_PAUSE_SECONDS:
        result["reason"] = "narration_active_or_pause_too_short"
        return result

    if source_has_dialogue:
        result["reason"] = "external_dialogue_not_allowed_automatically"
        return result

    if not audio_is_narratively_relevant:
        result["reason"] = "source_audio_not_narratively_relevant"
        return result

    result["use"] = True

    if pause_seconds >= SOURCE_AUDIO_PREFERRED_PAUSE_SECONDS:
        result["reason"] = "eligible_narration_pause_with_relevant_ambience"
    else:
        result["reason"] = "short_but_eligible_pause_with_relevant_ambience"

    return result


def _obardo_scale_pad_filter(
    width: int,
    height: int,
    fps: int = OBARDO_OUTPUT_FPS,
) -> str:
    """
    Normalize heterogeneous external material without stretching it.
    """
    width = max(int(width), 2)
    height = max(int(height), 2)
    fps = max(int(fps), 1)

    return (
        f"scale={width}:{height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:"
        "(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},"
        "format=yuv420p"
    )


def normalize_external_clip(
    input_path: str | Path,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = OBARDO_OUTPUT_FPS,
    keep_source_audio: bool = SOURCE_AUDIO_DEFAULT_ENABLED,
) -> dict:
    """
    Convert one selected external clip to O Bardo's stable internal format.

    Default:
        H.264
        yuv420p
        30 fps
        requested canvas size
        NO source audio

    When keep_source_audio=True, audio is normalized and encoded as:
        AAC
        48 kHz
        stereo
        EBU-style loudness normalization
    """
    import subprocess

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input clip not found: {input_path}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    video_filter = _obardo_scale_pad_filter(
        width=width,
        height=height,
        fps=fps,
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
    ]

    if keep_source_audio:
        command.extend(
            [
                "-map",
                "0:a:0?",
                "-af",
                (
                    "loudnorm="
                    f"I={SOURCE_AUDIO_TARGET_LUFS}:"
                    f"TP={SOURCE_AUDIO_TRUE_PEAK_DB}:"
                    f"LRA={SOURCE_AUDIO_LRA}"
                ),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                str(OBARDO_OUTPUT_AUDIO_RATE),
                "-ac",
                "2",
            ]
        )
    else:
        # Narration-first rule:
        # external clip is silent unless explicitly authorized later.
        command.append("-an")

    command.append(str(output_path))

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "O Bardo clip normalization failed:\n"
            + (process.stderr or process.stdout or "")
        )

    return {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "width": int(width),
        "height": int(height),
        "fps": int(fps),
        "source_audio_kept": bool(keep_source_audio),
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "audio_codec": "aac" if keep_source_audio else None,
        "audio_rate": (
            OBARDO_OUTPUT_AUDIO_RATE
            if keep_source_audio
            else None
        ),
        "target_lufs": (
            SOURCE_AUDIO_TARGET_LUFS
            if keep_source_audio
            else None
        ),
    }


def build_external_clip_metadata(
    *,
    source_url: str,
    clip_start: float,
    clip_end: float,
    source_audio_policy: dict | None = None,
    license_name: str | None = None,
    license_url: str | None = None,
) -> dict:
    """
    Metadata record used later by the final O Bardo timeline renderer.
    """
    clip_start = max(float(clip_start), 0.0)
    clip_end = max(float(clip_end), clip_start)

    if source_audio_policy is None:
        source_audio_policy = decide_source_audio_policy()

    return {
        "source_url": source_url,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "duration": round(clip_end - clip_start, 3),
        "source_audio": source_audio_policy,
        "license": license_name,
        "license_url": license_url,
        "temporary": True,
    }

def _obardo_srt_timestamp_to_seconds(value: str) -> float:
    """
    Convert SRT timestamp HH:MM:SS,mmm to seconds.
    Also tolerates HH:MM:SS.mmm.
    """
    value = str(value or "").strip().replace(",", ".")

    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")

    hours = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])

    return (
        hours * 3600.0
        + minutes * 60.0
        + seconds
    )


def _obardo_parse_srt_timing(timing: str) -> tuple[float, float]:
    """
    Parse:
        00:00:01,000 --> 00:00:03,500
    """
    timing = str(timing or "")

    if "-->" not in timing:
        raise ValueError(
            f"Invalid SRT timing line: {timing!r}"
        )

    start_text, end_text = timing.split("-->", 1)

    return (
        _obardo_srt_timestamp_to_seconds(start_text),
        _obardo_srt_timestamp_to_seconds(end_text),
    )


def get_narration_intervals_from_srt(
    subtitle_path: str | Path,
) -> list[dict]:
    """
    Read narration-active intervals from MoneyPrinterTurbo's SRT.

    These intervals represent periods where external source audio must
    normally remain muted.
    """
    from app.services import subtitle

    subtitle_path = Path(subtitle_path)

    if not subtitle_path.exists():
        raise FileNotFoundError(
            f"Subtitle file not found: {subtitle_path}"
        )

    subtitles = subtitle.file_to_subtitles(
        str(subtitle_path)
    )

    intervals = []

    for index, timing, text in subtitles:
        start, end = _obardo_parse_srt_timing(timing)

        if end <= start:
            continue

        intervals.append(
            {
                "index": int(index),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "text": str(text or "").strip(),
            }
        )

    return intervals


def get_narration_pause_windows(
    subtitle_path: str | Path,
    *,
    audio_duration: float | None = None,
    minimum_pause_seconds: float = SOURCE_AUDIO_MIN_PAUSE_SECONDS,
) -> list[dict]:
    """
    Find real silence windows between narration cues.

    Short linguistic pauses are deliberately rejected.
    """
    intervals = get_narration_intervals_from_srt(
        subtitle_path
    )

    minimum_pause_seconds = max(
        float(minimum_pause_seconds),
        0.0,
    )

    if not intervals:
        return []

    pauses = []

    # Optional silence before first spoken cue.
    first_start = float(intervals[0]["start"])

    if first_start >= minimum_pause_seconds:
        pauses.append(
            {
                "start": 0.0,
                "end": round(first_start, 3),
                "duration": round(first_start, 3),
                "position": "before_narration",
                "eligible_for_source_audio": True,
            }
        )

    # Pauses between spoken subtitle cues.
    for previous, current in zip(
        intervals,
        intervals[1:],
    ):
        pause_start = float(previous["end"])
        pause_end = float(current["start"])

        duration = pause_end - pause_start

        if duration < minimum_pause_seconds:
            continue

        pauses.append(
            {
                "start": round(pause_start, 3),
                "end": round(pause_end, 3),
                "duration": round(duration, 3),
                "position": "between_narration",
                "after_cue": previous["index"],
                "before_cue": current["index"],
                "eligible_for_source_audio": True,
            }
        )

    # Optional silence after final spoken cue.
    if audio_duration is not None:
        audio_duration = max(
            float(audio_duration),
            0.0,
        )

        last_end = float(intervals[-1]["end"])

        if audio_duration > last_end:
            duration = audio_duration - last_end

            if duration >= minimum_pause_seconds:
                pauses.append(
                    {
                        "start": round(last_end, 3),
                        "end": round(audio_duration, 3),
                        "duration": round(duration, 3),
                        "position": "after_narration",
                        "eligible_for_source_audio": True,
                    }
                )

    return pauses


def build_source_audio_windows(
    subtitle_path: str | Path,
    *,
    audio_duration: float | None = None,
    allow_source_audio: bool = False,
    source_has_dialogue: bool = False,
    audio_is_narratively_relevant: bool = False,
) -> list[dict]:
    """
    Apply O Bardo's narration-first audio policy to every real pause.

    Returning an empty list means the external clip should remain silent
    throughout the narration timeline.
    """
    pauses = get_narration_pause_windows(
        subtitle_path,
        audio_duration=audio_duration,
    )

    approved = []

    for pause in pauses:
        policy = decide_source_audio_policy(
            pause_seconds=pause["duration"],
            allow_source_audio=allow_source_audio,
            source_has_dialogue=source_has_dialogue,
            audio_is_narratively_relevant=(
                audio_is_narratively_relevant
            ),
        )

        item = {
            **pause,
            "audio_policy": policy,
        }

        if policy["use"]:
            approved.append(item)

    return approved

def detect_preview_scenes(
    preview_path: str | Path,
    *,
    video_id: str | None = None,
    threshold: float = 0.32,
    max_scenes: int = 300,
) -> dict:
    """
    Detect visually significant scene changes using FFmpeg.

    A representative JPEG is exported for each detected scene boundary.
    This is much lighter than sampling every second across the whole video.
    """
    import subprocess
    import re

    ensure_obardo_media_dirs()

    preview_path = Path(preview_path)

    if not preview_path.exists():
        raise FileNotFoundError(
            f"Preview not found: {preview_path}"
        )

    threshold = min(max(float(threshold), 0.05), 0.95)
    max_scenes = max(int(max_scenes), 1)

    if video_id:
        folder_name = _safe_stem(video_id) + "-scenes"
    else:
        folder_name = _safe_stem(preview_path.stem) + "-scenes"

    scene_dir = OBARDO_FRAMES_DIR / folder_name
    scene_dir.mkdir(parents=True, exist_ok=True)

    for old_file in scene_dir.glob("*.jpg"):
        try:
            old_file.unlink()
        except OSError:
            pass

    output_pattern = scene_dir / "scene-%06d.jpg"

    vf = (
        f"select='gt(scene,{threshold})',"
        "showinfo"
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-y",
        "-i",
        str(preview_path),
        "-vf",
        vf,
        "-vsync",
        "vfr",
        "-frames:v",
        str(max_scenes),
        "-q:v",
        "4",
        str(output_pattern),
    ]

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg scene detection failed:\n"
            + (process.stderr or process.stdout or "")
        )

    frames = sorted(scene_dir.glob("scene-*.jpg"))

    # showinfo writes timestamps to stderr.
    stderr = process.stderr or ""

    timestamps = []

    for match in re.finditer(
        r"pts_time:([0-9]+(?:\.[0-9]+)?)",
        stderr,
    ):
        try:
            timestamps.append(float(match.group(1)))
        except ValueError:
            continue

    # Keep timestamps aligned with actual exported frames.
    timestamps = timestamps[: len(frames)]

    scenes = []

    for index, frame in enumerate(frames):
        timestamp = (
            round(timestamps[index], 3)
            if index < len(timestamps)
            else None
        )

        scenes.append(
            {
                "index": index + 1,
                "timestamp": timestamp,
                "frame_path": str(frame),
            }
        )

    total_bytes = sum(
        frame.stat().st_size
        for frame in frames
        if frame.exists()
    )

    return {
        "preview_path": str(preview_path),
        "video_id": video_id,
        "threshold": threshold,
        "scene_dir": str(scene_dir),
        "scene_count": len(scenes),
        "frames_size_mb": round(
            total_bytes / (1024 ** 2),
            2,
        ),
        "scenes": scenes,
    }

def build_scene_windows(
    scenes: list[dict],
    *,
    video_duration: float | None = None,
    default_tail_seconds: float = 5.0,
) -> list[dict]:
    """
    Convert detected scene-boundary timestamps into usable scene windows.

    Example:
        2.0, 4.5, 7.0
    becomes:
        2.0 -> 4.5
        4.5 -> 7.0
        7.0 -> video_duration
    """
    if not scenes:
        return []

    valid = []

    for scene in scenes:
        timestamp = scene.get("timestamp")

        if timestamp is None:
            continue

        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            continue

        valid.append(
            {
                **scene,
                "timestamp": timestamp,
            }
        )

    valid.sort(
        key=lambda item: item["timestamp"]
    )

    windows = []

    for index, scene in enumerate(valid):
        start = float(scene["timestamp"])

        if index + 1 < len(valid):
            end = float(
                valid[index + 1]["timestamp"]
            )
        elif video_duration is not None:
            end = max(
                float(video_duration),
                start,
            )
        else:
            end = start + max(
                float(default_tail_seconds),
                0.1,
            )

        if end <= start:
            continue

        windows.append(
            {
                "scene_index": scene.get(
                    "index",
                    index + 1,
                ),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(
                    end - start,
                    3,
                ),
                "frame_path": scene.get(
                    "frame_path"
                ),
            }
        )

    return windows


def choose_clip_window_from_scene(
    scene: dict,
    *,
    preferred_duration: float = 4.0,
    min_duration: float = FINAL_CLIP_MIN_SECONDS,
    max_duration: float = FINAL_CLIP_MAX_SECONDS,
) -> dict:
    """
    Choose a practical 2-6 second clip inside one detected scene.

    Longer scenes are trimmed around the middle instead of blindly taking
    the first seconds.
    """
    start = float(scene["start"])
    end = float(scene["end"])

    available = max(end - start, 0.0)

    min_duration = max(
        float(min_duration),
        0.1,
    )

    max_duration = max(
        float(max_duration),
        min_duration,
    )

    preferred_duration = min(
        max(float(preferred_duration), min_duration),
        max_duration,
    )

    if available <= preferred_duration:
        selected_start = start
        selected_end = end
    else:
        center = start + (available / 2.0)

        selected_start = (
            center - preferred_duration / 2.0
        )

        selected_end = (
            selected_start + preferred_duration
        )

        if selected_start < start:
            selected_start = start
            selected_end = (
                start + preferred_duration
            )

        if selected_end > end:
            selected_end = end
            selected_start = (
                end - preferred_duration
            )

    return {
        "scene_index": scene.get("scene_index"),
        "scene_start": round(start, 3),
        "scene_end": round(end, 3),
        "clip_start": round(
            selected_start,
            3,
        ),
        "clip_end": round(
            selected_end,
            3,
        ),
        "clip_duration": round(
            selected_end - selected_start,
            3,
        ),
        "frame_path": scene.get("frame_path"),
    }

_OBARDO_OPENCLIP_CACHE = {}

# ------------------------------------------------------------
# O Bardo Turbo v1 - YouTube HQ failure cache.
#
# Process-local on purpose:
# a temporary network/CDN failure should not poison future runs,
# but the same bad video must not cost another 25 seconds over
# and over during one documentary generation.
# ------------------------------------------------------------
_OBARDO_YOUTUBE_HQ_FAILED_IDS = set()

# ------------------------------------------------------------
# O Bardo 4.17B - process-local partial HQ strategy cache.
#
# This is intentionally separate from the per-video HQ failure
# cache above.
#
# A repeated --download-sections timeout indicates that the
# partial remote-range strategy is unreliable for the current
# process/network/CDN session. Once observed, later beats skip
# the 25-second partial attempt and go directly to the existing
# full-HQ source fallback.
#
# Only timeout-style failures poison this strategy cache.
# ------------------------------------------------------------
_OBARDO_YOUTUBE_PARTIAL_HQ_UNRELIABLE = set()

# ------------------------------------------------------------
# O Bardo Turbo v1 - process-local YouTube caches.
# ------------------------------------------------------------
_OBARDO_YOUTUBE_SEARCH_CACHE = {}
_OBARDO_YOUTUBE_PREFLIGHT_CACHE = {}




def _get_obardo_openclip_bundle(
    model_name: str,
    pretrained: str,
    device: str = "cpu",
):
    """
    Load OpenCLIP once per model/pretrained/device combination
    and reuse it for subsequent frame-ranking calls.
    """
    cache_key = (
        str(model_name),
        str(pretrained),
        str(device),
    )

    cached = _OBARDO_OPENCLIP_CACHE.get(
        cache_key
    )

    if cached is not None:
        return cached

    import open_clip

    model, _, preprocess = (
        open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
            device=device,
        )
    )

    tokenizer = open_clip.get_tokenizer(
        model_name
    )

    model.eval()

    cached = (
        model,
        preprocess,
        tokenizer,
    )

    _OBARDO_OPENCLIP_CACHE[
        cache_key
    ] = cached

    return cached


def _obardo_encode_scene_frames_openclip(
    scene_windows: list[dict],
    *,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
):
    """
    Encode scene representative frames once with OpenCLIP.

    Returns:
        valid_items,
        normalized image feature matrix.

    This helper deliberately performs no text ranking so the image
    embeddings can later be persisted and reused for different queries.
    """
    import torch
    from PIL import Image

    if not scene_windows:
        return [], None

    device = "cpu"

    model, preprocess, _ = (
        _get_obardo_openclip_bundle(
            model_name,
            pretrained,
            device,
        )
    )

    valid_items = []
    image_tensors = []

    for scene in scene_windows:
        frame_path = scene.get(
            "frame_path"
        )

        if not frame_path:
            continue

        frame_path = Path(
            frame_path
        )

        if not frame_path.exists():
            continue

        try:
            image = (
                Image.open(
                    frame_path
                )
                .convert("RGB")
            )

            tensor = preprocess(
                image
            )

        except Exception:
            continue

        valid_items.append(
            scene
        )

        image_tensors.append(
            tensor
        )

    if not image_tensors:
        return [], None

    images = torch.stack(
        image_tensors
    ).to(device)

    with torch.no_grad():
        image_features = model.encode_image(
            images
        )

        image_features /= (
            image_features.norm(
                dim=-1,
                keepdim=True,
            )
        )

    return (
        valid_items,
        image_features,
    )


def _obardo_rank_openclip_image_features_by_text(
    scene_items: list[dict],
    image_features,
    query_text: str,
    *,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    top_k: int = 10,
) -> list[dict]:
    """
    Rank already-normalized OpenCLIP image embeddings against text.

    image_features may be a torch Tensor from a fresh encode or a NumPy
    matrix loaded from the persistent visual cache.
    """
    import torch

    if not scene_items:
        return []

    if image_features is None:
        return []

    query_text = str(
        query_text
        or ""
    ).strip()

    if not query_text:
        raise ValueError(
            "query_text is required."
        )

    top_k = max(
        int(top_k),
        1,
    )

    device = "cpu"

    model, _, tokenizer = (
        _get_obardo_openclip_bundle(
            model_name,
            pretrained,
            device,
        )
    )

    if isinstance(
        image_features,
        torch.Tensor,
    ):
        features = (
            image_features
            .detach()
            .to(device)
        )

    else:
        features = torch.as_tensor(
            image_features,
            dtype=torch.float32,
            device=device,
        )

    if features.ndim != 2:
        raise ValueError(
            "image_features must be a 2D matrix."
        )

    if features.shape[0] != len(
        scene_items
    ):
        raise ValueError(
            "Embedding row count does not match scene count."
        )

    text_tokens = tokenizer(
        [query_text]
    ).to(device)

    with torch.no_grad():
        text_features = model.encode_text(
            text_tokens
        )

        text_features /= (
            text_features.norm(
                dim=-1,
                keepdim=True,
            )
        )

        similarities = (
            features
            @ text_features.T
        ).squeeze(1)

    results = []

    for scene, score in zip(
        scene_items,
        similarities.tolist(),
    ):
        results.append(
            {
                **scene,
                "semantic_score": float(
                    score
                ),
                "query_text": query_text,
            }
        )

    results.sort(
        key=lambda item: item[
            "semantic_score"
        ],
        reverse=True,
    )

    return results[:top_k]


def build_director_openclip_query(
    beat_text: str,
    *,
    visual_plan: dict | None = None,
    fallback_query: str | None = None,
) -> str:
    """
    Build an OpenCLIP-oriented visual description from the
    Historical Director plan.

    Search queries optimize retrieval.
    This query optimizes frame-level semantic matching.
    """

    plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    parts = []

    def add(value):
        if value is None:
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return

        value = " ".join(
            str(value).strip().split()
        )

        if not value:
            return

        lowered = value.lower()

        if not any(
            lowered == existing.lower()
            for existing in parts
        ):
            parts.append(value)

    # -----------------------------------------------------
    # Subject identity
    # -----------------------------------------------------

    add(
        plan.get("subject")
    )

    add(
        plan.get("subject_identity")
    )

    add(
        plan.get("object_focus")
    )

    # -----------------------------------------------------
    # Requested visible action
    # -----------------------------------------------------

    actions = (
        plan.get("action")
        or []
    )

    add(actions)

    # -----------------------------------------------------
    # Environment / visual context
    # -----------------------------------------------------

    add(
        plan.get("environment")
    )

    add(
        plan.get("required_elements")
    )

    add(
        plan.get("preferred_elements")
    )

    # Some Director versions use singular names.
    add(
        plan.get("required_element")
    )

    add(
        plan.get("preferred_element")
    )

    # -----------------------------------------------------
    # Director visual intent
    # -----------------------------------------------------

    add(
        plan.get("visual_intent")
    )

    # -----------------------------------------------------
    # Explicit human-action semantic cue
    # -----------------------------------------------------

    if plan.get(
        "human_action_required"
    ):
        add(
            "visible human action"
        )

        add(
            "people visibly performing the requested action"
        )

    scene_type = str(
        plan.get("scene_type")
        or ""
    ).strip()

    if scene_type:
        add(scene_type.replace("_", " "))

    # -----------------------------------------------------
    # Historical grounding
    # -----------------------------------------------------

    civilization = str(
        plan.get("civilization")
        or ""
    ).strip()

    historical_period = str(
        plan.get("historical_period")
        or ""
    ).strip()

    if civilization:
        add(civilization)

    if historical_period:
        add(historical_period)

    # -----------------------------------------------------
    # Fallback/search query remains useful, but it should
    # not dominate the Director's semantic description.
    # -----------------------------------------------------

    if fallback_query:
        add(fallback_query)

    elif beat_text:
        add(beat_text)

    if not parts:
        return str(
            fallback_query
            or beat_text
            or ""
        ).strip()

    # Keep prompt compact. CLIP benefits more from concrete
    # visual concepts than from a long prose paragraph.
    result = "; ".join(
        parts[:12]
    )

    return result[:700].strip()


def _obardo_rank_openclip_multi_prompt(
    scene_items: list[dict],
    image_features,
    *,
    visual_plan: dict | None = None,
    fallback_query: str | None = None,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    top_k: int = 10,
) -> list[dict]:
    """
    Rank cached OpenCLIP image embeddings using separate
    semantic dimensions.

    Image embeddings are reused. Only text embeddings are
    generated for subject/action/environment/history.
    """

    import torch

    if not scene_items:
        return []

    if image_features is None:
        return []

    plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    device = "cpu"

    model, _, tokenizer = (
        _get_obardo_openclip_bundle(
            model_name,
            pretrained,
            device,
        )
    )

    if isinstance(
        image_features,
        torch.Tensor,
    ):
        features = (
            image_features
            .detach()
            .to(device)
        )

    else:
        features = torch.as_tensor(
            image_features,
            dtype=torch.float32,
            device=device,
        )

    if features.ndim != 2:
        raise ValueError(
            "image_features must be a 2D matrix."
        )

    if features.shape[0] != len(
        scene_items
    ):
        raise ValueError(
            "Embedding row count does not match scene count."
        )

    def flatten(value):
        if value is None:
            return []

        if isinstance(
            value,
            (list, tuple, set),
        ):
            result = []

            for item in value:
                result.extend(
                    flatten(item)
                )

            return result

        text = " ".join(
            str(value).strip().split()
        )

        return [text] if text else []

    subjects = []

    subjects += flatten(
        plan.get("subject")
    )

    subjects += flatten(
        plan.get("subject_identity")
    )

    actions = flatten(
        plan.get("action")
    )

    environment = flatten(
        plan.get("environment")
    )

    environment += flatten(
        plan.get("required_elements")
    )

    civilization = flatten(
        plan.get("civilization")
    )

    historical_period = flatten(
        plan.get("historical_period")
    )

    # -----------------------------------------------------
    # Build concrete visual prompts.
    # -----------------------------------------------------

    subject_prompt = (
        ", ".join(subjects)
        if subjects
        else str(
            fallback_query
            or ""
        ).strip()
    )

    if subjects and actions:
        action_prompt = (
            ", ".join(subjects[:2])
            + " visibly "
            + ", ".join(actions)
        )

    elif actions:
        action_prompt = (
            "people visibly "
            + ", ".join(actions)
        )

    else:
        action_prompt = subject_prompt

    environment_prompt = (
        ", ".join(environment)
        if environment
        else subject_prompt
    )

    historical_parts = (
        civilization
        + historical_period
    )

    if historical_parts:
        historical_prompt = (
            ", ".join(
                historical_parts
            )
            + ", historically appropriate scene"
        )

    elif subjects:
        historical_prompt = (
            ", ".join(subjects[:2])
            + ", historically appropriate scene"
        )

    else:
        historical_prompt = (
            str(
                fallback_query
                or ""
            ).strip()
        )

    prompt_map = {
        "subject": subject_prompt,
        "action": action_prompt,
        "environment": environment_prompt,
        "historical": historical_prompt,
    }

    # Remove empty prompts.
    prompt_map = {
        key: value
        for key, value in prompt_map.items()
        if str(value or "").strip()
    }

    if not prompt_map:
        return []

    labels = list(
        prompt_map.keys()
    )

    prompts = [
        prompt_map[label]
        for label in labels
    ]

    text_tokens = tokenizer(
        prompts
    ).to(device)

    with torch.no_grad():

        text_features = (
            model.encode_text(
                text_tokens
            )
        )

        text_features /= (
            text_features.norm(
                dim=-1,
                keepdim=True,
            )
        )

        similarity_matrix = (
            features
            @ text_features.T
        )

    # -----------------------------------------------------
    # Dynamic weights.
    # -----------------------------------------------------

    human_action_required = bool(
        plan.get(
            "human_action_required"
        )
    )

    action_priority = str(
        plan.get(
            "action_priority"
        )
        or ""
    ).strip().lower()

    if (
        human_action_required
        and action_priority == "high"
    ):
        weights = {
            "subject": 0.25,
            "action": 0.45,
            "environment": 0.15,
            "historical": 0.15,
        }

    elif human_action_required:
        weights = {
            "subject": 0.30,
            "action": 0.35,
            "environment": 0.20,
            "historical": 0.15,
        }

    else:
        weights = {
            "subject": 0.40,
            "action": 0.20,
            "environment": 0.20,
            "historical": 0.20,
        }

    results = []

    matrix = similarity_matrix.tolist()

    for scene, row in zip(
        scene_items,
        matrix,
    ):

        component_scores = {
            label: float(score)
            for label, score in zip(
                labels,
                row,
            )
        }

        total_weight = 0.0
        weighted_score = 0.0

        for label, score in (
            component_scores.items()
        ):
            weight = float(
                weights.get(
                    label,
                    0.0,
                )
            )

            weighted_score += (
                score * weight
            )

            total_weight += weight

        if total_weight > 0:
            weighted_score /= (
                total_weight
            )

        # -------------------------------------------------
        # Action fidelity guard:
        #
        # A historically good static frame should not win
        # an action-heavy beat solely because subject and
        # environment scores are high.
        # -------------------------------------------------

        action_score = (
            component_scores.get(
                "action"
            )
        )

        subject_score = (
            component_scores.get(
                "subject"
            )
        )

        if (
            human_action_required
            and action_score is not None
        ):
            # If action trails subject too much, penalize.
            if (
                subject_score is not None
                and action_score
                < subject_score - 0.035
            ):
                weighted_score -= 0.025

            # Very weak action confidence receives an
            # additional conservative penalty.
            if action_score < 0.20:
                weighted_score -= 0.025

        result = {
            **scene,
            "semantic_score": float(
                weighted_score
            ),
            "multiprompt_score": float(
                weighted_score
            ),
            "subject_score": (
                component_scores.get(
                    "subject"
                )
            ),
            "action_score": (
                component_scores.get(
                    "action"
                )
            ),
            "environment_score": (
                component_scores.get(
                    "environment"
                )
            ),
            "historical_score": (
                component_scores.get(
                    "historical"
                )
            ),
            "multiprompt_prompts": (
                prompt_map
            ),
            "multiprompt_weights": (
                weights
            ),
            "query_text": (
                str(
                    fallback_query
                    or ""
                ).strip()
            ),
        }

        results.append(
            result
        )

    results.sort(
        key=lambda item: item[
            "semantic_score"
        ],
        reverse=True,
    )

    return results[
        :max(
            int(top_k),
            1,
        )
    ]


def rank_scene_frames_by_text(
    scene_windows: list[dict],
    query_text: str,
    *,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    visual_plan: dict | None = None,
    top_k: int = 10,
) -> list[dict]:
    """
    Rank scene representative frames against a text query using OpenCLIP.

    Public behavior remains identical. Internally, image encoding and
    text ranking are separated so visual embeddings can be cached later.
    """
    valid_items, image_features = (
        _obardo_encode_scene_frames_openclip(
            scene_windows,
            model_name=model_name,
            pretrained=pretrained,
        )
    )

    if isinstance(
        visual_plan,
        dict,
    ) and visual_plan:
        return _obardo_rank_openclip_multi_prompt(
            valid_items,
            image_features,
            visual_plan=visual_plan,
            fallback_query=query_text,
            model_name=model_name,
            pretrained=pretrained,
            top_k=top_k,
        )

    return (
        _obardo_rank_openclip_image_features_by_text(
            valid_items,
            image_features,
            query_text,
            model_name=model_name,
            pretrained=pretrained,
            top_k=top_k,
        )
    )



def _obardo_youtube_cookiefile() -> str | None:
    cookie_path = (
        Path(__file__).resolve().parents[2]
        / "cookies-youtube.txt"
    )

    if (
        cookie_path.exists()
        and cookie_path.is_file()
        and cookie_path.stat().st_size > 0
    ):
        return str(cookie_path)

    return None


def _obardo_apply_youtube_auth(
    ydl_opts: dict,
) -> dict:
    opts = dict(
        ydl_opts
        or {}
    )

    cookiefile = (
        _obardo_youtube_cookiefile()
    )

    if cookiefile:
        opts["cookiefile"] = cookiefile

    # yt-dlp current syntax.
    opts["remote_components"] = {
        "ejs:github": [],
    }

    return opts


def _search_youtube_candidates_uncached(
    query: str,
    *,
    max_results: int = 5,
) -> list[dict]:
    """
    Search YouTube candidates using yt-dlp without downloading media.
    """
    import yt_dlp

    query = str(query or "").strip()

    if not query:
        raise ValueError("query is required.")

    max_results = max(1, int(max_results))

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "js_runtimes": {
            "deno": {},
        },
        "remote_components": {
            "ejs:github": [],
        },
    }

    search_url = f"ytsearch{max_results}:{query}"

    ydl_opts = _obardo_apply_youtube_auth(ydl_opts)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            search_url,
            download=False,
        )

    entries = info.get("entries") or []

    results = []

    for index, entry in enumerate(entries, 1):
        if not entry:
            continue

        video_id = entry.get("id")

        if not video_id:
            continue

        results.append(
            {
                "rank": index,
                "video_id": video_id,
                "title": entry.get("title"),
                "uploader": entry.get("uploader"),
                "duration": entry.get("duration"),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "query": query,
            }
        )

    return results


def search_youtube_candidates(
    query: str,
    *,
    max_results: int = 5,
) -> list[dict]:
    """
    Cached wrapper around the original yt-dlp YouTube search.
    """
    import copy

    query = str(query or "").strip()

    if not query:
        raise ValueError("query is required.")

    max_results = max(
        1,
        int(max_results),
    )

    cache_key = (
        " ".join(
            query.lower().split()
        ),
        max_results,
    )

    cached = (
        _OBARDO_YOUTUBE_SEARCH_CACHE.get(
            cache_key
        )
    )

    if cached is not None:
        print(
            "O Bardo TURBO YOUTUBE SEARCH CACHE HIT | "
            f"query={query!r} | "
            f"results={len(cached)}"
        )

        return copy.deepcopy(
            cached
        )

    results = (
        _search_youtube_candidates_uncached(
            query,
            max_results=max_results,
        )
    )

    _OBARDO_YOUTUBE_SEARCH_CACHE[
        cache_key
    ] = copy.deepcopy(
        results
    )

    print(
        "O Bardo TURBO YOUTUBE SEARCH CACHE ADD | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results


def rank_youtube_candidates(
    candidates: list[dict],
    *,
    query: str,
    preferred_duration_min: float = 60.0,
    preferred_duration_max: float = 1800.0,
) -> list[dict]:
    """
    Cheap metadata ranking before preview downloads.

    This is only a pre-filter. Final scene selection must still be done
    from visual analysis of candidate previews.
    """
    import re

    query = str(query or "").strip().lower()

    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", query)
        if len(token) >= 3
    }

    strong_visual_terms = {
        "reconstruction",
        "reconstructed",
        "virtual",
        "3d",
        "documentary",
        "archaeology",
        "archaeological",
        "museum",
        "artifact",
        "history",
        "historical",
        "ancient",
        "rome",
        "roman",
        "colosseum",
    }

    weak_or_unwanted_terms = {
        "reaction",
        "podcast",
        "gameplay",
        "music",
        "song",
        "lyrics",
        "meme",
        "shorts",
        "short",
        "live stream",
        "livestream",
    }

    ranked = []

    for candidate in candidates:
        title = str(candidate.get("title") or "")
        uploader = str(candidate.get("uploader") or "")
        duration = candidate.get("duration")

        title_l = title.lower()
        uploader_l = uploader.lower()
        haystack = f"{title_l} {uploader_l}"

        score = 0.0
        reasons = []

        # Exact query token overlap.
        matched_query_tokens = sorted(
            token
            for token in query_tokens
            if token in haystack
        )

        if matched_query_tokens:
            token_score = len(matched_query_tokens) * 3.0
            score += token_score
            reasons.append(
                f"query_tokens={','.join(matched_query_tokens)}"
            )

        # Historical / visual signals.
        matched_visual_terms = sorted(
            term
            for term in strong_visual_terms
            if term in haystack
        )

        if matched_visual_terms:
            visual_score = len(matched_visual_terms) * 1.5
            score += visual_score
            reasons.append(
                f"visual_terms={','.join(matched_visual_terms)}"
            )

        # Penalize formats that are often poor sources for visual beats.
        matched_bad_terms = sorted(
            term
            for term in weak_or_unwanted_terms
            if term in haystack
        )

        if matched_bad_terms:
            penalty = len(matched_bad_terms) * 8.0
            score -= penalty
            reasons.append(
                f"penalty={','.join(matched_bad_terms)}"
            )

        # Duration preference.
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            duration_f = None

        if duration_f is not None:
            if preferred_duration_min <= duration_f <= preferred_duration_max:
                score += 2.0
                reasons.append("duration=preferred")
            elif duration_f < 30:
                score -= 5.0
                reasons.append("duration=very_short")
            elif duration_f > 3600:
                score -= 1.5
                reasons.append("duration=very_long")

        item = dict(candidate)
        item["metadata_score"] = float(score)
        item["metadata_reasons"] = reasons

        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            item["metadata_score"],
            -(item.get("rank") or 999999),
        ),
        reverse=True,
    )

    for new_rank, item in enumerate(ranked, 1):
        item["metadata_rank"] = new_rank

    return ranked

def extract_preview_sample_frames(
    preview_path,
    *,
    video_id: str,
    interval_seconds: float = 5.0,
    max_frames: int = 300,
):
    """
    Extract periodic frames from a preview for semantic OpenCLIP search.

    This is intentionally independent from scene-cut detection because
    long continuous shots may contain very few detectable cuts.
    """
    import subprocess
    from pathlib import Path

    preview_path = Path(preview_path)

    if not preview_path.exists():
        raise FileNotFoundError(preview_path)

    interval_seconds = max(
        float(interval_seconds),
        1.0,
    )

    max_frames = max(
        int(max_frames),
        1,
    )

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "frames"
        / f"{video_id}-samples"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Clear only old periodic samples for this video.
    for old_file in output_dir.glob("sample-*.jpg"):
        try:
            old_file.unlink()
        except OSError:
            pass

    output_pattern = str(
        output_dir / "sample-%06d.jpg"
    )

    vf = f"fps=1/{interval_seconds}"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(preview_path),
        "-vf",
        vf,
        "-frames:v",
        str(max_frames),
        "-q:v",
        "4",
        output_pattern,
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg sample extraction failed:\n"
            + result.stderr[-4000:]
        )

    files = sorted(
        output_dir.glob("sample-*.jpg")
    )

    samples = []

    for index, frame_path in enumerate(files):
        timestamp = index * interval_seconds

        samples.append(
            {
                "scene_index": index + 1,
                "sample_index": index + 1,
                "timestamp": float(timestamp),
                "start": float(timestamp),
                "end": float(
                    timestamp + interval_seconds
                ),
                "duration": float(interval_seconds),
                "frame_path": str(frame_path),
                "sampling_mode": "periodic",
            }
        )

    return {
        "video_id": video_id,
        "interval_seconds": interval_seconds,
        "frame_count": len(samples),
        "frames_dir": str(output_dir),
        "samples": samples,
    }

def filter_youtube_candidates_by_identity(
    candidates: list[dict],
    *,
    required_terms: list[str] | None = None,
) -> list[dict]:
    """
    Hard identity gate for historically specific subjects.

    Example:
        required_terms=["colosseum"]

    A candidate must contain every required term in its title.
    Generic searches can simply omit required_terms.
    """
    import re

    required_terms = [
        str(term).strip().lower()
        for term in (required_terms or [])
        if str(term).strip()
    ]

    if not required_terms:
        return list(candidates)

    accepted = []

    for candidate in candidates:
        title = str(
            candidate.get("title") or ""
        ).lower()

        normalized_title = re.sub(
            r"\s+",
            " ",
            title,
        )

        matched = []
        missing = []

        for term in required_terms:
            if term in normalized_title:
                matched.append(term)
            else:
                missing.append(term)

        if missing:
            continue

        item = dict(candidate)

        item["identity_gate"] = True
        item["identity_terms_matched"] = matched

        accepted.append(item)

    return accepted

def extract_preview_window_frames(
    preview_path,
    *,
    video_id: str,
    center_seconds: float,
    radius_seconds: float = 10.0,
    interval_seconds: float = 0.5,
    max_frames: int = 120,
):
    """
    Extract dense frames around a promising timestamp for fine semantic ranking.
    """
    import subprocess
    from pathlib import Path

    preview_path = Path(preview_path)

    if not preview_path.exists():
        raise FileNotFoundError(preview_path)

    center_seconds = max(float(center_seconds), 0.0)
    radius_seconds = max(float(radius_seconds), 1.0)
    interval_seconds = max(float(interval_seconds), 0.1)
    max_frames = max(int(max_frames), 1)

    start = max(
        center_seconds - radius_seconds,
        0.0,
    )

    duration = radius_seconds * 2.0

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "frames"
        / f"{video_id}-fine-{int(center_seconds)}"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    for old_file in output_dir.glob("fine-*.jpg"):
        try:
            old_file.unlink()
        except OSError:
            pass

    output_pattern = str(
        output_dir / "fine-%06d.jpg"
    )

    vf = f"fps=1/{interval_seconds}"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(preview_path),
        "-t",
        str(duration),
        "-vf",
        vf,
        "-frames:v",
        str(max_frames),
        "-q:v",
        "3",
        output_pattern,
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg fine-window extraction failed:\n"
            + result.stderr[-4000:]
        )

    files = sorted(
        output_dir.glob("fine-*.jpg")
    )

    samples = []

    for index, frame_path in enumerate(files):
        timestamp = (
            start
            + index * interval_seconds
        )

        samples.append(
            {
                "scene_index": index + 1,
                "sample_index": index + 1,
                "timestamp": float(timestamp),
                "start": float(timestamp),
                "end": float(
                    timestamp + interval_seconds
                ),
                "duration": float(interval_seconds),
                "frame_path": str(frame_path),
                "sampling_mode": "fine_window",
            }
        )

    return {
        "video_id": video_id,
        "center_seconds": center_seconds,
        "start_seconds": start,
        "end_seconds": start + duration,
        "interval_seconds": interval_seconds,
        "frame_count": len(samples),
        "frames_dir": str(output_dir),
        "samples": samples,
    }

def download_hq_clip_section(
    url: str,
    *,
    start_seconds: float,
    duration_seconds: float = 4.0,
    max_height: int = 720,
    output_name: str | None = None,
    timeout_seconds: int = 30,
) -> dict:
    """
    Download only the selected remote time window.

    This is the preferred O Bardo workflow:

        low-res preview
        -> semantic timestamp
        -> remote HQ section only
        -> local normalization

    Falls back elsewhere if the remote provider cannot
    serve the section efficiently.
    """
    import subprocess
    import sys
    import time
    from pathlib import Path

    url = str(url or "").strip()

    if not url:
        raise ValueError("url is required.")

    start_seconds = max(
        float(start_seconds),
        0.0,
    )

    duration_seconds = max(
        float(duration_seconds),
        0.5,
    )

    max_height = max(
        360,
        min(int(max_height), 1080),
    )

    timeout_seconds = max(
        int(timeout_seconds),
        5,
    )

    end_seconds = (
        start_seconds
        + duration_seconds
    )

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "hq_sections"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_name:
        stem = Path(
            str(output_name)
        ).stem
    else:
        stem = (
            "section-"
            f"{start_seconds:.3f}-"
            f"{end_seconds:.3f}"
        )

    template = str(
        output_dir
        / f"{stem}-raw.%(ext)s"
    )

    # Remove stale files from an interrupted previous test.
    for old_file in output_dir.glob(
        f"{stem}-raw.*"
    ):
        try:
            old_file.unlink()
        except OSError:
            pass

    section_spec = (
        f"*{start_seconds:.3f}-"
        f"{end_seconds:.3f}"
    )

    format_spec = (
        f"bestvideo[height<={max_height}]"
        f"/bestvideo[height<=720]"
        f"/bestvideo[height<=480]"
        f"/bestvideo"
    )

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--js-runtimes",
        "deno",
        "--remote-components",
        "ejs:npm",
        "--download-sections",
        section_spec,
        "--format",
        format_spec,
        "--output",
        template,
        url,
    ]

    started = time.time()

    creationflags = 0

    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
        )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )

    try:
        stdout, _ = process.communicate(
            timeout=timeout_seconds
        )

    except subprocess.TimeoutExpired as exc:

        if sys.platform == "win32":
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        else:
            try:
                process.kill()
            except Exception:
                pass

        try:
            process.communicate(
                timeout=5
            )
        except Exception:
            pass

        for partial in output_dir.glob(
            f"{stem}-raw.*"
        ):
            try:
                partial.unlink()
            except OSError:
                pass

        raise RuntimeError(
            "Remote HQ section download timed out "
            f"after {timeout_seconds}s."
        ) from exc

    elapsed = (
        time.time()
        - started
    )

    if process.returncode != 0:
        raise RuntimeError(
            "Remote HQ section download failed:\n"
            + (stdout or "")[-5000:]
        )

    candidates = [
        item
        for item in output_dir.glob(
            f"{stem}-raw.*"
        )
        if (
            item.is_file()
            and not item.name.endswith(".part")
        )
    ]

    if not candidates:
        raise FileNotFoundError(
            "Remote section download completed "
            "but no output file was found."
        )

    candidates.sort(
        key=lambda item: (
            item.stat().st_size
        ),
        reverse=True,
    )

    filepath = candidates[0]

    # ---------------------------------------------------------
    # Validate remote section before reporting success.
    # A non-empty file is not enough: yt-dlp/ffmpeg may leave
    # behind a truncated or invalid WebM/MP4 after a partial
    # section request.
    # ---------------------------------------------------------

    file_size = filepath.stat().st_size

    if file_size < 32 * 1024:
        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Remote HQ section is too small to be valid: "
            f"{file_size} bytes."
        )

    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type,width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(filepath),
    ]

    try:
        probe_result = subprocess.run(
            probe_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "ffprobe timed out while validating "
            "the remote HQ section."
        ) from exc

    if probe_result.returncode != 0:
        probe_error = (
            probe_result.stderr
            or probe_result.stdout
            or "unknown ffprobe error"
        )

        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Remote HQ section failed ffprobe validation: "
            + probe_error[-2000:]
        )

    import json

    try:
        probe_data = json.loads(
            probe_result.stdout or "{}"
        )
    except json.JSONDecodeError as exc:
        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "ffprobe returned invalid JSON for "
            "the remote HQ section."
        ) from exc

    streams = (
        probe_data.get("streams")
        or []
    )

    if not streams:
        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Remote HQ section contains no video stream."
        )

    duration_raw = (
        probe_data.get("format", {})
        .get("duration")
    )

    try:
        probed_duration = float(
            duration_raw
        )
    except (TypeError, ValueError):
        probed_duration = 0.0

    if probed_duration <= 0:
        try:
            filepath.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Remote HQ section has invalid duration."
        )

    return {
        "type": "hq_remote_section",
        "source_url": url,
        "filepath": str(
            filepath.resolve()
        ),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "duration_seconds": duration_seconds,
        "max_height": max_height,
        "file_size_bytes": (
            filepath.stat().st_size
        ),
        "file_size_mb": round(
            filepath.stat().st_size
            / (1024 ** 2),
            2,
        ),
        "download_seconds": round(
            elapsed,
            2,
        ),
        "temporary": True,
        "log_tail": (stdout or "")[-2000:],
    }

def _inspect_hq_candidate_uncached(
    url: str,
    *,
    max_height: int = 720,
    max_duration_seconds: float = 2700.0,
    max_size_mb: float = 350.0,
) -> dict:
    """
    Inspect a YouTube source without downloading it.

    Reject sources that are too long or whose best usable
    video-only format would exceed the configured size cap.
    """
    import yt_dlp

    url = str(url or "").strip()

    if not url:
        raise ValueError("url is required.")

    max_height = max(
        360,
        min(int(max_height), 1080),
    )

    max_duration_seconds = max(
        float(max_duration_seconds),
        60.0,
    )

    max_size_bytes = int(
        float(max_size_mb)
        * 1024
        * 1024
    )

    ydl_opts = {
        "noplaylist": True,
        "quiet": True,
        "skip_download": True,
        "js_runtimes": {
            "deno": {},
        },
        "remote_components": {
            "ejs:github": [],
        },
    }

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:
        info = ydl.extract_info(
            url,
            download=False,
        )

    duration = float(
        info.get("duration") or 0.0
    )

    if (
        duration > 0
        and duration > max_duration_seconds
    ):
        return {
            "accepted": False,
            "reason": "duration_limit",
            "duration_seconds": duration,
            "max_duration_seconds": (
                max_duration_seconds
            ),
            "video_id": info.get("id"),
            "title": info.get("title"),
        }

    usable = []

    for fmt in (
        info.get("formats") or []
    ):
        vcodec = str(
            fmt.get("vcodec") or "none"
        )

        if vcodec == "none":
            continue

        height = fmt.get("height")

        try:
            height_value = int(
                height or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            height_value = 0

        if (
            height_value
            and height_value > max_height
        ):
            continue

        filesize = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
        )

        try:
            size_bytes = (
                int(filesize)
                if filesize
                else 0
            )
        except (
            TypeError,
            ValueError,
        ):
            size_bytes = 0

        # If yt-dlp has no explicit size estimate,
        # derive one from bitrate and duration.
        if (
            not size_bytes
            and duration > 0
        ):
            try:
                tbr = float(
                    fmt.get("tbr") or 0.0
                )
            except (
                TypeError,
                ValueError,
            ):
                tbr = 0.0

            if tbr > 0:
                size_bytes = int(
                    (
                        tbr
                        * 1000
                        / 8
                    )
                    * duration
                )

        if not size_bytes:
            continue

        if size_bytes > max_size_bytes:
            continue

        usable.append(
            {
                "format_id": str(
                    fmt.get("format_id")
                    or ""
                ),
                "height": height_value,
                "width": fmt.get("width"),
                "fps": fmt.get("fps"),
                "vcodec": vcodec,
                "ext": fmt.get("ext"),
                "filesize_bytes": (
                    size_bytes
                ),
                "filesize_mb": round(
                    size_bytes
                    / (1024 ** 2),
                    2,
                ),
                "tbr": fmt.get("tbr"),
            }
        )

    if not usable:
        return {
            "accepted": False,
            "reason": "no_format_under_limit",
            "duration_seconds": duration,
            "max_height": max_height,
            "max_size_mb": max_size_mb,
            "video_id": info.get("id"),
            "title": info.get("title"),
        }

    # Prefer the highest resolution available,
    # then the smaller file among equivalent heights.
    usable.sort(
        key=lambda item: (
            item["height"],
            -item["filesize_bytes"],
        ),
        reverse=True,
    )

    selected = usable[0]

    return {
        "accepted": True,
        "reason": "accepted",
        "duration_seconds": duration,
        "video_id": info.get("id"),
        "title": info.get("title"),
        "selected_format": selected,
        "max_height": max_height,
        "max_size_mb": max_size_mb,
    }


def inspect_hq_candidate(
    url: str,
    *,
    max_height: int = 720,
    max_duration_seconds: float = 2700.0,
    max_size_mb: float = 350.0,
) -> dict:
    """
    Cached wrapper around the original yt-dlp HQ preflight.
    """
    import copy

    url = str(url or "").strip()

    if not url:
        raise ValueError("url is required.")

    normalized_max_height = max(
        360,
        min(
            int(max_height),
            1080,
        ),
    )

    normalized_max_duration = max(
        float(max_duration_seconds),
        60.0,
    )

    normalized_max_size = float(
        max_size_mb
    )

    cache_key = (
        url,
        normalized_max_height,
        normalized_max_duration,
        normalized_max_size,
    )

    cached = (
        _OBARDO_YOUTUBE_PREFLIGHT_CACHE.get(
            cache_key
        )
    )

    if cached is not None:
        print(
            "O Bardo TURBO PREFLIGHT CACHE HIT | "
            f"url={url} | "
            f"accepted={cached.get('accepted')}"
        )

        return copy.deepcopy(
            cached
        )

    result = (
        _inspect_hq_candidate_uncached(
            url,
            max_height=normalized_max_height,
            max_duration_seconds=(
                normalized_max_duration
            ),
            max_size_mb=normalized_max_size,
        )
    )

    _OBARDO_YOUTUBE_PREFLIGHT_CACHE[
        cache_key
    ] = copy.deepcopy(
        result
    )

    print(
        "O Bardo TURBO PREFLIGHT CACHE ADD | "
        f"url={url} | "
        f"accepted={result.get('accepted')}"
    )

    return result


def download_hq_source_video(
    url: str,
    *,
    max_height: int = 720,
    preferred_format_id: str | None = None,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> dict:
    """
    Download one temporary video-only HQ source.

    Intended workflow:
        preview -> semantic selection -> HQ source -> local clips -> delete HQ source
    """
    import time
    from pathlib import Path
    import yt_dlp

    url = str(url or "").strip()

    if not url:
        raise ValueError("url is required.")

    max_height = max(int(max_height), 360)

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "hq_sources"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    ydl_opts = {
        "noplaylist": True,
        "quiet": False,
        "js_runtimes": {
            "deno": {},
        },
        "remote_components": {
            "ejs:github": [],
        },
        "format": (
            str(preferred_format_id)
            if preferred_format_id
            else (
                f"bestvideo[height<={max_height}]"
                f"/bestvideo[height<=720]"
                f"/bestvideo[height<=480]"
            )
        ),
        "outtmpl": str(
            output_dir
            / "%(id)s-HQ-source.%(ext)s"
        ),
    }

    started = time.time()

    ydl_opts = _obardo_apply_youtube_auth(ydl_opts)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            url,
            download=True,
        )

        requested = (
            info.get("requested_downloads")
            or []
        )

        filepath = None

        for item in requested:
            candidate = item.get("filepath")

            if candidate and Path(candidate).exists():
                filepath = Path(candidate)
                break

        if filepath is None:
            prepared = Path(
                ydl.prepare_filename(info)
            )

            if prepared.exists():
                filepath = prepared

    video_id = str(info.get("id") or "").strip()

    if filepath is None and video_id:
        matches = sorted(
            output_dir.glob(
                f"{video_id}-HQ-source.*"
            )
        )

        matches = [
            p for p in matches
            if not p.name.endswith(".part")
        ]

        if matches:
            filepath = matches[0]

    if filepath is None or not filepath.exists():
        raise FileNotFoundError(
            "HQ source download finished but output file was not found."
        )

    elapsed = time.time() - started

    size_bytes = filepath.stat().st_size

    result = {
        "type": "hq_source",
        "source_url": url,
        "video_id": video_id,
        "title": info.get("title"),
        "filepath": str(filepath.resolve()),
        "ext": filepath.suffix.lstrip("."),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "format_id": info.get("format_id"),
        "duration": info.get("duration"),
        "file_size_bytes": size_bytes,
        "file_size_mb": round(
            size_bytes / (1024 ** 2),
            2,
        ),
        "download_seconds": round(
            elapsed,
            2,
        ),
        "temporary": True,
    }

    return result

def extract_hq_clip(
    source_path,
    output_path,
    *,
    start_seconds: float,
    duration_seconds: float = 4.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    keep_source_audio: bool = False,
) -> dict:
    """
    Extract and normalize an exact local HQ clip.

    Output:
        H.264
        yuv420p
        fixed FPS
        scale + pad without stretching
        silent by default
    """
    import subprocess
    from pathlib import Path

    source_path = Path(source_path)
    output_path = Path(output_path)

    if not source_path.exists():
        raise FileNotFoundError(source_path)

    start_seconds = max(float(start_seconds), 0.0)
    duration_seconds = max(float(duration_seconds), 0.1)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    vf = (
        f"scale={width}:{height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},"
        "format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(source_path),
        "-t",
        str(duration_seconds),
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
    ]

    if keep_source_audio:
        cmd += [
            "-map",
            "0:a:0?",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
        ]
    else:
        cmd += ["-an"]

    cmd += [
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(
            "HQ clip extraction failed:\n"
            + result.stderr[-5000:]
        )

    if not output_path.exists():
        raise FileNotFoundError(
            "FFmpeg completed but output clip was not found."
        )

    return {
        "type": "hq_clip",
        "source_path": str(source_path.resolve()),
        "filepath": str(output_path.resolve()),
        "start_seconds": start_seconds,
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "source_audio": bool(keep_source_audio),
        "file_size_bytes": output_path.stat().st_size,
        "file_size_mb": round(
            output_path.stat().st_size / (1024 ** 2),
            2,
        ),
    }

# ------------------------------------------------------------
# O Bardo 4.17H
# Persistent YouTube OpenAI editorial cache.
#
# Identity:
#   video_id
#   + timestamp
#   + beat_text
#   + context_text
#   + visual_plan
#   + explicit cache schema/prompt version
#
# Search/ranking, OpenCLIP, fine refinement and editorial gates
# remain untouched. Only an identical OpenAI image evaluation
# may be reused.
# ------------------------------------------------------------

_OBARDO_YOUTUBE_OPENAI_CACHE_VERSION = "417H-v1"
_OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE = {}
_OBARDO_YOUTUBE_OPENAI_DISK_CACHE = {}
_OBARDO_YOUTUBE_OPENAI_DISK_CACHE_LOADED = False
_OBARDO_YOUTUBE_OPENAI_CACHE_MAX_ENTRIES = 5000

_OBARDO_YOUTUBE_OPENAI_CACHE_FILE = (
    PROJECT_ROOT
    / "storage"
    / "obardo_cache"
    / "youtube_openai_editorial.json"
)

_OBARDO_YOUTUBE_OPENAI_CACHE_LOCK = (
    __import__("threading").RLock()
)


def _obardo_youtube_openai_cache_key(
    *,
    video_id,
    timestamp,
    beat_text,
    context_text=None,
    visual_plan=None,
) -> str:
    import hashlib
    import json

    try:
        normalized_plan = json.dumps(
            visual_plan or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        normalized_plan = str(
            visual_plan
            or {}
        )

    identity = {
        "version": (
            _OBARDO_YOUTUBE_OPENAI_CACHE_VERSION
        ),
        "video_id": str(
            video_id
            or ""
        ).strip(),
        "timestamp": round(
            float(
                timestamp
                or 0.0
            ),
            3,
        ),
        "beat_text": " ".join(
            str(
                beat_text
                or ""
            ).split()
        ),
        "context_text": " ".join(
            str(
                context_text
                or ""
            ).split()
        ),
        "visual_plan": normalized_plan,
    }

    serialized = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        serialized.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


def _obardo_load_youtube_openai_disk_cache():
    global _OBARDO_YOUTUBE_OPENAI_DISK_CACHE_LOADED
    global _OBARDO_YOUTUBE_OPENAI_DISK_CACHE

    with _OBARDO_YOUTUBE_OPENAI_CACHE_LOCK:
        if (
            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE_LOADED
        ):
            return

        _OBARDO_YOUTUBE_OPENAI_DISK_CACHE_LOADED = True
        _OBARDO_YOUTUBE_OPENAI_DISK_CACHE = {}

        try:
            if not (
                _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
                .exists()
            ):
                return

            import json

            payload = json.loads(
                _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
                .read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(
                payload,
                dict,
            ):
                return

            if (
                payload.get("version")
                != _OBARDO_YOUTUBE_OPENAI_CACHE_VERSION
            ):
                return

            entries = payload.get(
                "entries"
            )

            if not isinstance(
                entries,
                dict,
            ):
                return

            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE = (
                entries
            )

        except Exception:
            # Fail-open: corrupt cache must never break media
            # resolution.
            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE = {}


def _obardo_youtube_openai_evaluation_cacheable(
    evaluation,
) -> bool:
    """
    Reject internally inconsistent OpenAI editorial responses.

    Scores are defined on a 0-100 scale. An ACCEPT with
    extremely low relevance/historical scores must not become
    sticky through the persistent cache.
    """
    if not isinstance(
        evaluation,
        dict,
    ) or not evaluation:
        return False

    decision = str(
        evaluation.get(
            "decision",
            "",
        )
        or ""
    ).strip().lower()

    relevance = int(
        evaluation.get(
            "relevance_score",
            0,
        )
        or 0
    )

    historical = int(
        evaluation.get(
            "historical_score",
            0,
        )
        or 0
    )

    if (
        decision == "accept"
        and (
            relevance < 25
            or historical < 25
        )
    ):
        return False

    return True


def _obardo_get_youtube_openai_cache(
    cache_key: str,
):
    import copy

    cache_key = str(
        cache_key
        or ""
    ).strip()

    if not cache_key:
        return None

    with _OBARDO_YOUTUBE_OPENAI_CACHE_LOCK:
        ram_value = (
            _OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE
            .get(
                cache_key
            )
        )

        if isinstance(
            ram_value,
            dict,
        ) and ram_value:
            if (
                _obardo_youtube_openai_evaluation_cacheable(
                    ram_value
                )
            ):
                return copy.deepcopy(
                    ram_value
                )

            _OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE.pop(
                cache_key,
                None,
            )

        _obardo_load_youtube_openai_disk_cache()

        disk_value = (
            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE
            .get(
                cache_key
            )
        )

        if not isinstance(
            disk_value,
            dict,
        ) or not disk_value:
            return None

        evaluation = disk_value.get(
            "evaluation"
        )

        if not isinstance(
            evaluation,
            dict,
        ) or not evaluation:
            return None

        if not (
            _obardo_youtube_openai_evaluation_cacheable(
                evaluation
            )
        ):
            # Ignore stale/incoherent cached editorial data.
            # A fresh OpenAI evaluation will be requested.
            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE.pop(
                cache_key,
                None,
            )

            _OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE.pop(
                cache_key,
                None,
            )

            return None

        evaluation_copy = copy.deepcopy(
            evaluation
        )

        _OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE[
            cache_key
        ] = copy.deepcopy(
            evaluation_copy
        )

        return evaluation_copy


def _obardo_save_youtube_openai_cache(
    cache_key: str,
    evaluation: dict,
):
    import copy
    import json
    import time

    cache_key = str(
        cache_key
        or ""
    ).strip()

    if (
        not cache_key
        or not (
            _obardo_youtube_openai_evaluation_cacheable(
                evaluation
            )
        )
    ):
        return False

    evaluation_copy = copy.deepcopy(
        evaluation
    )

    with _OBARDO_YOUTUBE_OPENAI_CACHE_LOCK:
        _OBARDO_YOUTUBE_OPENAI_EDITORIAL_CACHE[
            cache_key
        ] = copy.deepcopy(
            evaluation_copy
        )

        _obardo_load_youtube_openai_disk_cache()

        _OBARDO_YOUTUBE_OPENAI_DISK_CACHE[
            cache_key
        ] = {
            "saved_at": int(
                time.time()
            ),
            "evaluation": (
                evaluation_copy
            ),
        }

        # Conservative bounded growth.
        max_entries = max(
            int(
                _OBARDO_YOUTUBE_OPENAI_CACHE_MAX_ENTRIES
            ),
            100,
        )

        if (
            len(
                _OBARDO_YOUTUBE_OPENAI_DISK_CACHE
            )
            > max_entries
        ):
            ordered_items = sorted(
                _OBARDO_YOUTUBE_OPENAI_DISK_CACHE.items(),
                key=lambda item: int(
                    (
                        item[1]
                        if isinstance(
                            item[1],
                            dict,
                        )
                        else {}
                    ).get(
                        "saved_at",
                        0,
                    )
                    or 0
                ),
                reverse=True,
            )

            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE.clear()

            _OBARDO_YOUTUBE_OPENAI_DISK_CACHE.update(
                dict(
                    ordered_items[
                        :max_entries
                    ]
                )
            )

        try:
            cache_dir = (
                _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
                .parent
            )

            cache_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            payload = {
                "version": (
                    _OBARDO_YOUTUBE_OPENAI_CACHE_VERSION
                ),
                "entries": (
                    _OBARDO_YOUTUBE_OPENAI_DISK_CACHE
                ),
            }

            temp_path = (
                _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
                .with_name(
                    _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
                    .name
                    + ".tmp"
                )
            )

            temp_path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
                encoding="utf-8",
            )

            temp_path.replace(
                _OBARDO_YOUTUBE_OPENAI_CACHE_FILE
            )

        except Exception:
            # RAM cache remains valid even when disk persistence
            # fails.
            return True

    return True


def _obardo_evaluate_youtube_openai_cached(
    *,
    editor,
    frame_path,
    video_id,
    timestamp,
    beat_text,
    context_text=None,
    visual_plan=None,
    logger=None,
):
    cache_key = (
        _obardo_youtube_openai_cache_key(
            video_id=video_id,
            timestamp=timestamp,
            beat_text=beat_text,
            context_text=context_text,
            visual_plan=visual_plan,
        )
    )

    cached = (
        _obardo_get_youtube_openai_cache(
            cache_key
        )
    )

    if cached:
        if logger is not None:
            logger.info(
                "O Bardo 4.17H OPENAI CACHE HIT | "
                f"video_id={video_id} | "
                f"timestamp={float(timestamp or 0.0):.3f}"
            )

        return cached

    evaluation = (
        editor
        .evaluate_image_candidate(
            frame_path,
            beat_text=beat_text,
            context_text=context_text,
            visual_plan=visual_plan or {},
        )
        or {}
    )

    if isinstance(
        evaluation,
        dict,
    ) and evaluation:
        # Store the raw editorial response before downstream
        # code adds transient timestamp/OpenCLIP metadata.
        _obardo_save_youtube_openai_cache(
            cache_key,
            evaluation,
        )

        if logger is not None:
            logger.info(
                "O Bardo 4.17H OPENAI CACHE ADD | "
                f"video_id={video_id} | "
                f"timestamp={float(timestamp or 0.0):.3f}"
            )

    return evaluation


def resolve_video_for_beat(
    beat_text: str,
    *,
    context_text: str | None = None,
    visual_plan: dict | None = None,
    search_query: str | None = None,
    required_terms: list[str] | None = None,
    search_results: int = 15,
    candidate_count: int = 3,
    coarse_interval_seconds: float = 5.0,
    fine_radius_seconds: float = 10.0,
    fine_interval_seconds: float = 0.5,
    clip_duration_seconds: float = 4.0,
    max_preview_height: int = 360,
    max_hq_height: int = 720,
    output_name: str | None = None,
    cleanup_hq_source: bool = True,
    avoid_media_keys: list[str] | None = None,
    preflight_only: bool = False,
    skip_openai_coarse_for_test: bool = False,
    _hq_candidate_retry_depth: int = 0,
) -> dict:
    """
    Resolve one visual beat into a final normalized external video clip.

    Pipeline:
        search
        -> metadata ranking
        -> optional identity gate
        -> preview tournament
        -> coarse OpenCLIP ranking
        -> fine OpenCLIP ranking
        -> HQ source download
        -> local normalized H.264 clip
        -> optional HQ source cleanup
    """
    import re
    from pathlib import Path
    from loguru import logger

    # ---------------------------------------------------------
    # O Bardo 4.17G
    # Conservative process-local temporary housekeeping.
    #
    # Rules:
    # - run at most once per Python process;
    # - only on the primary resolver invocation;
    # - delete only files older than 24 hours;
    # - never touch final clips;
    # - never touch persistent obardo_cache;
    # - cleanup failure must never break media resolution.
    # ---------------------------------------------------------

    if (
        int(
            _hq_candidate_retry_depth
            or 0
        )
        == 0
        and not globals().get(
            "_OBARDO_417G_HOUSEKEEPING_DONE",
            False,
        )
    ):
        globals()[
            "_OBARDO_417G_HOUSEKEEPING_DONE"
        ] = True

        try:
            housekeeping_now = (
                __import__("time").time()
            )

            housekeeping_max_age_seconds = (
                24 * 60 * 60
            )

            housekeeping_cutoff = (
                housekeeping_now
                - housekeeping_max_age_seconds
            )

            housekeeping_targets = [
                OBARDO_PREVIEWS_DIR,
                OBARDO_FRAMES_DIR,
                OBARDO_TEMP_ROOT
                / "hq_sections",
                OBARDO_TEMP_ROOT
                / "hq_sources",
            ]

            housekeeping_removed_files = 0
            housekeeping_removed_dirs = 0
            housekeeping_removed_bytes = 0

            for housekeeping_root in (
                housekeeping_targets
            ):
                if (
                    not housekeeping_root.exists()
                    or not housekeeping_root.is_dir()
                ):
                    continue

                housekeeping_entries = list(
                    housekeeping_root.rglob("*")
                )

                # Files first.
                for housekeeping_item in (
                    housekeeping_entries
                ):
                    try:
                        if housekeeping_item.is_symlink():
                            item_stat = (
                                housekeeping_item.lstat()
                            )

                            if (
                                float(
                                    item_stat.st_mtime
                                )
                                >= housekeeping_cutoff
                            ):
                                continue

                            housekeeping_item.unlink()

                            housekeeping_removed_files += 1
                            continue

                        if not housekeeping_item.is_file():
                            continue

                        item_stat = (
                            housekeeping_item.stat()
                        )

                        if (
                            float(
                                item_stat.st_mtime
                            )
                            >= housekeeping_cutoff
                        ):
                            continue

                        housekeeping_removed_bytes += (
                            int(
                                item_stat.st_size
                            )
                        )

                        housekeeping_item.unlink()

                        housekeeping_removed_files += 1

                    except OSError:
                        continue

                # Remove only directories that became empty.
                # Deepest directories are attempted first.
                housekeeping_dirs = sorted(
                    (
                        item
                        for item in housekeeping_entries
                        if (
                            item.exists()
                            and item.is_dir()
                            and not item.is_symlink()
                        )
                    ),
                    key=lambda item: len(
                        item.parts
                    ),
                    reverse=True,
                )

                for housekeeping_dir in (
                    housekeeping_dirs
                ):
                    try:
                        housekeeping_dir.rmdir()

                        housekeeping_removed_dirs += 1

                    except OSError:
                        # Non-empty / active directories stay.
                        continue

            logger.info(
                "O Bardo 4.17G HOUSEKEEPING | "
                f"files={housekeeping_removed_files} | "
                f"dirs={housekeeping_removed_dirs} | "
                f"freed_mb="
                f"{housekeeping_removed_bytes / (1024 ** 2):.2f} | "
                "max_age_hours=24"
            )

        except Exception as housekeeping_exc:
            logger.warning(
                "O Bardo 4.17G HOUSEKEEPING FAILED | "
                f"{type(housekeeping_exc).__name__}: "
                f"{housekeeping_exc}"
            )

    beat_text = str(beat_text or "").strip()

    if not beat_text:
        raise ValueError("beat_text is required.")

    search_query = str(
        search_query or ""
    ).strip()

    # ---------------------------------------------------------
    # Automatic historical identity + visual query
    # ---------------------------------------------------------

    identity = detect_historical_identity(
        beat_text
    )

    _obardo_t_youtube_total = __import__("time").perf_counter()
    _obardo_t_search_preflight = _obardo_t_youtube_total

    # ---------------------------------------------------------
    # Effective YouTube query.
    #
    # The master router may provide the raw narration sentence
    # as search_query. When an OpenAI visual plan exists, use
    # its historically specific English queries instead.
    # ---------------------------------------------------------
    query = ""

    plan_primary_queries = []

    plan_context_queries = []

    if isinstance(
        visual_plan,
        dict,
    ):
        plan_primary_queries = [
            str(item).strip()
            for item in (
                visual_plan.get(
                    "primary_queries"
                )
                or []
            )
            if str(item).strip()
        ]

        plan_context_queries = [
            str(item).strip()
            for item in (
                visual_plan.get(
                    "context_queries"
                )
                or []
            )
            if str(item).strip()
        ]

    # The caller may already have produced a retrieval query
    # through the master router (for example the compact
    # human-action fallback from 4.16C2).
    #
    # Preserve that explicit retrieval query when it is not
    # simply the raw narration text.
    #
    # If search_query is effectively the narration itself,
    # OpenAI primary/context queries may still improve retrieval.
    normalized_explicit_query = " ".join(
        str(
            search_query
            or ""
        ).lower().split()
    )

    normalized_beat_query = " ".join(
        str(
            beat_text
            or ""
        ).lower().split()
    )

    explicit_retrieval_query = bool(
        search_query
        and normalized_explicit_query
        != normalized_beat_query
    )

    if explicit_retrieval_query:
        query = str(
            search_query
        ).strip()

    # The OpenAI primary query describes what the CURRENT
    # narration beat should actually show.
    #
    # Context queries describe supporting/background material
    # and must not override the primary visual subject.
    elif plan_primary_queries:
        query = plan_primary_queries[0]

    elif plan_context_queries:
        query = plan_context_queries[0]

    elif search_query:
        query = str(
            search_query
        ).strip()

    else:
        query = build_visual_search_query(
            beat_text,
            identity=identity,
        )

    if required_terms is None:
        effective_required_terms = list(
            identity.get("required_terms") or []
        )
    else:
        effective_required_terms = list(
            required_terms
        )

    # Use the generated visual query for CLIP whenever possible.
    visual_query = build_director_openclip_query(
        beat_text,
        visual_plan=visual_plan,
        fallback_query=query,
    )

    logger.info(
        "O Bardo OPENCLIP DIRECTOR QUERY | {}",
        visual_query,
    )

    # ---------------------------------------------------------
    # O Bardo 4.16F
    # Relax only the EDITORIAL copy of the visual plan for
    # transient human-action beats.
    #
    # Search/retrieval keeps the complete visual plan.
    #
    # For actions such as approaching, arriving or entering,
    # requiring a single frame to reproduce the exact transient
    # phase can reject historically excellent documentary media.
    #
    # The narration remains authoritative and period/location/
    # preferred media/avoid constraints remain intact.
    # ---------------------------------------------------------
    editorial_visual_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    import unicodedata

    transient_probe = unicodedata.normalize(
        "NFKD",
        str(beat_text or "").lower(),
    )

    transient_probe = "".join(
        char
        for char in transient_probe
        if not unicodedata.combining(char)
    )

    transient_probe = " ".join(
        transient_probe.strip().split()
    )

    transient_action_markers = (
        " approach",
        "approach ",
        "approached",
        "approaching",
        " arrive",
        "arrived",
        "arriving",
        " enter",
        "entered",
        "entering",
        " gather",
        "gathered",
        "gathering",
        "moving toward",
        "move toward",
        " aproxim",
        "aproximou",
        "aproximando",
        " chega",
        "chegou",
        "chegando",
        " entra",
        "entrou",
        "entrando",
        " reuni",
        "reunindo",
    )

    transient_human_action = any(
        marker in transient_probe
        for marker in transient_action_markers
    )

    # Narration used only by the YouTube editorial image review.
    #
    # Search, routing and OpenCLIP continue using the original
    # narration/query. For a transient human action, however, a
    # single frozen frame should not be required to prove the
    # exact phase of motion.
    editorial_beat_text = beat_text

    if transient_human_action:
        import re

        stable_subject = str(
            query
            or beat_text
            or ""
        ).strip()

        transient_query_patterns = (
            r"\bmoving\s+toward\b",
            r"\bmove\s+toward\b",
            r"\bapproaching\b",
            r"\bapproached\b",
            r"\bapproach\b",
            r"\barriving\b",
            r"\barrived\b",
            r"\barrive\b",
            r"\bentering\b",
            r"\bentered\b",
            r"\benter\b",
            r"\bgathering\b",
            r"\bgathered\b",
            r"\bgather\b",
            r"\baproximando\b",
            r"\baproximou\b",
            r"\baproximar\b",
            r"\bchegando\b",
            r"\bchegou\b",
            r"\bchegar\b",
            r"\bentrando\b",
            r"\bentrou\b",
            r"\bentrar\b",
            r"\breunindo\b",
            r"\breuniu\b",
            r"\breunir\b",
        )

        for pattern in transient_query_patterns:
            stable_subject = re.sub(
                pattern,
                " ",
                stable_subject,
                flags=re.IGNORECASE,
            )

        stable_subject = re.sub(
            r"\s+",
            " ",
            stable_subject,
        ).strip(" ,.;:-")

        if not stable_subject:
            stable_subject = str(
                query
                or beat_text
            ).strip()

        editorial_beat_text = (
            "Historically accurate documentary visual of "
            f"{stable_subject}. "
            "For this single-frame evaluation, the exact "
            "transient phase of movement does not need to be "
            "visibly frozen in the frame. Require the correct "
            "historical people or crowd, subject, place, period "
            "and event context, and reject misleading or "
            "anachronistic imagery."
        )

    if (
        transient_human_action
        and editorial_visual_plan
    ):
        editorial_visual_plan = {
            key: editorial_visual_plan[key]
            for key in (
                "period",
                "location",
                "preferred_media",
                "avoid",
            )
            if key in editorial_visual_plan
        }

    # ---------------------------------------------------------
    # 1. Search + cheap metadata ranking
    # ---------------------------------------------------------

    candidates = search_youtube_candidates(
        query,
        max_results=search_results,
    )

    ranked_candidates = rank_youtube_candidates(
        candidates,
        query=query,
    )

    # ---------------------------------------------------------
    # Contextual historical refinement
    # ---------------------------------------------------------

    ranked_candidates = refine_youtube_candidates_for_context(
        ranked_candidates,
        beat_text=beat_text,
        search_query=query,
    )

    ranked_candidates = filter_candidates_by_historical_context(
        ranked_candidates,
        beat_text=beat_text,
        search_query=query,
    )

    ranked_candidates = prune_contextually_weak_candidates(
        ranked_candidates,
        beat_text=beat_text,
        minimum_context_score=8.0,
    )

    # ---------------------------------------------------------
    # Specific historical identity gate
    # ---------------------------------------------------------

    identity_terms = identity.get(
        "youtube_identity_terms"
    ) or []

    if identity.get("specific") and identity_terms:
        ranked_candidates = (
            filter_youtube_candidates_by_identity_any(
                ranked_candidates,
                identity_terms=identity_terms,
            )
        )

    if not ranked_candidates:
        raise RuntimeError(
            "No YouTube candidates survived the search/identity gate."
        )

    # ---------------------------------------------------------
    # Preflight before preview download
    # ---------------------------------------------------------

    top_candidates = []

    preflight_limit = max(
        1,
        int(candidate_count),
    )

    avoided_keys = set(
        avoid_media_keys
        or []
    )

    preferred_ranked_candidates = [
        item
        for item in ranked_candidates
        if (
            "youtube:"
            + str(
                item.get(
                    "video_id"
                )
                or ""
            ).strip()
        )
        not in avoided_keys
    ]

    # Strong preference against repetition. If absolutely every
    # viable result was recently used, retain the original pool
    # as a last-resort quality fallback.
    if preferred_ranked_candidates:
        ranked_candidates = (
            preferred_ranked_candidates
        )

    for candidate_item in ranked_candidates:
        candidate_video_id = str(
            candidate_item.get("video_id")
            or ""
        ).strip()

        if (
            candidate_video_id
            and candidate_video_id
            in _OBARDO_YOUTUBE_HQ_FAILED_IDS
        ):
            logger.info(
                "O Bardo TURBO HQ CACHE SKIP | "
                f"video_id={candidate_video_id} | "
                "reason=previous_hq_failure"
            )
            continue

        try:
            preflight = inspect_hq_candidate(
                candidate_item["url"],
                max_height=min(
                    int(max_hq_height),
                    720,
                ),
                max_duration_seconds=2700,
                max_size_mb=350,
            )

        except Exception as exc:
            candidate_item[
                "_obardo_preflight_error"
            ] = (
                f"{type(exc).__name__}: {exc}"
            )
            continue

        candidate_item[
            "_obardo_hq_preflight"
        ] = preflight

        if not preflight.get(
            "accepted"
        ):
            continue

        top_candidates.append(
            candidate_item
        )

        if len(top_candidates) >= (
            preflight_limit
        ):
            break

    if not top_candidates:
        raise RuntimeError(
            "No YouTube candidate passed "
            "the pre-download safety limits."
        )

    logger.info(
        "O Bardo TIMING | youtube_search_preflight="
        f"{__import__('time').perf_counter() - _obardo_t_search_preflight:.2f}s"
    )

    if preflight_only:
        logger.info(
            "O Bardo TURBO YOUTUBE SPECULATIVE "
            "PREFLIGHT READY | query={!r} | candidates={}",
            query,
            len(top_candidates),
        )

        return {
            "route": "youtube_preflight_only",
            "query": query,
            "candidate_count": len(
                top_candidates
            ),
        }

    _obardo_t_coarse = __import__("time").perf_counter()

    # ---------------------------------------------------------
    # 2. Coarse visual tournament
    # ---------------------------------------------------------

    tournament = []

    # ---------------------------------------------------------
    # Parallel preview acquisition.
    #
    # Network/cache work can safely overlap across candidates.
    # Frame extraction and OpenCLIP remain serial to avoid CPU
    # contention and shared-model inference concurrency.
    # ---------------------------------------------------------
    from concurrent.futures import (
        ThreadPoolExecutor,
        as_completed,
    )

    preview_results = {}

    def _download_candidate_preview(candidate):
        started = __import__("time").perf_counter()

        preview = download_preview(
            candidate["url"],
            max_height=max_preview_height,
        )

        elapsed = (
            __import__("time").perf_counter()
            - started
        )

        return preview, elapsed

    max_preview_workers = max(
        1,
        min(
            len(top_candidates),
            3,
        ),
    )

    with ThreadPoolExecutor(
        max_workers=max_preview_workers
    ) as executor:
        future_map = {
            executor.submit(
                _download_candidate_preview,
                candidate,
            ): candidate
            for candidate in top_candidates
        }

        for future in as_completed(
            future_map
        ):
            candidate = future_map[
                future
            ]

            video_id = str(
                candidate.get(
                    "video_id"
                )
                or ""
            )

            try:
                preview, preview_elapsed = (
                    future.result()
                )

                preview_results[
                    video_id
                ] = {
                    "preview": preview,
                    "preview_elapsed": (
                        preview_elapsed
                    ),
                }

            except Exception as exc:
                preview_results[
                    video_id
                ] = {
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }

    # ---------------------------------------------------------
    # Serial local semantic analysis.
    # ---------------------------------------------------------
    for candidate in top_candidates:
        video_id = str(
            candidate.get(
                "video_id"
            )
            or ""
        )

        preview_item = (
            preview_results.get(
                video_id
            )
            or {}
        )

        if preview_item.get(
            "error"
        ):
            tournament.append(
                {
                    "candidate": candidate,
                    "error": (
                        preview_item[
                            "error"
                        ]
                    ),
                }
            )
            continue

        preview = preview_item.get(
            "preview"
        )

        if not preview:
            tournament.append(
                {
                    "candidate": candidate,
                    "error": (
                        "Preview acquisition "
                        "returned no result."
                    ),
                }
            )
            continue

        try:
            _obardo_t_candidate = (
                __import__(
                    "time"
                ).perf_counter()
            )

            preview_elapsed = float(
                preview_item.get(
                    "preview_elapsed",
                    0.0,
                )
                or 0.0
            )

            # -------------------------------------------------
            # O Bardo 4.17D
            # Same-process YouTube coarse visual cache.
            #
            # Important:
            # - keeps max_frames=60 unchanged;
            # - keeps coarse_interval_seconds unchanged;
            # - does not bypass OpenAI/editorial gates;
            # - only reuses a previous coarse result when its
            #   temporary frame files still exist.
            #
            # This primarily helps when the resolver is invoked
            # again in the same process after an HQ acquisition
            # failure for the first selected candidate.
            # -------------------------------------------------

            coarse_cache = globals().setdefault(
                "_OBARDO_YOUTUBE_COARSE_VISUAL_CACHE",
                {},
            )

            coarse_cache_key = (
                str(
                    candidate.get(
                        "video_id"
                    )
                    or ""
                ).strip(),
                " ".join(
                    str(
                        visual_query
                        or ""
                    ).split()
                ),
                round(
                    float(
                        coarse_interval_seconds
                    ),
                    6,
                ),
                60,
            )

            cached_coarse = coarse_cache.get(
                coarse_cache_key
            )

            cached_visual_rank = []

            if isinstance(
                cached_coarse,
                dict,
            ):
                cached_visual_rank = (
                    cached_coarse.get(
                        "visual_rank"
                    )
                    or []
                )

            coarse_cache_valid = bool(
                cached_visual_rank
            )

            if coarse_cache_valid:
                for cached_frame in cached_visual_rank:
                    cached_frame_path = (
                        _obardo_frame_path(
                            cached_frame
                        )
                    )

                    if (
                        not cached_frame_path
                        or not Path(
                            cached_frame_path
                        ).exists()
                    ):
                        coarse_cache_valid = False
                        break

            if coarse_cache_valid:
                sampled = dict(
                    cached_coarse.get(
                        "sampled"
                    )
                    or {}
                )

                if isinstance(
                    sampled.get("samples"),
                    list,
                ):
                    sampled["samples"] = [
                        dict(item)
                        if isinstance(item, dict)
                        else item
                        for item in sampled[
                            "samples"
                        ]
                    ]

                visual_rank = [
                    dict(item)
                    if isinstance(item, dict)
                    else item
                    for item in cached_visual_rank
                ]

                logger.info(
                    "O Bardo 4.17D COARSE CACHE HIT | "
                    f"video_id={candidate.get('video_id')} | "
                    f"frames={len(visual_rank)}"
                )

                _obardo_t_frames_done = (
                    __import__(
                        "time"
                    ).perf_counter()
                )

            else:
                sampled = extract_preview_sample_frames(
                    preview["filepath"],
                    video_id=candidate["video_id"],
                    interval_seconds=coarse_interval_seconds,
                    max_frames=60,
                )

                _obardo_t_frames_done = (
                    __import__(
                        "time"
                    ).perf_counter()
                )

                visual_rank = rank_scene_frames_by_text(
                    sampled["samples"],
                    visual_query,
                    top_k=5,
                    visual_plan=visual_plan,
                )

                coarse_cache[
                    coarse_cache_key
                ] = {
                    "sampled": {
                        **sampled,
                        "samples": [
                            dict(item)
                            if isinstance(item, dict)
                            else item
                            for item in (
                                sampled.get(
                                    "samples"
                                )
                                or []
                            )
                        ],
                    },
                    "visual_rank": [
                        dict(item)
                        if isinstance(item, dict)
                        else item
                        for item in (
                            visual_rank
                            or []
                        )
                    ],
                }

                logger.info(
                    "O Bardo 4.17D COARSE CACHE ADD | "
                    f"video_id={candidate.get('video_id')} | "
                    f"frames={len(visual_rank or [])}"
                )

            _obardo_t_rank_done = __import__("time").perf_counter()

            frames_elapsed = (
                _obardo_t_frames_done
                - _obardo_t_candidate
            )

            openclip_elapsed = (
                _obardo_t_rank_done
                - _obardo_t_frames_done
            )

            logger.info(
                "O Bardo TIMING | youtube_candidate="
                f"{candidate.get('video_id')} | "
                f"preview={preview_elapsed:.2f}s | "
                f"frames={frames_elapsed:.2f}s | "
                f"openclip={openclip_elapsed:.2f}s | "
                f"total={preview_elapsed + frames_elapsed + openclip_elapsed:.2f}s"
            )

            if not visual_rank:
                continue

            best = visual_rank[0]

            tournament.append(
                {
                    "candidate": candidate,
                    "preview": preview,
                    "coarse_best": best,
                    "coarse_top": visual_rank,
                }
            )

        except Exception as exc:
            tournament.append(
                {
                    "candidate": candidate,
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            )

    valid_tournament = [
        item
        for item in tournament
        if item.get("coarse_best")
    ]

    if not valid_tournament:
        raise RuntimeError(
            "No candidate produced a valid visual ranking."
        )

    logger.info(
        "O Bardo TIMING | youtube_coarse_openclip="
        f"{__import__('time').perf_counter() - _obardo_t_coarse:.2f}s"
    )

    _obardo_t_openai_coarse = __import__("time").perf_counter()

    # ---------------------------------------------------------
    # Optional OpenAI coarse editorial tournament.
    #
    # OpenCLIP remains the cheap/local first stage.
    # OpenAI only inspects the best frames of candidates that
    # already survived metadata, historical and visual filters.
    #
    # Crucially, rejected candidates are removed BEFORE the
    # expensive HQ download.
    # ---------------------------------------------------------
    try:
        from app.services import (
            obardo_openai_editorial,
        )

        openai_active = (
            obardo_openai_editorial
            .is_enabled()
        )

    except Exception:
        openai_active = False

    if (
        openai_active
        and skip_openai_coarse_for_test
    ):
        logger.info(
            "O Bardo TEST | OpenAI coarse bypassed; "
            "OpenCLIP tournament preserved."
        )

    if (
        openai_active
        and not skip_openai_coarse_for_test
    ):
        # -----------------------------------------------------
        # O Bardo 4.17C
        # Adaptive OpenAI coarse tournament.
        #
        # OpenCLIP still evaluates every valid candidate.
        # OpenAI first inspects only the three strongest local
        # semantic matches. The remaining candidates are only
        # reviewed when the first batch produces no survivor.
        #
        # Existing editorial thresholds remain unchanged.
        # -----------------------------------------------------

        valid_tournament.sort(
            key=lambda item: float(
                (
                    item.get("coarse_best")
                    or {}
                ).get(
                    "semantic_score",
                    0.0,
                )
                or 0.0
            ),
            reverse=True,
        )

        openai_coarse_batch_size = min(
            3,
            len(valid_tournament),
        )

        def _evaluate_openai_coarse_batch(
            tournament_items,
        ):
            batch_survivors = []

            for tournament_item in tournament_items:
                editorial_frames = []

                for frame in (
                    tournament_item.get(
                        "coarse_top"
                    )
                    or []
                )[:1]:
                    frame_path = _obardo_frame_path(
                        frame
                    )

                    if not frame_path:
                        continue

                    try:
                        evaluation = (
                            _obardo_evaluate_youtube_openai_cached(
                                editor=(
                                    obardo_openai_editorial
                                ),
                                frame_path=frame_path,
                                video_id=(
                                    tournament_item.get(
                                        "candidate",
                                        {},
                                    ).get(
                                        "video_id"
                                    )
                                ),
                                timestamp=(
                                    frame.get(
                                        "timestamp",
                                        0.0,
                                    )
                                ),
                                beat_text=(
                                    editorial_beat_text
                                ),
                                context_text=context_text,
                                visual_plan=(
                                    editorial_visual_plan
                                ),
                                logger=logger,
                            )
                            or {}
                        )

                    except Exception:
                        evaluation = {}

                    if not evaluation:
                        continue

                    evaluation = dict(
                        evaluation
                    )

                    evaluation[
                        "timestamp"
                    ] = frame.get(
                        "timestamp"
                    )

                    evaluation[
                        "openclip_score"
                    ] = frame.get(
                        "semantic_score"
                    )

                    editorial_frames.append(
                        evaluation
                    )

                if not editorial_frames:
                    # Fail-open:
                    # API/configuration failure must not discard
                    # an otherwise valid local candidate.
                    tournament_item[
                        "openai_coarse"
                    ] = None

                    batch_survivors.append(
                        tournament_item
                    )

                    continue

                def editorial_score(item):
                    score = (
                        float(
                            item.get(
                                "relevance_score",
                                0,
                            )
                            or 0
                        )
                        * 0.50
                        + float(
                            item.get(
                                "historical_score",
                                0,
                            )
                            or 0
                        )
                        * 0.30
                        + float(
                            item.get(
                                "documentary_value",
                                0,
                            )
                            or 0
                        )
                        * 0.20
                    )

                    if item.get(
                        "object_match"
                    ):
                        score += 15.0

                    if item.get(
                        "anachronism"
                    ):
                        score -= 100.0

                    if (
                        item.get(
                            "decision"
                        )
                        == "reject"
                    ):
                        score -= 100.0

                    return score

                best_editorial = max(
                    editorial_frames,
                    key=editorial_score,
                )

                tournament_item[
                    "openai_coarse"
                ] = best_editorial

                tournament_item[
                    "openai_coarse_score"
                ] = editorial_score(
                    best_editorial
                )

                decision_value = str(
                    best_editorial.get(
                        "decision",
                        "",
                    )
                    or ""
                ).strip().lower()

                relevance_value = int(
                    best_editorial.get(
                        "relevance_score",
                        0,
                    )
                    or 0
                )

                if (
                    decision_value == "reject"
                    or best_editorial.get(
                        "anachronism"
                    )
                ):
                    continue

                if (
                    obardo_openai_editorial
                    .editorial_mode()
                    == "assist"
                    and decision_value == "weak"
                    and relevance_value < 60
                ):
                    continue

                # ---------------------------------------------
                # Preserve 4.17A3 numeric sanity gate.
                # ---------------------------------------------
                historical_value = int(
                    best_editorial.get(
                        "historical_score",
                        0,
                    )
                    or 0
                )

                if (
                    decision_value == "accept"
                    and (
                        relevance_value < 25
                        or historical_value < 25
                    )
                ):
                    continue

                if (
                    obardo_openai_editorial
                    .editorial_mode()
                    == "strict"
                    and obardo_openai_editorial
                    .should_reject(
                        best_editorial
                    )
                ):
                    continue

                batch_survivors.append(
                    tournament_item
                )

            return batch_survivors

        first_openai_batch = (
            valid_tournament[
                :openai_coarse_batch_size
            ]
        )

        deferred_openai_batch = (
            valid_tournament[
                openai_coarse_batch_size:
            ]
        )

        logger.info(
            "O Bardo TURBO OPENAI COARSE BATCH | "
            f"first={len(first_openai_batch)} | "
            f"deferred={len(deferred_openai_batch)}"
        )

        editorial_survivors = (
            _evaluate_openai_coarse_batch(
                first_openai_batch
            )
        )

        if (
            not editorial_survivors
            and deferred_openai_batch
        ):
            logger.info(
                "O Bardo TURBO OPENAI COARSE EXPAND | "
                f"candidates={len(deferred_openai_batch)} | "
                "reason=first_batch_no_survivor"
            )

            editorial_survivors = (
                _evaluate_openai_coarse_batch(
                    deferred_openai_batch
                )
            )

        if editorial_survivors:
            valid_tournament = (
                editorial_survivors
            )

        else:
            raise RuntimeError(
                "All YouTube candidates were rejected "
                "by OpenAI before HQ download."
            )

    logger.info(
        "O Bardo TIMING | youtube_openai_coarse="
        f"{__import__('time').perf_counter() - _obardo_t_openai_coarse:.2f}s"
    )

    # Final candidate score:
    # OpenCLIP is always present; OpenAI becomes a secondary
    # semantic/editorial signal when enabled.
    def youtube_candidate_score(item):
        openclip_score = float(
            item[
                "coarse_best"
            ].get(
                "semantic_score",
                0.0,
            )
            or 0.0
        )

        openai_score = item.get(
            "openai_coarse_score"
        )

        if openai_score is None:
            return openclip_score

        return (
            openclip_score
            + float(
                openai_score
            ) / 100.0
        )

    valid_tournament.sort(
        key=youtube_candidate_score,
        reverse=True,
    )

    # All surviving candidates already passed HQ preflight.
    winner = valid_tournament[0]

    candidate = winner["candidate"]
    preview = winner["preview"]
    coarse_best = winner["coarse_best"]

    # ---------------------------------------------------------
    # O Bardo Turbo Fast Accept v1
    #
    # The winning YouTube candidate may already have received
    # a strong OpenAI editorial approval during the coarse
    # tournament. In that case, running the slow local VLM and
    # paying for another fine OpenAI review adds little value.
    #
    # Strict thresholds keep this optimization conservative.
    # ---------------------------------------------------------
    strong_coarse_evaluation = (
        winner.get("openai_coarse")
        if isinstance(
            winner.get("openai_coarse"),
            dict,
        )
        else None
    )

    strong_coarse_accept = False

    if strong_coarse_evaluation:
        strong_coarse_decision = str(
            strong_coarse_evaluation.get(
                "decision",
                "",
            )
            or ""
        ).strip().lower()

        strong_coarse_relevance = int(
            strong_coarse_evaluation.get(
                "relevance_score",
                0,
            )
            or 0
        )

        strong_coarse_historical = int(
            strong_coarse_evaluation.get(
                "historical_score",
                0,
            )
            or 0
        )

        strong_coarse_object_match = (
            strong_coarse_evaluation.get(
                "object_match"
            )
            is True
        )

        strong_coarse_anachronism = bool(
            strong_coarse_evaluation.get(
                "anachronism"
            )
        )

        strong_coarse_accept = bool(
            strong_coarse_decision == "accept"
            and strong_coarse_relevance >= 85
            and strong_coarse_historical >= 70
            and strong_coarse_object_match
            and not strong_coarse_anachronism
        )

    if strong_coarse_accept:
        logger.info(
            "O Bardo TURBO FAST ACCEPT | "
            f"video_id={candidate.get('video_id')} | "
            f"relevance={strong_coarse_relevance} | "
            f"historical={strong_coarse_historical} | "
            "skipping local VLM and duplicate OpenAI fine review"
        )

    hq_preflight = (
        candidate.get(
            "_obardo_hq_preflight"
        )
        or {}
    )

    if not hq_preflight.get(
        "accepted"
    ):
        raise RuntimeError(
            "Selected visual winner has no valid "
            "cached HQ preflight metadata."
        )

    coarse_timestamp = float(
        coarse_best["timestamp"]
    )

    _obardo_t_fine = __import__("time").perf_counter()

    # ---------------------------------------------------------
    # 3. Fine visual refinement
    # ---------------------------------------------------------

    fine = extract_preview_window_frames(
        preview["filepath"],
        video_id=candidate["video_id"],
        center_seconds=coarse_timestamp,
        radius_seconds=fine_radius_seconds,
        interval_seconds=fine_interval_seconds,
        max_frames=30,
    )

    fine_rank = rank_scene_frames_by_text(
        fine["samples"],
        visual_query,
        top_k=10,
        visual_plan=visual_plan,
    )

    if not fine_rank:
        raise RuntimeError(
            "Fine visual ranking returned no results."
        )

    fine_best = fine_rank[0]

    logger.info(
        "O Bardo TIMING | youtube_fine_openclip="
        f"{__import__('time').perf_counter() - _obardo_t_fine:.2f}s"
    )

    # ---------------------------------------------------------
    # O Bardo local vision gate
    #
    # Conservative v1:
    # - clear local ACCEPT skips the paid OpenAI fine review;
    # - uncertain/reject/error falls through to OpenAI;
    # - local vision must never break video generation.
    # ---------------------------------------------------------
    youtube_local_vision_evaluation = None
    youtube_local_vision_accept = False
    action_sensitive_local_vision = False

    if isinstance(visual_plan, dict):
        action_sensitive_local_vision = bool(
            visual_plan.get(
                "human_action_required"
            )
            or str(
                visual_plan.get(
                    "action_priority"
                )
                or ""
            ).strip().lower()
            == "high"
        )

    if action_sensitive_local_vision:
        logger.info(
            "O Bardo V2.3.6 ACTION VLM | "
            "local vision forced before OpenAI fine"
        )

    try:
        if strong_coarse_accept:
            youtube_local_vision_evaluation = {
                "executed": False,
                "decision": "skipped",
                "reason": (
                    "strong_openai_coarse_accept"
                ),
            }

        elif (
            openai_active
            and not action_sensitive_local_vision
        ):
            # -------------------------------------------------
            # O Bardo 4.17A
            # OpenAI-first editorial path.
            #
            # When OpenAI is already active, do not spend
            # 20-30+ seconds running the local Qwen/VLM before
            # the fine OpenAI review.
            #
            # The local vision model remains available when
            # OpenAI is disabled or unavailable.
            # -------------------------------------------------
            youtube_local_vision_evaluation = {
                "executed": False,
                "decision": "skipped",
                "reason": (
                    "openai_editorial_active"
                ),
            }

        from app.services import obardo_local_vision

        local_frame_path = _obardo_frame_path(fine_best)

        if (
            not strong_coarse_accept
            and local_frame_path
            and (
                not openai_active
                or action_sensitive_local_vision
            )
        ):
            local_target_subject = str(
                beat_text
                or ""
            ).strip()

            if isinstance(visual_plan, dict):
                primary_queries = (
                    visual_plan.get("primary_queries")
                    or []
                )

                if (
                    isinstance(primary_queries, list)
                    and primary_queries
                    and str(primary_queries[0]).strip()
                ):
                    local_target_subject = str(
                        primary_queries[0]
                    ).strip()

            if (
                action_sensitive_local_vision
                and isinstance(visual_plan, dict)
            ):
                local_visual_requirements = []

                for local_key in (
                    "subject",
                    "subject_identity",
                    "action",
                    "environment",
                ):
                    local_value = visual_plan.get(
                        local_key
                    )

                    if isinstance(
                        local_value,
                        (list, tuple, set),
                    ):
                        for local_item in local_value:
                            local_item_text = str(
                                local_item
                            ).strip()

                            if local_item_text:
                                local_visual_requirements.append(
                                    local_item_text
                                )

                    else:
                        local_value_text = str(
                            local_value
                            or ""
                        ).strip()

                        if local_value_text:
                            local_visual_requirements.append(
                                local_value_text
                            )

                if local_visual_requirements:
                    local_target_subject = (
                        "Required visual scene: "
                        + "; ".join(
                            local_visual_requirements
                        )
                        + ". The requested human action must "
                        + "be visibly occurring. Do not accept "
                        + "a scene merely because the historical "
                        + "subject is correct."
                    )

                    logger.info(
                        "O Bardo V2.3.6 VLM TARGET | {}",
                        local_target_subject,
                    )
            youtube_local_vision_evaluation = (
                obardo_local_vision
                .evaluate_local_vision_frame(
                    local_frame_path,
                    target_subject=local_target_subject,
                    context=context_text,
                )
            )

            local_decision = str(
                youtube_local_vision_evaluation.get(
                    "decision",
                    "uncertain",
                )
                or "uncertain"
            ).strip().lower()

            local_object_match = (
                youtube_local_vision_evaluation.get(
                    "object_match"
                )
            )

            local_confidence = int(
                youtube_local_vision_evaluation.get(
                    "confidence",
                    0,
                )
                or 0
            )

            youtube_local_vision_accept = bool(
                local_decision == "accept"
                and local_object_match is True
                and local_confidence >= 70
            )

            logger.info(
                "O Bardo LOCAL VISION | "
                f"decision={local_decision} | "
                f"object_match={local_object_match} | "
                f"confidence={local_confidence} | "
                f"accept={youtube_local_vision_accept} | "
                f"elapsed="
                f"{youtube_local_vision_evaluation.get('elapsed_seconds')}s"
            )

    except Exception as local_vision_exc:
        youtube_local_vision_evaluation = {
            "executed": False,
            "decision": "uncertain",
            "error": (
                f"{type(local_vision_exc).__name__}: "
                f"{local_vision_exc}"
            ),
        }

        youtube_local_vision_accept = False

        logger.warning(
            "O Bardo LOCAL VISION ERROR | "
            f"{type(local_vision_exc).__name__}: "
            f"{local_vision_exc}"
        )

    _obardo_t_openai_fine = __import__("time").perf_counter()

    youtube_openai_evaluation = (
        strong_coarse_evaluation
        if strong_coarse_accept
        else None
    )

    try:
        from app.services import (
            obardo_openai_editorial,
        )

        if (
            obardo_openai_editorial.is_enabled()
            and (
                not youtube_local_vision_accept
                or action_sensitive_local_vision
            )
            and not strong_coarse_accept
        ):
            evaluated_frames = []

            for frame in fine_rank[:1]:
                frame_path = _obardo_frame_path(
                    frame
                )

                if not frame_path:
                    continue

                evaluation = (
                    _obardo_evaluate_youtube_openai_cached(
                        editor=(
                            obardo_openai_editorial
                        ),
                        frame_path=frame_path,
                        video_id=(
                            candidate.get(
                                "video_id"
                            )
                        ),
                        timestamp=(
                            frame.get(
                                "timestamp",
                                0.0,
                            )
                        ),
                        beat_text=(
                            editorial_beat_text
                        ),
                        context_text=context_text,
                        visual_plan=(
                            editorial_visual_plan
                        ),
                        logger=logger,
                    )
                    or {}
                )

                if not evaluation:
                    continue

                evaluation = dict(
                    evaluation
                )

                evaluation[
                    "timestamp"
                ] = frame.get(
                    "timestamp"
                )

                evaluation[
                    "openclip_score"
                ] = frame.get(
                    "semantic_score"
                )

                evaluated_frames.append(
                    evaluation
                )

            if evaluated_frames:
                def yt_editorial_score(item):
                    score = (
                        float(
                            item.get(
                                "relevance_score",
                                0,
                            )
                            or 0
                        )
                        * 0.50
                        + float(
                            item.get(
                                "historical_score",
                                0,
                            )
                            or 0
                        )
                        * 0.30
                        + float(
                            item.get(
                                "documentary_value",
                                0,
                            )
                            or 0
                        )
                        * 0.20
                    )

                    if item.get(
                        "object_match"
                    ):
                        score += 15.0

                    if (
                        item.get(
                            "anachronism"
                        )
                        or item.get(
                            "decision"
                        )
                        == "reject"
                    ):
                        score -= 100.0

                    return score

                youtube_openai_evaluation = max(
                    evaluated_frames,
                    key=yt_editorial_score,
                )

                selected_timestamp = (
                    youtube_openai_evaluation.get(
                        "timestamp"
                    )
                )

                if selected_timestamp is not None:
                    matching_frame = min(
                        fine_rank,
                        key=lambda item: abs(
                            float(
                                item.get(
                                    "timestamp",
                                    0.0,
                                )
                                or 0.0
                            )
                            - float(
                                selected_timestamp
                            )
                        ),
                    )

                    fine_best = (
                        matching_frame
                    )

    except Exception:
        youtube_openai_evaluation = None

    logger.info(
        "O Bardo TIMING | youtube_openai_fine="
        f"{__import__('time').perf_counter() - _obardo_t_openai_fine:.2f}s"
    )

    # Final OpenAI safety gate before HQ.
    if youtube_openai_evaluation:
        final_decision = str(
            youtube_openai_evaluation.get(
                "decision",
                "",
            )
            or ""
        ).strip().lower()

        final_relevance = int(
            youtube_openai_evaluation.get(
                "relevance_score",
                0,
            )
            or 0
        )

        final_anachronism = bool(
            youtube_openai_evaluation.get(
                "anachronism"
            )
        )

        try:
            final_editorial_mode = (
                obardo_openai_editorial
                .editorial_mode()
            )
        except Exception:
            final_editorial_mode = "assist"

        final_anachronism_reject = (
            final_anachronism
            and (
                final_editorial_mode == "strict"
                or final_decision == "reject"
                or final_relevance < 60
            )
        )

        final_historical = int(
            youtube_openai_evaluation.get(
                "historical_score",
                0,
            )
            or 0
        )

        # Documentary tolerance:
        # a historically correct and strongly relevant scene
        # must not be discarded only because the composition
        # is not ideal (crop, trees, tourists, partial view).
        final_object_match = bool(
            youtube_openai_evaluation.get(
                "object_match"
            )
        )

        strong_documentary_match = (
            not final_anachronism
            and (
                final_object_match
                or (
                    final_relevance >= 70
                    and final_historical >= 70
                )
            )
        )

        logger.info(
            "O Bardo FINAL GATE | "
            f"mode={final_editorial_mode} | "
            f"decision={final_decision} | "
            f"relevance={final_relevance} | "
            f"historical={final_historical} | "
            f"object_match={final_object_match} | "
            f"anachronism={final_anachronism} | "
            f"strong_match={strong_documentary_match}"
        )

        # -----------------------------------------------------
        # O Bardo V2.3.11
        # Final editorial score normalization.
        #
        # Some editorial responses use a 0-10 scale while older
        # gates expect 0-100. Normalize low-range scores before
        # applying numeric sanity thresholds.
        #
        # A clear object match with no anachronism must not be
        # discarded only because of score-scale mismatch.
        # -----------------------------------------------------

        final_relevance_normalized = (
            final_relevance * 10
            if 0 <= final_relevance <= 10
            else final_relevance
        )

        final_historical_normalized = (
            final_historical * 10
            if 0 <= final_historical <= 10
            else final_historical
        )

        final_numeric_sanity_reject = (
            final_decision == "accept"
            and not strong_documentary_match
            and (
                final_relevance_normalized < 25
                or final_historical_normalized < 25
            )
        )

        logger.info(
            "O Bardo V2.3.11 FINAL SCORE SCALE | "
            f"relevance_raw={final_relevance} | "
            f"relevance_norm={final_relevance_normalized} | "
            f"historical_raw={final_historical} | "
            f"historical_norm={final_historical_normalized} | "
            f"numeric_reject={final_numeric_sanity_reject}"
        )

        if (
            final_anachronism_reject
            or final_numeric_sanity_reject
            or (
                final_decision == "reject"
                and (
                    final_editorial_mode == "strict"
                    or not strong_documentary_match
                )
            )
            or (
                final_decision == "weak"
                and final_relevance < 60
            )
        ):
            current_retry_depth = max(
                int(
                    _hq_candidate_retry_depth
                    or 0
                ),
                0,
            )

            if current_retry_depth < 2:
                retry_avoid_media_keys = list(
                    avoid_media_keys
                    or []
                )

                rejected_video_id = str(
                    candidate.get(
                        "video_id"
                    )
                    or ""
                ).strip()

                rejected_media_key = (
                    "youtube:"
                    + rejected_video_id
                )

                if (
                    rejected_video_id
                    and rejected_media_key
                    not in retry_avoid_media_keys
                ):
                    retry_avoid_media_keys.append(
                        rejected_media_key
                    )

                next_retry_depth = (
                    current_retry_depth + 1
                )

                logger.warning(
                    "O Bardo 4.17F EDITORIAL CANDIDATE FALLBACK | "
                    f"rejected_video_id={rejected_video_id!r} | "
                    f"rejected_rank={current_retry_depth + 1} | "
                    f"next_rank={next_retry_depth + 1} | "
                    f"decision={final_decision} | "
                    f"relevance={final_relevance} | "
                    f"historical={final_historical}"
                )

                return resolve_video_for_beat(
                    beat_text,
                    context_text=context_text,
                    visual_plan=visual_plan,
                    search_query=search_query,
                    required_terms=required_terms,
                    search_results=search_results,
                    candidate_count=candidate_count,
                    coarse_interval_seconds=(
                        coarse_interval_seconds
                    ),
                    fine_radius_seconds=(
                        fine_radius_seconds
                    ),
                    fine_interval_seconds=(
                        fine_interval_seconds
                    ),
                    clip_duration_seconds=(
                        clip_duration_seconds
                    ),
                    max_preview_height=(
                        max_preview_height
                    ),
                    max_hq_height=max_hq_height,
                    output_name=output_name,
                    cleanup_hq_source=(
                        cleanup_hq_source
                    ),
                    avoid_media_keys=(
                        retry_avoid_media_keys
                    ),
                    preflight_only=preflight_only,
                    skip_openai_coarse_for_test=skip_openai_coarse_for_test,
                    _hq_candidate_retry_depth=(
                        next_retry_depth
                    ),
                )

            raise RuntimeError(
                "Selected YouTube scene was rejected "
                "by final OpenAI editorial review after "
                "three fully evaluated candidates: "
                + str(
                    youtube_openai_evaluation.get(
                        "reason"
                    )
                    or "candidate rejected"
                )
            )


    # ---------------------------------------------------------
    # O Bardo V2.3.9
    # Fine OpenAI rejection must block this YouTube candidate.
    # ---------------------------------------------------------
    if isinstance(
        youtube_openai_evaluation,
        dict,
    ):
        fine_openai_decision = str(
            youtube_openai_evaluation.get(
                "decision"
            )
            or ""
        ).strip().lower()

        fine_openai_anachronism = bool(
            youtube_openai_evaluation.get(
                "anachronism"
            )
            is True
        )

        if (
            fine_openai_decision == "reject"
            or fine_openai_anachronism
        ):
            logger.warning(
                "O Bardo V2.3.9 OPENAI FINE REJECT | "
                f"video_id={candidate.get('video_id')} | "
                f"timestamp={fine_best.get('timestamp')} | "
                f"decision={fine_openai_decision} | "
                f"anachronism={fine_openai_anachronism}"
            )

            # -------------------------------------------------
            # O Bardo V2.3.10
            # Fine editorial rejection retries the resolver
            # while excluding the rejected YouTube candidate.
            #
            # This preserves the existing tournament/search
            # architecture instead of immediately falling back
            # to stock footage.
            # -------------------------------------------------

            rejected_video_id = str(
                candidate.get(
                    "video_id"
                )
                or ""
            ).strip()

            rejected_media_key = (
                "youtube:"
                + rejected_video_id
                if rejected_video_id
                else ""
            )

            retry_avoid_media_keys = list(
                avoid_media_keys
                or []
            )

            if (
                rejected_media_key
                and rejected_media_key
                not in retry_avoid_media_keys
            ):
                retry_avoid_media_keys.append(
                    rejected_media_key
                )

            current_retry_depth = int(
                _hq_candidate_retry_depth
                or 0
            )

            next_retry_depth = (
                current_retry_depth
                + 1
            )

            max_retry_depth = max(
                1,
                int(candidate_count),
            )

            if (
                rejected_video_id
                and next_retry_depth
                < max_retry_depth
            ):
                logger.warning(
                    "O Bardo V2.3.10 FINE RETRY | "
                    f"rejected={rejected_video_id} | "
                    f"failed_rank={current_retry_depth + 1} | "
                    f"next_rank={next_retry_depth + 1} | "
                    f"avoid={retry_avoid_media_keys}"
                )

                return resolve_video_for_beat(
                    beat_text,
                    context_text=context_text,
                    visual_plan=visual_plan,
                    search_query=search_query,
                    required_terms=required_terms,
                    search_results=search_results,
                    candidate_count=candidate_count,
                    coarse_interval_seconds=(
                        coarse_interval_seconds
                    ),
                    fine_radius_seconds=(
                        fine_radius_seconds
                    ),
                    fine_interval_seconds=(
                        fine_interval_seconds
                    ),
                    clip_duration_seconds=(
                        clip_duration_seconds
                    ),
                    max_preview_height=(
                        max_preview_height
                    ),
                    max_hq_height=max_hq_height,
                    output_name=output_name,
                    cleanup_hq_source=(
                        cleanup_hq_source
                    ),
                    avoid_media_keys=(
                        retry_avoid_media_keys
                    ),
                    preflight_only=preflight_only,
                    skip_openai_coarse_for_test=(
                        skip_openai_coarse_for_test
                    ),
                    _hq_candidate_retry_depth=(
                        next_retry_depth
                    ),
                )

            raise RuntimeError(
                "All YouTube candidate retries were "
                "rejected by OpenAI fine editorial review."
            )

    best_timestamp = float(
        fine_best["timestamp"]
    )

    # Center the final clip around the best fine frame.
    clip_duration_seconds = max(
        float(clip_duration_seconds),
        2.0,
    )

    clip_start = max(
        0.0,
        best_timestamp
        - (clip_duration_seconds / 2.0),
    )

    # ---------------------------------------------------------
    # 4. HQ source
    # ---------------------------------------------------------

    selected_hq_format = (
        hq_preflight.get(
            "selected_format",
            {},
        )
    )

    hq_source = None
    hq_path = None

    # When the remote section downloader succeeds, the
    # resulting local file already begins at the selected
    # visual window, so normalization must start at 0.
    hq_source_clip_start = clip_start
    hq_partial = False

    logger.info(
        "O Bardo TIMING | youtube_total_pre_hq="
        f"{__import__('time').perf_counter() - _obardo_t_youtube_total:.2f}s"
    )

    partial_hq_strategy_key = (
        "youtube_download_sections"
    )

    try:
        if (
            partial_hq_strategy_key
            in _OBARDO_YOUTUBE_PARTIAL_HQ_UNRELIABLE
        ):
            logger.info(
                "O Bardo TURBO PARTIAL HQ STRATEGY SKIP | "
                f"strategy={partial_hq_strategy_key} | "
                "reason=previous_timeout"
            )

            raise RuntimeError(
                "Partial HQ strategy skipped after "
                "previous process-local timeout."
            )

        logger.info(
            "O Bardo YouTube: trying partial HQ | "
            f"start={clip_start:.2f}s | "
            f"duration={clip_duration_seconds:.2f}s"
        )

        hq_source = download_hq_clip_section(
            candidate["url"],
            start_seconds=clip_start,
            duration_seconds=clip_duration_seconds,
            max_height=min(
                int(max_hq_height),
                720,
            ),
            timeout_seconds=25,
        )

        hq_path = Path(
            hq_source["filepath"]
        )

        if (
            not hq_path.exists()
            or hq_path.stat().st_size <= 0
        ):
            raise RuntimeError(
                "Partial HQ downloader returned "
                "an invalid file."
            )

        hq_source_clip_start = 0.0
        hq_partial = True

        logger.info(
            "O Bardo YouTube: partial HQ ready | "
            f"size_mb={hq_source.get('file_size_mb')} | "
            f"file={hq_path}"
        )

    except Exception as partial_exc:
        partial_error_text = str(
            partial_exc
            or ""
        ).lower()

        partial_timeout_failure = (
            "timed out" in partial_error_text
            or "timeout" in partial_error_text
        )

        partial_cached_skip = (
            "strategy skipped after previous "
            "process-local timeout"
            in partial_error_text
        )

        if (
            partial_timeout_failure
            and not partial_cached_skip
        ):
            _OBARDO_YOUTUBE_PARTIAL_HQ_UNRELIABLE.add(
                partial_hq_strategy_key
            )

            logger.warning(
                "O Bardo TURBO PARTIAL HQ STRATEGY CACHE ADD | "
                f"strategy={partial_hq_strategy_key} | "
                f"cached_count="
                f"{len(_OBARDO_YOUTUBE_PARTIAL_HQ_UNRELIABLE)}"
            )

        if partial_cached_skip:
            logger.info(
                "O Bardo YouTube: partial HQ bypassed; "
                "trying full HQ source directly."
            )
        else:
            logger.warning(
                "O Bardo YouTube: partial HQ failed: "
                f"{type(partial_exc).__name__}: "
                f"{partial_exc}. Trying full HQ source fallback."
            )

        # -----------------------------------------------------
        # O Bardo 4.16E
        # Full HQ source fallback.
        #
        # Some YouTube formats do not serve remote ranges
        # efficiently through yt-dlp --download-sections.
        #
        # In that case:
        #   1. download only the preflight-selected HQ format;
        #   2. keep the semantic timestamp already selected;
        #   3. cut the exact final window locally;
        #   4. delete the temporary HQ source in the existing
        #      cleanup block below.
        #
        # Low-resolution preview is still NEVER accepted as
        # final production media.
        # -----------------------------------------------------
        try:
            preferred_format_id = None

            if isinstance(
                selected_hq_format,
                dict,
            ):
                preferred_format_id = (
                    selected_hq_format.get(
                        "format_id"
                    )
                    or selected_hq_format.get(
                        "id"
                    )
                )

            logger.info(
                "O Bardo YouTube: trying full HQ "
                "source fallback | "
                f"format_id={preferred_format_id!r}"
            )

            hq_source = download_hq_source_video(
                candidate["url"],
                max_height=min(
                    int(max_hq_height),
                    720,
                ),
                preferred_format_id=(
                    str(preferred_format_id)
                    if preferred_format_id
                    else None
                ),
            )

            hq_path = Path(
                hq_source["filepath"]
            )

            if (
                not hq_path.exists()
                or hq_path.stat().st_size <= 0
            ):
                raise RuntimeError(
                    "Full HQ source fallback returned "
                    "an invalid file."
                )

            # Full source retains the original timeline.
            hq_source_clip_start = clip_start
            hq_partial = False

            logger.info(
                "O Bardo YouTube: full HQ source ready | "
                f"size_mb={hq_source.get('file_size_mb')} | "
                f"download_seconds="
                f"{hq_source.get('download_seconds')} | "
                f"file={hq_path}"
            )

        except Exception as full_exc:
            failed_video_id = str(
                candidate.get("video_id")
                or ""
            ).strip()

            if failed_video_id:
                _OBARDO_YOUTUBE_HQ_FAILED_IDS.add(
                    failed_video_id
                )

                logger.warning(
                    "O Bardo TURBO HQ CACHE ADD | "
                    f"video_id={failed_video_id} | "
                    f"failed_count="
                    f"{len(_OBARDO_YOUTUBE_HQ_FAILED_IDS)}"
                )

            logger.warning(
                "O Bardo YouTube: full HQ fallback failed: "
                f"{type(full_exc).__name__}: "
                f"{full_exc}. Rejecting low-resolution "
                "preview fallback."
            )

            # -------------------------------------------------
            # O Bardo 4.17F
            # HQ candidate fallback.
            #
            # The current candidate already passed:
            #   metadata/context ranking
            #   HQ preflight
            #   coarse OpenCLIP
            #   optional coarse OpenAI
            #   fine OpenCLIP
            #   local/OpenAI editorial gates
            #
            # Only the production HQ acquisition failed.
            #
            # Retry the resolver while excluding this failed
            # video. The replacement candidate therefore passes
            # through the COMPLETE normal pipeline instead of
            # being downloaded directly from a coarse timestamp.
            #
            # Depth:
            #   0 = candidate #1
            #   1 = candidate #2
            #   2 = candidate #3
            #
            # After candidate #3, preserve the original hard
            # failure. Low-resolution preview is never accepted.
            # -------------------------------------------------

            current_retry_depth = max(
                int(
                    _hq_candidate_retry_depth
                    or 0
                ),
                0,
            )

            if current_retry_depth < 2:
                retry_avoid_media_keys = list(
                    avoid_media_keys
                    or []
                )

                failed_media_key = (
                    "youtube:"
                    + str(
                        failed_video_id
                        or candidate.get(
                            "video_id"
                        )
                        or ""
                    ).strip()
                )

                if (
                    failed_media_key
                    != "youtube:"
                    and failed_media_key
                    not in retry_avoid_media_keys
                ):
                    retry_avoid_media_keys.append(
                        failed_media_key
                    )

                next_retry_depth = (
                    current_retry_depth + 1
                )

                logger.warning(
                    "O Bardo 4.17F HQ CANDIDATE FALLBACK | "
                    f"failed_video_id={failed_video_id!r} | "
                    f"failed_rank={current_retry_depth + 1} | "
                    f"next_rank={next_retry_depth + 1} | "
                    f"avoid={retry_avoid_media_keys}"
                )

                return resolve_video_for_beat(
                    beat_text,
                    context_text=context_text,
                    visual_plan=visual_plan,
                    search_query=search_query,
                    required_terms=required_terms,
                    search_results=search_results,
                    candidate_count=candidate_count,
                    coarse_interval_seconds=(
                        coarse_interval_seconds
                    ),
                    fine_radius_seconds=(
                        fine_radius_seconds
                    ),
                    fine_interval_seconds=(
                        fine_interval_seconds
                    ),
                    clip_duration_seconds=(
                        clip_duration_seconds
                    ),
                    max_preview_height=(
                        max_preview_height
                    ),
                    max_hq_height=max_hq_height,
                    output_name=output_name,
                    cleanup_hq_source=(
                        cleanup_hq_source
                    ),
                    avoid_media_keys=(
                        retry_avoid_media_keys
                    ),
                    preflight_only=preflight_only,
                    _hq_candidate_retry_depth=(
                        next_retry_depth
                    ),
                )

            logger.error(
                "O Bardo 4.17F HQ CANDIDATES EXHAUSTED | "
                f"attempts={current_retry_depth + 1} | "
                f"last_video_id={failed_video_id!r}"
            )

            raise RuntimeError(
                "YouTube HQ media unavailable after "
                "three fully evaluated candidates. "
                "Partial and full HQ acquisition failed. "
                "Low-resolution cached preview is not allowed "
                "as final O Bardo media."
            ) from full_exc
    # ---------------------------------------------------------
    # 5. Final normalized clip
    # ---------------------------------------------------------

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "clips"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_name:
        filename = str(output_name)
    else:
        safe_beat = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            beat_text,
        ).strip("_")

        safe_beat = (
            safe_beat[:60]
            or "obardo_clip"
        )

        filename = (
            f"{candidate['video_id']}-"
            f"{safe_beat}-final.mp4"
        )

    output_path = (
        output_dir / filename
    )

    final_clip = None

    try:
        final_clip = extract_hq_clip(
            hq_path,
            output_path,
            start_seconds=hq_source_clip_start,
            duration_seconds=clip_duration_seconds,
            width=1920,
            height=1080,
            fps=30,
            keep_source_audio=False,
        )

    finally:
        if (
            cleanup_hq_source
            and bool(
                hq_source.get(
                    "temporary",
                    True,
                )
            )
            and hq_path.exists()
        ):
            try:
                hq_path.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------
    # 6. Result metadata
    # ---------------------------------------------------------

    return {
        "type": "resolved_video_beat",
        "beat_text": beat_text,
        "search_query": query,
        "identity": identity,
        "required_terms": effective_required_terms,
        "visual_query": visual_query,
        "video_id": candidate["video_id"],
        "title": candidate["title"],
        "url": candidate["url"],
        "source_url": candidate["url"],
        "best_timestamp": best_timestamp,
        "metadata_score": candidate.get(
            "metadata_score"
        ),
        "coarse_timestamp": coarse_timestamp,
        "coarse_score": coarse_best[
            "semantic_score"
        ],
        "fine_timestamp": best_timestamp,
        "fine_score": fine_best[
            "semantic_score"
        ],
        "fine_multiprompt_score": fine_best.get(
            "multiprompt_score"
        ),
        "fine_subject_score": fine_best.get(
            "subject_score"
        ),
        "fine_action_score": fine_best.get(
            "action_score"
        ),
        "fine_environment_score": fine_best.get(
            "environment_score"
        ),
        "fine_historical_score": fine_best.get(
            "historical_score"
        ),
        "openai_editorial": (
            youtube_openai_evaluation
        ),
        "local_vision": (
            youtube_local_vision_evaluation
        ),
        "clip_start_seconds": clip_start,
        "clip_duration_seconds": (
            clip_duration_seconds
        ),
        "final_clip": final_clip,
        "hq_source_deleted": (
            cleanup_hq_source
            and not hq_path.exists()
        ),
        "tournament": [
            {
                "video_id": item[
                    "candidate"
                ].get("video_id"),
                "title": item[
                    "candidate"
                ].get("title"),
                "metadata_score": item[
                    "candidate"
                ].get("metadata_score"),
                "visual_score": (
                    item.get(
                        "coarse_best",
                        {},
                    ).get(
                        "semantic_score"
                    )
                ),
                "error": item.get("error"),
            }
            for item in tournament
        ],
    }

def detect_historical_identity(
    beat_text: str,
) -> dict:
    """
    Detect historically specific subjects that should use an identity gate.
    """
    import re

    import unicodedata

    def normalize_identity_text(value: str) -> str:
        value = unicodedata.normalize(
            "NFKD",
            str(value or "").lower(),
        )

        value = "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

        return " ".join(
            value.strip().split()
        )

    text = normalize_identity_text(
        beat_text
    )
    entities = [
        {
            "canonical": "colosseum",
            "aliases": [
                "colosseum",
                "coliseum",
                "coliseu",
                "coliseu de roma",
            ],
            "query_term": "Colosseum Rome",
            "youtube_identity_terms": [
                "colosseum",
                "coliseum",
            ],
        },
        {
            "canonical": "strigil",
            "aliases": [
                "strigil",
                "strigilo",
                "strigilis",
                "estrigil",
                "estrigilo",
            ],
            "query_term": "Roman bronze strigil",
            "youtube_identity_terms": [
                "strigil",
                "strigils",
            ],
        },
        {
            "canonical": "pantheon",
            "aliases": [
                "pantheon",
                "pante?o",
                "panteao",
            ],
            "query_term": "Pantheon Rome",
            "youtube_identity_terms": [
                "pantheon",
            ],
        },
        {
            "canonical": "pompeii",
            "aliases": [
                "pompeii",
                "pompeia",
            ],
            "query_term": "Pompeii ancient Rome",
            "youtube_identity_terms": [
                "pompeii",
                "pompeii ruins",
            ],
        },
        {
            "canonical": "trajan column",
            "aliases": [
                "trajan's column",
                "trajan column",
                "coluna de trajano",
            ],
            "query_term": "Trajan Column Rome",
            "youtube_identity_terms": [
                "trajan column",
                "trajan's column",
            ],
        },
        {
            "canonical": "roman baths",
            "aliases": [
                "roman baths",
                "termas romanas",
                "termas",
                "bathhouse",
            ],
            "query_term": "Roman baths ancient Rome",
            "youtube_identity_terms": [
                "roman baths",
                "roman bath",
                "roman bathhouse",
                "roman bath house",
                "thermae",
                "baths of caracalla",
                "caracalla baths",
            ],
        },
    ]

    for entity in entities:
        for alias in entity["aliases"]:
            normalized_alias = normalize_identity_text(
                alias
            )

            if re.search(
                rf"(?<!\w){re.escape(normalized_alias)}(?!\w)",
                text,
            ):
                return {
                    "specific": True,
                    "canonical": entity["canonical"],
                    "required_terms": [
                        entity["canonical"]
                    ],
                    "youtube_identity_terms": list(
                        entity.get(
                            "youtube_identity_terms",
                            [entity["canonical"]],
                        )
                    ),
                    "query_term": entity["query_term"],
                    "matched_alias": normalized_alias,
                }

    return {
        "specific": False,
        "canonical": None,
        "required_terms": [],
        "youtube_identity_terms": [],
        "query_term": None,
        "matched_alias": None,
    }


def build_visual_search_query(
    beat_text: str,
    *,
    identity: dict | None = None,
) -> str:
    """
    Build an English visual-search query from a beat and optional identity.
    """
    beat_text = str(beat_text or "").strip()

    if not beat_text:
        raise ValueError("beat_text is required.")

    identity = identity or detect_historical_identity(
        beat_text
    )

    if identity.get("specific"):
        base = identity.get("query_term") or ""

        return (
            f"{base} historical reconstruction documentary "
            f"ancient visual"
        ).strip()

    # Generic beats must also become concrete English visual queries.
    visual_context = infer_historical_visual_context(
        beat_text
    )

    return str(
        visual_context.get("query")
        or beat_text
    ).strip()

def filter_youtube_candidates_by_identity_any(
    candidates: list[dict],
    *,
    identity_terms: list[str] | None = None,
) -> list[dict]:
    """
    Accept a YouTube candidate when ANY known identity alias
    appears in its title.

    Example Roman baths:
        roman baths
        roman bath
        thermae
        bathhouse
        baths
    """
    import re

    terms = [
        str(term).strip().lower()
        for term in (identity_terms or [])
        if str(term).strip()
    ]

    if not terms:
        return list(candidates)

    accepted = []

    for candidate in candidates:
        title = str(
            candidate.get("title") or ""
        ).lower()

        normalized = re.sub(
            r"\s+",
            " ",
            title,
        )

        matched = [
            term
            for term in terms
            if term in normalized
        ]

        if not matched:
            continue

        item = dict(candidate)
        item["identity_gate"] = True
        item["identity_terms_matched"] = matched

        accepted.append(item)

    return accepted

def decide_media_source_for_beat(
    beat_text: str,
    *,
    search_query: str | None = None,
    search_results: int = 20,
    minimum_video_candidates: int = 1,
    visual_plan: dict | None = None,
) -> dict:
    """
    Decide whether a visual beat should proceed through the external-video
    pipeline or fall back to historically specific still imagery.

    This is a routing decision only. It does not download previews or HQ media.
    """

    beat_text = str(beat_text or "").strip()

    if not beat_text:
        raise ValueError("beat_text is required.")

    identity = detect_historical_identity(
        beat_text
    )

    search_query = str(
        search_query or ""
    ).strip()

    director_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    director_queries = []

    for key in (
        "queries",
        "all_queries",
        "fallback_queries",
    ):
        values = director_plan.get(key)

        if not isinstance(values, list):
            continue

        for value in values:
            candidate_query = str(
                value or ""
            ).strip()

            if (
                candidate_query
                and candidate_query
                not in director_queries
            ):
                director_queries.append(
                    candidate_query
                )

    director_query = (
        director_queries[0]
        if director_queries
        else ""
    )

    query = (
        director_query
        or search_query
        or build_visual_search_query(
            beat_text,
            identity=identity,
        )
    )

    # ---------------------------------------------------------
    # O Bardo Historical Director V2.3.2
    # AI multi-query retrieval.
    #
    # Use multiple action-aware queries from the Historical
    # Visual Director instead of relying on a single generic
    # YouTube query.
    #
    # Search results are merged and deduplicated by video_id.
    # Existing downstream ranking remains authoritative.
    # ---------------------------------------------------------

    retrieval_queries = []

    if director_queries:
        director_seed_queries = (
            director_queries[:3]
        )

        historical_suffixes = (
            "historical reenactment",
            "historical reconstruction",
            "ancient Rome",
        )

        for index, base_query in enumerate(
            director_seed_queries
        ):
            suffix = historical_suffixes[
                min(
                    index,
                    len(historical_suffixes) - 1,
                )
            ]

            enriched_query = (
                f"{base_query} {suffix}"
            ).strip()

            if (
                enriched_query
                and enriched_query
                not in retrieval_queries
            ):
                retrieval_queries.append(
                    enriched_query
                )

    if not retrieval_queries:
        retrieval_queries = [
            query
        ]

    # Keep the primary query visible in routing/debug output.
    query = retrieval_queries[0]

    merged_candidates = []
    seen_video_ids = set()
    seen_urls = set()

    # Avoid exploding retrieval cost.
    # Each query receives a fraction of the requested budget,
    # with a useful minimum for semantic diversity.
    per_query_results = max(
        8,
        int(search_results),
    )

    for retrieval_query in retrieval_queries:
        query_candidates = (
            search_youtube_candidates(
                retrieval_query,
                max_results=per_query_results,
            )
            or []
        )

        for candidate in query_candidates:
            if not isinstance(
                candidate,
                dict,
            ):
                continue

            video_id = str(
                candidate.get("video_id")
                or ""
            ).strip()

            candidate_url = str(
                candidate.get("url")
                or ""
            ).strip()

            if (
                video_id
                and video_id in seen_video_ids
            ):
                continue

            if (
                not video_id
                and candidate_url
                and candidate_url in seen_urls
            ):
                continue

            if video_id:
                seen_video_ids.add(
                    video_id
                )

            if candidate_url:
                seen_urls.add(
                    candidate_url
                )

            enriched_candidate = dict(
                candidate
            )

            enriched_candidate[
                "director_retrieval_query"
            ] = retrieval_query

            merged_candidates.append(
                enriched_candidate
            )

    candidates = merged_candidates

    # ---------------------------------------------------------
    # O Bardo 4.16C2
    # Compact YouTube fallback for specific historical
    # human-action beats.
    #
    # Long planner queries can be semantically excellent but too
    # restrictive for YouTube retrieval. When such a query returns
    # nothing, retry once with a compact identity + action query.
    #
    # This affects retrieval only. Beat narration remains
    # authoritative for OpenCLIP/OpenAI editorial validation.
    # ---------------------------------------------------------
    if (
        not candidates
        and identity.get("specific")
    ):
        import unicodedata

        action_probe = unicodedata.normalize(
            "NFKD",
            f"{beat_text} {query}".lower(),
        )

        action_probe = "".join(
            char
            for char in action_probe
            if not unicodedata.combining(char)
        )

        action_probe = " ".join(
            action_probe.strip().split()
        )

        compact_action_hint = None

        if any(
            marker in action_probe
            for marker in (
                "crowd",
                "crowds",
                "spectator",
                "spectators",
                "audience",
                "people",
                "approach",
                "approached",
                "approaching",
                "arrive",
                "arrived",
                "arriving",
                "enter",
                "entered",
                "entering",
                "multidao",
                "espectador",
                "espectadores",
                "publico",
                "plateia",
                "aproximando",
                "chegando",
                "entrando",
                "pessoas",
            )
        ):
            compact_action_hint = (
                "Roman crowd reconstruction"
            )

        elif any(
            marker in action_probe
            for marker in (
                "gladiator",
                "gladiators",
                "fight",
                "fighting",
                "combat",
                "lutando",
                "combate",
            )
        ):
            compact_action_hint = (
                "Roman gladiator reconstruction"
            )

        elif any(
            marker in action_probe
            for marker in (
                "bather",
                "bathers",
                "bathing",
                "bath",
                "baths",
                "banhista",
                "banhistas",
                "banho",
                "termas",
            )
        ):
            compact_action_hint = (
                "Roman bathers reconstruction"
            )

        elif any(
            marker in action_probe
            for marker in (
                "merchant",
                "merchants",
                "vendor",
                "vendors",
                "market",
                "comerciante",
                "comerciantes",
                "vendedor",
                "vendedores",
                "mercado",
            )
        ):
            compact_action_hint = (
                "Roman market reconstruction"
            )

        elif any(
            marker in action_probe
            for marker in (
                "soldier",
                "soldiers",
                "march",
                "marched",
                "marching",
                "soldado",
                "soldados",
                "marchando",
            )
        ):
            compact_action_hint = (
                "Roman soldiers reconstruction"
            )

        if compact_action_hint:
            compact_identity = str(
                identity.get("canonical")
                or identity.get("query_term")
                or ""
            ).strip()

            if compact_identity:
                compact_identity = (
                    compact_identity[0].upper()
                    + compact_identity[1:]
                )

                compact_query = (
                    f"{compact_identity} "
                    f"{compact_action_hint}"
                ).strip()

                compact_candidates = (
                    search_youtube_candidates(
                        compact_query,
                        max_results=search_results,
                    )
                )

                if compact_candidates:
                    query = compact_query
                    candidates = compact_candidates

    ranked = rank_youtube_candidates(
        candidates,
        query=query,
    )

    ranked = refine_youtube_candidates_for_context(
        ranked,
        beat_text=beat_text,
        search_query=query,
        visual_plan=director_plan,
    )

    ranked = filter_candidates_by_historical_context(
        ranked,
        beat_text=beat_text,
        search_query=query,
    )

    ranked = prune_contextually_weak_candidates(
        ranked,
        beat_text=beat_text,
        minimum_context_score=8.0,
    )

    identity_terms = identity.get(
        "youtube_identity_terms"
    ) or []

    if identity.get("specific") and identity_terms:
        filtered = (
            filter_youtube_candidates_by_identity_any(
                ranked,
                identity_terms=identity_terms,
            )
        )
    else:
        filtered = ranked

    minimum_video_candidates = max(
        int(minimum_video_candidates),
        1,
    )

    # O Bardo editorial priority:
    #
    # Specific historical OBJECTS / PLACES / ARTIFACTS normally
    # prefer historically specific Wikimedia imagery.
    #
    # Exception: when the narration explicitly requires HUMAN ACTION
    # and identity-safe video candidates exist, motion footage should
    # be preferred. A static architectural/artifact image must not be
    # forced to represent actions such as crowds approaching, entering,
    # marching, bathing, trading or fighting.
    import unicodedata

    action_text = unicodedata.normalize(
        "NFKD",
        f"{beat_text} {query}".lower(),
    )

    action_text = "".join(
        char
        for char in action_text
        if not unicodedata.combining(char)
    )

    action_text = " ".join(
        action_text.strip().split()
    )

    human_action_required = any(
        marker in action_text
        for marker in (
            "crowd",
            "crowds",
            "spectator",
            "spectators",
            "audience",
            "people",
            "approach",
            "approached",
            "approaching",
            "arrive",
            "arrived",
            "arriving",
            "enter",
            "entered",
            "entering",
            "march",
            "marched",
            "marching",
            "fight",
            "fighting",
            "gladiator",
            "gladiators",
            "bather",
            "bathers",
            "bathing",
            "merchant",
            "merchants",
            "vendor",
            "vendors",
            "soldier",
            "soldiers",
            "multidao",
            "espectador",
            "espectadores",
            "publico",
            "plateia",
            "aproximando",
            "chegando",
            "entrando",
            "marchando",
            "lutando",
            "banhista",
            "banhistas",
            "comerciante",
            "comerciantes",
            "vendedor",
            "vendedores",
            "soldado",
            "soldados",
            "pessoas",
        )
    )

    if (
        identity.get("specific")
        and human_action_required
        and len(filtered) >= minimum_video_candidates
    ):
        recommended_media_type = "video"
        reason = (
            "specific_historical_identity_human_action_prefers_video"
        )

    elif identity.get("specific"):
        recommended_media_type = "historical_image"
        reason = "specific_historical_identity_prefers_image"

    elif len(filtered) >= minimum_video_candidates:
        recommended_media_type = "video"
        reason = "usable_video_candidates_found"

    else:
        recommended_media_type = "historical_image"
        reason = "no_identity_safe_video_candidates"

    return {
        "beat_text": beat_text,
        "identity": identity,
        "search_query": query,
        "recommended_media_type": recommended_media_type,
        "reason": reason,
        "candidate_count": len(filtered),
        "candidates": filtered,
    }

def infer_historical_visual_context(
    beat_text: str,
) -> dict:
    """
    Infer a concrete English visual-search context from Portuguese or English
    historical narration.

    This is a deterministic fallback for beats without a hard historical
    identity such as Colosseum, strigil, Pantheon, etc.
    """
    import unicodedata

    raw = str(beat_text or "").strip()

    normalized = unicodedata.normalize(
        "NFKD",
        raw.lower(),
    )

    normalized = "".join(
        char
        for char in normalized
        if not unicodedata.combining(char)
    )

    contexts = [
        {
            "name": "roman_street_market",
            "triggers": [
                "rua",
                "ruas",
                "comerciante",
                "comerciantes",
                "mercador",
                "mercadores",
                "mercado",
                "vendedor",
                "vendedores",
                "street",
                "merchant",
                "market",
            ],
            "query": (
                "ancient Rome street market merchants "
                "crowd 3D reconstruction"
            ),
        },
        {
            "name": "roman_forum",
            "triggers": [
                "forum",
                "forum romano",
                "roman forum",
            ],
            "query": (
                "Roman Forum ancient Rome "
                "3D historical reconstruction"
            ),
        },
        {
            "name": "roman_port",
            "triggers": [
                "porto",
                "portos",
                "porto romano",
                "navio",
                "navios",
                "harbor",
                "harbour",
                "port",
            ],
            "query": (
                "ancient Roman port harbor ships "
                "3D reconstruction"
            ),
        },
        {
            "name": "roman_house",
            "triggers": [
                "casa",
                "casas",
                "domus",
                "villa",
                "residencia",
                "residencias",
                "house",
                "home",
            ],
            "query": (
                "ancient Roman domus house interior "
                "3D reconstruction"
            ),
        },
        {
            "name": "roman_temple",
            "triggers": [
                "templo",
                "templos",
                "temple",
                "religiao",
                "religioso",
                "religiosa",
            ],
            "query": (
                "ancient Roman temple religious ritual "
                "3D reconstruction"
            ),
        },
        {
            "name": "roman_soldiers",
            "triggers": [
                "soldado",
                "soldados",
                "legionario",
                "legionarios",
                "legiao",
                "army",
                "soldier",
                "legionary",
            ],
            "query": (
                "ancient Roman legionaries soldiers "
                "historical reconstruction"
            ),
        },
        {
            "name": "roman_crowd",
            "triggers": [
                "multidao",
                "publico",
                "populacao",
                "crowd",
                "people",
            ],
            "query": (
                "ancient Rome crowd daily life "
                "historical reconstruction"
            ),
        },
    ]

    best = None
    best_matches = []

    for context in contexts:
        matches = [
            trigger
            for trigger in context["triggers"]
            if trigger in normalized
        ]

        if (
            best is None
            or len(matches) > len(best_matches)
        ):
            if matches:
                best = context
                best_matches = matches

    if best:
        return {
            "matched": True,
            "context": best["name"],
            "query": best["query"],
            "matched_terms": best_matches,
        }

    # Safe historical fallback. Do not send untranslated Portuguese
    # narration directly into YouTube search.
    roman_context = any(
        term in normalized
        for term in [
            "roma",
            "romano",
            "romana",
            "romanos",
            "romanas",
        ]
    )

    if roman_context:
        return {
            "matched": True,
            "context": "generic_ancient_rome",
            "query": (
                "ancient Rome daily life "
                "historical 3D reconstruction"
            ),
            "matched_terms": ["rome_context"],
        }

    return {
        "matched": False,
        "context": None,
        "query": raw,
        "matched_terms": [],
    }

def refine_youtube_candidates_for_context(
    candidates: list[dict],
    *,
    beat_text: str,
    search_query: str | None = None,
    visual_plan: dict | None = None,
) -> list[dict]:
    """
    Refine YouTube metadata ranking using the historical context of the beat.

    This second-stage ranker:
      - rewards Ancient Rome / Roman context;
      - rewards visually useful documentary/reconstruction signals;
      - rewards beat-specific visual vocabulary;
      - penalizes wrong civilizations and unrelated entertainment content.

    It does not replace OpenCLIP. It only improves candidate selection
    before preview downloads.
    """
    import unicodedata

    def normalize(value: str) -> str:
        value = str(value or "").lower()

        value = unicodedata.normalize(
            "NFKD",
            value,
        )

        return "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

    beat = normalize(beat_text)
    query = normalize(search_query or "")
    director_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    director_human_action = bool(
        director_plan.get(
            "human_action_required"
        )
    )

    director_action_priority = normalize(
        director_plan.get(
            "action_priority"
        )
        or ""
    )

    def _director_values(key):
        raw = director_plan.get(key)

        if isinstance(raw, str):
            raw = [raw]

        if not isinstance(raw, list):
            return []

        result = []

        for value in raw:
            value = normalize(value)

            if (
                value
                and value not in result
            ):
                result.append(value)

        return result

    director_subjects = (
        _director_values("subject")
        + _director_values(
            "subject_identity"
        )
    )

    director_actions = (
        _director_values("action")
    )

    director_environments = (
        _director_values(
            "environment"
        )
    )

    def _director_tokens(values):
        result = []

        ignored = {
            "the",
            "and",
            "with",
            "during",
            "ancient",
            "historical",
            "operation",
        }

        for value in values:
            for token in value.replace(
                "-",
                " ",
            ).split():

                token = token.strip()

                if (
                    len(token) < 4
                    or token in ignored
                ):
                    continue

                variants = [token]

                if (
                    token.endswith("ing")
                    and len(token) > 6
                ):
                    variants.append(
                        token[:-3]
                    )

                if (
                    token.endswith("ed")
                    and len(token) > 5
                ):
                    variants.append(
                        token[:-2]
                    )

                if (
                    token.endswith("s")
                    and len(token) > 5
                ):
                    variants.append(
                        token[:-1]
                    )

                for variant in variants:
                    if (
                        len(variant) >= 4
                        and variant not in result
                    ):
                        result.append(
                            variant
                        )

        return result

    director_subject_tokens = (
        _director_tokens(
            director_subjects
        )
    )

    director_action_tokens = (
        _director_tokens(
            director_actions
        )
    )
    terrain_only_action_tokens = {
        "uphill",
        "hill",
        "hills",
        "mountain",
        "mountains",
        "terrain",
        "slope",
        "slopes",
        "ridge",
        "ridges",
        "path",
        "paths",
        "rugged",
    }

    director_action_tokens = [
        token
        for token in director_action_tokens
        if token not in terrain_only_action_tokens
    ]

    director_environment_tokens = (
        _director_tokens(
            director_environments
        )
    )
    
    roman_context = any(
        term in f"{beat} {query}"
        for term in [
            "roma",
            "rome",
            "roman",
            "romano",
            "romana",
            "romanos",
            "romanas",
        ]
    )

    # Visual/documentary characteristics that are generally useful.
    visual_positive = {
        "reconstruction": 2.5,
        "reconstructed": 2.5,
        "3d": 2.0,
        "virtual": 1.5,
        "documentary": 1.5,
        "daily life": 2.0,
        "life in ancient rome": 2.5,
        "tour": 1.0,
        "walking": 1.0,
        "walkthrough": 1.5,
        "immersive": 1.0,
        "archaeology": 1.0,
        "historical": 1.0,
    }

    # Subjects that should fall sharply when the active context is Rome.
    wrong_roman_context = {
        "ancient china": -12.0,
        "china": -9.0,
        "chinese": -9.0,
        "tang dynasty": -12.0,
        "ancient greece": -10.0,
        "greece": -8.0,
        "greek": -8.0,
        "carthage": -8.0,
        "carthaginian": -8.0,
        "silk road": -9.0,
        "medieval": -9.0,
        "middle ages": -9.0,
        "ancient egypt": -9.0,
        "egyptian": -7.0,
        "egypt": -7.0,
    }

    unrelated_content = {
        "anime": -15.0,
        "reincarnated": -15.0,
        "assassin": -12.0,
        "gameplay": -10.0,
        "gaming": -10.0,
        "music video": -10.0,
        "lyrics": -10.0,
        "reaction": -8.0,
        "podcast": -7.0,
    }

    # Beat-specific visual vocabulary.
    beat_visual_terms = {}

    if any(
        term in beat
        for term in [
            "rua",
            "ruas",
            "street",
            "streets",
        ]
    ):
        beat_visual_terms.update(
            {
                "street": 3.0,
                "streets": 3.0,
                "city": 1.0,
                "urban": 1.0,
            }
        )

    if any(
        term in beat
        for term in [
            "comerciante",
            "comerciantes",
            "mercador",
            "mercadores",
            "mercado",
            "merchant",
            "market",
        ]
    ):
        beat_visual_terms.update(
            {
                "merchant": 4.0,
                "merchants": 4.0,
                "market": 4.0,
                "marketplace": 4.0,
                "trade": 2.0,
                "traders": 3.0,
                "daily life": 2.0,
            }
        )

    refined = []

    for candidate in candidates:
        item = dict(candidate)

        title = normalize(
            item.get("title") or ""
        )

        uploader = normalize(
            item.get("uploader") or ""
        )

        haystack = f"{title} {uploader}"

        base_score = float(
            item.get("metadata_score") or 0.0
        )

        adjustment = 0.0
        reasons = []

        # Strong Roman-context reward.
        if roman_context:
            if "ancient rome" in haystack:
                adjustment += 5.0
                reasons.append("+5 ancient_rome")

            elif "rome" in haystack:
                adjustment += 3.0
                reasons.append("+3 rome")

            if "roman" in haystack:
                adjustment += 2.0
                reasons.append("+2 roman")

        # Visual usefulness.
        for term, value in visual_positive.items():
            if term in haystack:
                adjustment += value
                reasons.append(
                    f"+{value:g} visual:{term}"
                )

        # Beat-specific terms.
        for term, value in beat_visual_terms.items():
            if term in haystack:
                adjustment += value
                reasons.append(
                    f"+{value:g} beat:{term}"
                )

        # Wrong historical context.
        if roman_context:
            for term, value in wrong_roman_context.items():
                if term in haystack:
                    adjustment += value
                    reasons.append(
                        f"{value:g} wrong_context:{term}"
                    )

        # General junk / entertainment mismatch.
        for term, value in unrelated_content.items():
            if term in haystack:
                adjustment += value
                reasons.append(
                    f"{value:g} unrelated:{term}"
                )

        # -----------------------------------------------------
        # O Bardo Historical Director V2.3.3
        # Action Fidelity Ranking
        # -----------------------------------------------------

        action_fidelity = 0.0
        action_reasons = []

        matched_subjects = [
            token
            for token in director_subject_tokens
            if token in haystack
        ]

        matched_actions = [
            token
            for token in director_action_tokens
            if token in haystack
        ]

        matched_environments = [
            token
            for token in director_environment_tokens
            if token in haystack
        ]

        if matched_subjects:
            subject_bonus = min(
                8.0,
                len(matched_subjects) * 3.0,
            )

            action_fidelity += subject_bonus

            action_reasons.append(
                f"+{subject_bonus:g} "
                "director_subject:"
                + ",".join(
                    matched_subjects
                )
            )

        if matched_actions:
            action_bonus = min(
                18.0,
                len(matched_actions) * 6.0,
            )

            action_fidelity += action_bonus

            action_reasons.append(
                f"+{action_bonus:g} "
                "director_action:"
                + ",".join(
                    matched_actions
                )
            )

        if matched_environments:
            env_bonus = min(
                6.0,
                len(
                    matched_environments
                ) * 2.0,
            )

            action_fidelity += env_bonus

            action_reasons.append(
                f"+{env_bonus:g} "
                "director_environment:"
                + ",".join(
                    matched_environments
                )
            )

        motion_markers = (
            "march",
            "ascend",
            "climb",
            "advance",
            "move",
            "moving",
            "walk",
            "run",
            "charge",
            "enter",
            "approach",
        )

        terrain_markers = (
            "uphill",
            "hill",
            "mountain",
            "terrain",
            "slope",
            "ridge",
        )

        matched_motion = [
            marker
            for marker in motion_markers
            if marker in haystack
        ]

        matched_terrain = [
            marker
            for marker in terrain_markers
            if marker in haystack
        ]

        # A word such as "uphill" alone describes terrain/
        # direction. It does not prove that human action is
        # actually represented by the candidate.
        # -----------------------------------------------------
        # V2.3.3.2 - Subject-bound human motion
        # -----------------------------------------------------

        human_subject_markers = (
            "roman",
            "romans",
            "soldier",
            "soldiers",
            "legionary",
            "legionaries",
            "legion",
            "legions",
            "army",
            "troops",
            "infantry",
        )

        human_subject_matches = [
            marker
            for marker in human_subject_markers
            if marker in haystack
        ]

        exact_director_action = bool(
            matched_actions
        )

        subject_bound_motion = bool(
            human_subject_matches
            and matched_motion
        )

        has_action_evidence = bool(
            exact_director_action
            or subject_bound_motion
        )

        if (
            director_human_action
            and subject_bound_motion
            and not exact_director_action
        ):
            action_fidelity += 4.0

            action_reasons.append(
                "+4 subject_bound_motion:"
                + ",".join(
                    human_subject_matches
                )
                + "|"
                + ",".join(
                    matched_motion
                )
            )

        if (
            director_human_action
            and exact_director_action
            and matched_terrain
        ):
            action_fidelity += 8.0

            action_reasons.append(
                "+8 exact_action_plus_terrain:"
                + ",".join(
                    matched_actions
                )
                + "|"
                + ",".join(
                    matched_terrain
                )
            )

        elif (
            director_human_action
            and matched_terrain
            and not exact_director_action
            and not subject_bound_motion
        ):
            action_fidelity -= 8.0

            action_reasons.append(
                "-8 terrain_without_human_motion:"
                + ",".join(
                    matched_terrain
                )
            )


        if (
            director_human_action
            and not has_action_evidence
        ):
            penalty = (
                -12.0
                if director_action_priority
                == "high"
                else -8.0
            )

            action_fidelity += penalty

            action_reasons.append(
                f"{penalty:g} "
                "missing_director_action"
            )

        static_markers = (
            "fort",
            "wall",
            "museum",
            "architecture",
            "archaeology",
            "archaeological",
            "ruins",
            "megacity",
            "tour",
        )

        static_matches = [
            marker
            for marker in static_markers
            if marker in haystack
        ]

        if (
            director_human_action
            and not has_action_evidence
            and static_matches
        ):
            action_fidelity -= 8.0

            action_reasons.append(
                "-8 static_for_human_action:"
                + ",".join(
                    static_matches
                )
            )

        bad_action_content = (
            "gameplay",
            "video game",
            "rise of cultures",
            "total war cinematic",
        )

        bad_matches = [
            marker
            for marker in bad_action_content
            if marker in haystack
        ]

        if bad_matches:
            action_fidelity -= 16.0

            action_reasons.append(
                "-16 action_content_mismatch:"
                + ",".join(
                    bad_matches
                )
            )

        adjustment += action_fidelity

        item[
            "action_fidelity_score"
        ] = float(
            action_fidelity
        )

        item[
            "action_fidelity_reasons"
        ] = action_reasons

        item[
            "action_fidelity_matches"
        ] = {
            "subject": matched_subjects,
            "action": matched_actions,
            "environment": (
                matched_environments
            ),
        }

        reasons.extend(
            action_reasons
        )
        final_score = base_score + adjustment

        item["context_adjustment"] = float(
            adjustment
        )

        item["context_score"] = float(
            final_score
        )

        item["context_reasons"] = reasons

        refined.append(item)

    refined.sort(
        key=lambda item: (
            item["context_score"],
            item.get("metadata_score", 0.0),
        ),
        reverse=True,
    )

    for index, item in enumerate(
        refined,
        1,
    ):
        item["context_rank"] = index

    return refined

def filter_candidates_by_historical_context(
    candidates: list[dict],
    *,
    beat_text: str,
    search_query: str | None = None,
) -> list[dict]:
    """
    Hard contextual gate applied after metadata/context ranking.

    For Ancient Rome beats:
      - reject clearly different civilizations/periods;
      - require a meaningful Roman-context signal.

    This prevents visually similar but historically unrelated results
    from reaching the expensive preview/OpenCLIP stage.
    """
    import unicodedata

    def normalize(value: str) -> str:
        value = str(value or "").lower()

        value = unicodedata.normalize(
            "NFKD",
            value,
        )

        return "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

    beat = normalize(beat_text)
    query = normalize(search_query or "")

    context_text = f"{beat} {query}"

    roman_context = any(
        term in context_text
        for term in [
            "roma",
            "rome",
            "roman",
            "romano",
            "romana",
            "romanos",
            "romanas",
        ]
    )

    if not roman_context:
        return list(candidates)

    roman_signals = [
        "ancient rome",
        "rome",
        "roman",
        "romans",
        "roman empire",
        "roman city",
        "roman citizen",
        "cardo",
        "forum romanum",
        "roman forum",
        "domus",
        "pompeii",
        "ostia antica",
    ]

    wrong_context = [
        "ancient china",
        "china",
        "chinese",
        "tang dynasty",
        "ancient greece",
        "greece",
        "greek",
        "carthage",
        "carthaginian",
        "indus valley",
        "angkor",
        "cambodia",
        "seoul",
        "korea",
        "jerusalem",
        "ancient egypt",
        "egyptian",
        "egypt",
        "medieval",
        "middle ages",
        "silk road",
    ]

    accepted = []

    for candidate in candidates:
        title = normalize(
            candidate.get("title") or ""
        )

        uploader = normalize(
            candidate.get("uploader") or ""
        )

        haystack = f"{title} {uploader}"

        rejected_terms = [
            term
            for term in wrong_context
            if term in haystack
        ]

        if rejected_terms:
            continue

        matched_roman = [
            term
            for term in roman_signals
            if term in haystack
        ]

        if not matched_roman:
            continue

        item = dict(candidate)

        item["historical_context_gate"] = True
        item["historical_context"] = "ancient_rome"
        item["historical_context_matches"] = matched_roman

        accepted.append(item)

    return accepted

def prune_contextually_weak_candidates(
    candidates: list[dict],
    *,
    beat_text: str,
    minimum_context_score: float = 8.0,
) -> list[dict]:
    """
    Final cheap pruning before preview/OpenCLIP analysis.

    Removes:
      - candidates with weak contextual score;
      - obvious content-type mismatches;
      - military/battle material when the beat asks for civilian urban life.
    """
    import unicodedata

    def normalize(value: str) -> str:
        value = str(value or "").lower()

        value = unicodedata.normalize(
            "NFKD",
            value,
        )

        return "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

    beat = normalize(beat_text)

    civilian_urban_beat = any(
        term in beat
        for term in [
            "rua",
            "ruas",
            "comerciante",
            "comerciantes",
            "mercado",
            "mercador",
            "mercadores",
            "street",
            "merchant",
            "market",
        ]
    )

    generic_bad_terms = [
        "ambience",
        "ambient",
        "music",
        "soundtrack",
        "relaxing",
        "sleep",
        "asmr",
        "loop",
    ]

    military_terms = [
        "battle",
        "war",
        "siege",
        "alesia",
        "legion battle",
        "military campaign",
    ]

    accepted = []

    for candidate in candidates:
        score = float(
            candidate.get(
                "context_score",
                candidate.get(
                    "metadata_score",
                    0.0,
                ),
            )
            or 0.0
        )

        if score < float(minimum_context_score):
            continue

        title = normalize(
            candidate.get("title") or ""
        )

        rejection_reasons = []

        for term in generic_bad_terms:
            if term in title:
                rejection_reasons.append(
                    f"content_mismatch:{term}"
                )

        if civilian_urban_beat:
            for term in military_terms:
                if term in title:
                    rejection_reasons.append(
                        f"civilian_vs_military:{term}"
                    )

        if rejection_reasons:
            continue

        item = dict(candidate)
        item["context_pruned"] = True
        item["context_prune_threshold"] = float(
            minimum_context_score
        )

        accepted.append(item)

    return accepted

def build_wikimedia_query_candidates(
    beat_text: str,
    *,
    identity: dict | None = None,
    context_text: str | None = None,
) -> list[str]:
    """
    Build concise Wikimedia-friendly historical queries.

    Wikimedia generally performs better with canonical object/place
    names than with full narration sentences.
    """
    import unicodedata

    beat_text = str(beat_text or "").strip()
    context_text = str(context_text or "").strip()

    identity = (
        identity
        or detect_historical_identity(
            f"{beat_text} {context_text or ''}"
        )
    )

    def normalize(value: str) -> str:
        value = unicodedata.normalize(
            "NFKD",
            str(value or "").lower(),
        )

        return "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

    combined = normalize(
        f"{beat_text} {context_text}"
    )

    canonical = str(
        identity.get("canonical")
        or ""
    ).strip().lower()

    query_term = str(
        identity.get("query_term")
        or ""
    ).strip()

    queries = []

    def add(value):
        value = str(
            value or ""
        ).strip()

        if (
            value
            and value not in queries
        ):
            queries.append(value)

    # Exact canonical identity always comes first.
    add(query_term)

    spectator_context = any(
        term in combined
        for term in [
            "espectador",
            "espectadores",
            "publico",
            "plateia",
            "multidao",
            "arquibancada",
            "arquibancadas",
            "espetaculo",
            "espetaculos",
            "spectator",
            "spectators",
            "audience",
            "crowd",
            "stands",
            "games",
            "spectacle",
        ]
    )

    if canonical == "strigil":
        add("Roman bronze strigil")
        add("ancient Roman strigil")
        add("Roman strigil artifact")
        add("bronze strigil archaeology")

    elif canonical == "roman baths":
        add("Roman baths ancient Rome")
        add("ancient Roman thermae")
        add("Roman bathhouse archaeology")
        add("Baths of Caracalla ancient Rome")

    elif canonical == "colosseum":
        if spectator_context:
            add("Colosseum spectators reconstruction")
            add("Roman amphitheatre spectators illustration")
            add("Pollice Verso Colosseum")
            add("Roman Colosseum crowd illustration")
            add("Colosseum interior spectators historical painting")
            add("Roman games audience Colosseum illustration")
            add("Flavian Amphitheatre crowd reconstruction")
            add("Colosseum arena crowd ancient Rome painting")
            add("Roman spectacle crowd historical illustration")
        else:
            add("Colosseum ancient Rome reconstruction")
            add("Flavian Amphitheatre historical illustration")
            add("Colosseum interior reconstruction")

    # Generic beat, but surrounding context clearly indicates Rome.
    roman_context = any(
        term in combined
        for term in [
            "roma",
            "roman",
            "romano",
            "romana",
            "romanos",
            "termas romanas",
            "ancient rome",
        ]
    )

    bath_context = any(
        term in combined
        for term in [
            "banho",
            "banhos",
            "terma",
            "termas",
            "bath",
            "baths",
            "thermae",
        ]
    )

    if (
        not identity.get("specific")
        and roman_context
        and bath_context
    ):
        add("Roman baths ancient Rome")
        add("ancient Roman thermae social life")
        add("Roman bathhouse archaeology")

    # ---------------------------------------------------------
    # Intent-aware Roman Wikimedia queries.
    #
    # Generic Roman beats should preserve the subject/action
    # instead of collapsing into broad terms such as
    # "ancient Rome daily".
    # ---------------------------------------------------------
    daily_life_context = any(
        term in combined
        for term in [
            "vida cotidiana",
            "cotidiano",
            "dia a dia",
            "daily life",
            "everyday life",
            "urban life",
            "vida urbana",
            "habitantes",
            "civis",
            "civilians",
            "townspeople",
        ]
    )

    urban_context = any(
        term in combined
        for term in [
            "cidade",
            "urbano",
            "urbana",
            "rua",
            "ruas",
            "street",
            "streets",
            "urban",
            "edificio",
            "edificios",
            "building",
            "buildings",
            "estrutura",
            "estruturas",
            "architecture",
            "arquitetura",
            "pedra",
            "stone",
        ]
    )

    market_context = any(
        term in combined
        for term in [
            "mercado",
            "market",
            "comercio",
            "comÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©rcio",
            "trade",
            "merchant",
            "merchants",
            "comerciante",
            "comerciantes",
        ]
    )

    military_context = any(
        term in combined
        for term in [
            "soldado",
            "soldados",
            "soldier",
            "soldiers",
            "legiao",
            "legiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â£o",
            "legion",
            "legionary",
            "militar",
            "military",
        ]
    )

    if (
        not identity.get("specific")
        and roman_context
        and daily_life_context
        and urban_context
    ):
        # Human activity first: for everyday-life narration we need
        # people performing activities inside a Roman environment,
        # not merely empty architecture.
        add("ancient Roman street scene people")
        add("Roman daily life people illustration")
        add("Roman forum crowd reconstruction")
        add("Roman market scene people")
        add("Pompeii street scene people")
        add("Roman citizens street reconstruction")
        add("Roman urban life people historical painting")
        add("ancient Roman everyday life crowd")
        add("Roman shops street life illustration")
        add("Roman forum people historical illustration")

    elif (
        not identity.get("specific")
        and roman_context
        and daily_life_context
    ):
        add("ancient Roman daily life reconstruction")
        add("Roman everyday life fresco")
        add("Roman daily life mosaic")
        add("Roman daily life historical illustration")
        add("Pompeii daily life fresco")

    if (
        not identity.get("specific")
        and roman_context
        and market_context
    ):
        add("ancient Roman market scene")
        add("Roman forum market reconstruction")
        add("Roman trade relief")
        add("Roman merchant fresco")
        add("Pompeii market scene")

    if (
        not identity.get("specific")
        and roman_context
        and military_context
    ):
        add("Roman legion relief")
        add("Roman legionary reconstruction")
        add("ancient Roman soldiers relief")
        add("Roman military fresco")
    # Original visual query remains a late fallback.
    add(
        build_visual_search_query(
            beat_text,
            identity=identity,
        )
    )

    # Raw narration comes last, not first.
    add(beat_text)

    # Keep Wikimedia searching focused.
    # Specific historical intent should win over broad narration fallbacks.
    return queries[:10]

def build_stock_query_candidates(
    beat_text: str,
    *,
    context_text: str | None = None,
) -> list[str]:
    """
    Build stock-friendly visual queries.

    Stock libraries are best used for contextual/background
    footage rather than rare archaeological objects.
    """
    import unicodedata

    beat_text = str(
        beat_text or ""
    ).strip()

    context_text = str(
        context_text or ""
    ).strip()

    identity = detect_historical_identity(
        beat_text
    )

    canonical = str(
        identity.get("canonical")
        or ""
    ).strip().lower()

    def normalize(value):
        value = unicodedata.normalize(
            "NFKD",
            str(value or "").lower(),
        )

        return "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

    combined = normalize(
        f"{beat_text} {context_text}"
    )

    queries = []

    def add(value):
        value = str(
            value or ""
        ).strip()

        if (
            value
            and value not in queries
        ):
            queries.append(value)

    # A strigil itself belongs to Wikimedia.
    # Stock provides the surrounding Roman-bath context.
    if canonical == "strigil":
        add("Roman baths ancient Rome")
        add("ancient Roman ruins")
        add("Roman archaeology museum")

    elif canonical == "roman baths":
        add("Roman baths ancient Rome")
        add("ancient Roman ruins")
        add("Roman archaeological ruins")

    elif any(
        term in combined
        for term in [
            "termas",
            "banho",
            "banhos",
            "thermae",
            "roman bath",
        ]
    ):
        add("Roman baths ancient Rome")
        add("ancient Roman ruins")
        add("Roman archaeology")

    elif any(
        term in combined
        for term in [
            "roma",
            "roman",
            "romano",
            "romana",
            "romanos",
            "ancient rome",
        ]
    ):
        add("ancient Rome ruins")
        add("Roman archaeology")
        add("ancient Roman architecture")

    # Existing visual query is still useful for generic subjects.
    if not queries:
        add(
            build_visual_search_query(
                beat_text,
                identity=identity,
            )
        )

    return queries[:3]


def _obardo_frame_path(frame_item: dict) -> str | None:
    """
    Resolve frame filepath defensively because older/newer
    sampling helpers may use slightly different key names.
    """
    if not isinstance(frame_item, dict):
        return None

    for key in [
        "filepath",
        "frame_path",
        "path",
        "image_path",
        "file",
    ]:
        value = frame_item.get(key)

        if value:
            path = Path(str(value))

            if path.exists():
                return str(path)

    return None


# ------------------------------------------------------------
# O Bardo Turbo v1
# Persistent OpenCLIP visual cache primitives.
#
# IMPORTANT:
# These helpers are intentionally NOT wired into the resolver yet.
# Block 4.15B1 only establishes and tests the cache mechanics.
# ------------------------------------------------------------

_OBARDO_OPENCLIP_VISUAL_CACHE_VERSION = 1


def _obardo_openclip_visual_cache_root() -> Path:
    cache_root = (
        PROJECT_ROOT
        / "storage"
        / "obardo_cache"
        / "openclip_visual"
    )

    cache_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return cache_root


def _obardo_openclip_visual_fingerprint(
    video_path: str | Path,
    *,
    interval_seconds: float,
    max_frames: int,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
) -> dict:
    """
    Build a cheap but invalidation-safe identity for cached visual embeddings.

    The full video is deliberately not SHA-256 hashed because that would add
    substantial disk I/O to large stock files. Path + size + mtime_ns detect
    ordinary replacement/modification of the local cached media.
    """
    import hashlib
    import json

    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    resolved = video_path.resolve()
    stat = resolved.stat()

    identity = {
        "schema_version": (
            _OBARDO_OPENCLIP_VISUAL_CACHE_VERSION
        ),
        "video_path": str(resolved),
        "video_size": int(stat.st_size),
        "video_mtime_ns": int(stat.st_mtime_ns),
        "interval_seconds": float(
            max(
                float(interval_seconds),
                1.0,
            )
        ),
        "max_frames": int(
            max(
                int(max_frames),
                1,
            )
        ),
        "model_name": str(model_name),
        "pretrained": str(pretrained),
    }

    serialized = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    identity["cache_key"] = hashlib.sha256(
        serialized.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()

    return identity


def _obardo_openclip_visual_cache_path(
    fingerprint: dict,
) -> Path:
    cache_key = str(
        fingerprint.get("cache_key")
        or ""
    ).strip()

    if not cache_key:
        raise ValueError(
            "OpenCLIP visual cache fingerprint has no cache_key."
        )

    return (
        _obardo_openclip_visual_cache_root()
        / f"{cache_key}.npz"
    )


def _obardo_save_openclip_visual_cache(
    cache_path: str | Path,
    *,
    fingerprint: dict,
    scenes: list[dict],
    image_features,
) -> Path:
    """
    Persist normalized image embeddings and scene metadata atomically.

    frame_path/filepath keys are deliberately removed because
    OBARDO_FRAMES_DIR is temporary and is cleaned after rendering.
    """
    import json
    import numpy as np

    cache_path = Path(cache_path)

    cache_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_scenes = []

    volatile_path_keys = {
        "filepath",
        "frame_path",
        "path",
        "image_path",
        "file",
    }

    for scene in scenes:
        if not isinstance(scene, dict):
            continue

        safe_scene = {
            key: value
            for key, value in scene.items()
            if key not in volatile_path_keys
        }

        safe_scenes.append(
            safe_scene
        )

    if hasattr(image_features, "detach"):
        features = (
            image_features
            .detach()
            .cpu()
            .numpy()
        )
    else:
        features = np.asarray(
            image_features
        )

    features = np.asarray(
        features,
        dtype=np.float32,
    )

    if features.ndim != 2:
        raise ValueError(
            "image_features must be a 2D matrix."
        )

    if features.shape[0] != len(safe_scenes):
        raise ValueError(
            "Embedding row count does not match scene count."
        )

    fingerprint_json = json.dumps(
        fingerprint,
        ensure_ascii=False,
        sort_keys=True,
    )

    scenes_json = json.dumps(
        safe_scenes,
        ensure_ascii=False,
    )

    temp_path = cache_path.with_name(
        cache_path.name + ".tmp"
    )

    with temp_path.open("wb") as handle:
        np.savez_compressed(
            handle,
            fingerprint_json=np.array(
                [fingerprint_json]
            ),
            scenes_json=np.array(
                [scenes_json]
            ),
            image_features=features,
        )

    temp_path.replace(
        cache_path
    )

    return cache_path


def _obardo_load_openclip_visual_cache(
    cache_path: str | Path,
    *,
    expected_fingerprint: dict,
) -> dict | None:
    """
    Load a visual embedding cache entry.

    Fail-open by design: corrupt, incomplete or stale entries simply miss.
    """
    import json
    import numpy as np

    cache_path = Path(cache_path)

    if not cache_path.exists():
        return None

    try:
        with np.load(
            cache_path,
            allow_pickle=False,
        ) as data:
            fingerprint_json = str(
                data[
                    "fingerprint_json"
                ][0]
            )

            scenes_json = str(
                data[
                    "scenes_json"
                ][0]
            )

            image_features = np.array(
                data[
                    "image_features"
                ],
                dtype=np.float32,
                copy=True,
            )

        fingerprint = json.loads(
            fingerprint_json
        )

        scenes = json.loads(
            scenes_json
        )

        if (
            fingerprint.get("cache_key")
            != expected_fingerprint.get(
                "cache_key"
            )
        ):
            return None

        if not isinstance(scenes, list):
            return None

        if image_features.ndim != 2:
            return None

        if image_features.shape[0] != len(
            scenes
        ):
            return None

        return {
            "fingerprint": fingerprint,
            "scenes": scenes,
            "image_features": image_features,
            "cache_path": str(cache_path),
        }

    except Exception:
        return None


def _obardo_materialize_frame_at_timestamp(
    video_path: str | Path,
    *,
    timestamp: float,
    candidate_id: str,
    frame_index: int = 1,
) -> str:
    """
    Materialize one temporary JPEG near an already-ranked timestamp.

    This is intended only for downstream consumers that require a physical
    image file, such as the OpenAI editorial image evaluator.
    """
    import subprocess

    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    timestamp = max(
        float(timestamp),
        0.0,
    )

    frame_index = max(
        int(frame_index),
        1,
    )

    safe_candidate = "".join(
        char
        if (
            char.isalnum()
            or char in "-_"
        )
        else "_"
        for char in str(candidate_id)
    )

    output_dir = (
        OBARDO_FRAMES_DIR
        / (
            f"{safe_candidate}"
            "-openclip-cache"
        )
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        output_dir
        / f"winner-{frame_index:03d}.jpg"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if (
        result.returncode != 0
        or not output_path.exists()
        or output_path.stat().st_size <= 0
    ):
        raise RuntimeError(
            "O Bardo cached-frame materialization failed:\n"
            + result.stderr[-4000:]
        )

    return str(output_path)


def evaluate_local_video_candidate(
    video_path: str | Path,
    *,
    beat_text: str,
    context_text: str | None = None,
    visual_query: str | None = None,
    visual_plan: dict | None = None,
    candidate_id: str = "stock",
    interval_seconds: float = 3.0,
    max_frames: int = 40,
    openai_top_k: int = 2,
    run_openai: bool = True,
    precomputed_local: dict | None = None,
) -> dict:
    """
    Local-first video evaluation.

    1. Sample frames.
    2. Rank locally with OpenCLIP.
    3. Optionally send only the best frames to OpenAI.
    """

    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    visual_query = build_director_openclip_query(
        beat_text,
        visual_plan=visual_plan,
        fallback_query=visual_query,
    )

    if (
        isinstance(precomputed_local, dict)
        and precomputed_local.get("openclip_top")
    ):
        visual_rank = list(
            precomputed_local.get("openclip_top")
            or []
        )

        best = visual_rank[0]

        result = dict(
            precomputed_local
        )

        result["openclip_best"] = best
        result["openclip_top"] = visual_rank
        result["openclip_score"] = float(
            best.get(
                "semantic_score",
                0.0,
            )
            or 0.0
        )
        result["best_timestamp"] = float(
            result.get(
                "best_timestamp",
                best.get(
                    "timestamp",
                    0.0,
                ),
            )
            or 0.0
        )
        result["openai_evaluations"] = []
        result["openai_score"] = None
        result["openai_rejected"] = False

    else:
        from loguru import logger

        effective_interval = max(
            float(interval_seconds),
            0.5,
        )

        effective_max_frames = max(
            int(max_frames),
            1,
        )

        openclip_model_name = "ViT-B-32"
        openclip_pretrained = (
            "laion2b_s34b_b79k"
        )

        fingerprint = (
            _obardo_openclip_visual_fingerprint(
                video_path,
                interval_seconds=effective_interval,
                max_frames=effective_max_frames,
                model_name=openclip_model_name,
                pretrained=openclip_pretrained,
            )
        )

        cache_path = (
            _obardo_openclip_visual_cache_path(
                fingerprint
            )
        )

        cache_entry = (
            _obardo_load_openclip_visual_cache(
                cache_path,
                expected_fingerprint=fingerprint,
            )
        )

        openclip_visual_cache_hit = (
            cache_entry is not None
        )

        if openclip_visual_cache_hit:
            cached_scenes = (
                cache_entry.get(
                    "scenes"
                )
                or []
            )

            cached_features = (
                cache_entry.get(
                    "image_features"
                )
            )

            visual_rank = (
                _obardo_rank_openclip_image_features_by_text(
                    cached_scenes,
                    cached_features,
                    visual_query,
                    model_name=openclip_model_name,
                    pretrained=openclip_pretrained,
                    top_k=max(
                        int(openai_top_k),
                        3,
                    ),
                )
            )

            logger.info(
                "O Bardo TURBO OPENCLIP VISUAL CACHE HIT | "
                "candidate={} | frames={} | cache={}",
                candidate_id,
                len(cached_scenes),
                cache_path.name,
            )

        else:
            sampled = extract_preview_sample_frames(
                str(video_path),
                video_id=str(candidate_id),
                interval_seconds=effective_interval,
                max_frames=effective_max_frames,
            )

            samples = (
                sampled.get(
                    "samples"
                )
                or []
            )

            valid_items, image_features = (
                _obardo_encode_scene_frames_openclip(
                    samples,
                    model_name=openclip_model_name,
                    pretrained=openclip_pretrained,
                )
            )

            if (
                not valid_items
                or image_features is None
            ):
                visual_rank = []

            else:
                visual_rank = (
                    _obardo_rank_openclip_image_features_by_text(
                        valid_items,
                        image_features,
                        visual_query,
                        model_name=openclip_model_name,
                        pretrained=openclip_pretrained,
                        top_k=max(
                            int(openai_top_k),
                            3,
                        ),
                    )
                )

                try:
                    _obardo_save_openclip_visual_cache(
                        cache_path,
                        fingerprint=fingerprint,
                        scenes=valid_items,
                        image_features=image_features,
                    )

                    logger.info(
                        "O Bardo TURBO OPENCLIP VISUAL CACHE ADD | "
                        "candidate={} | frames={} | cache={}",
                        candidate_id,
                        len(valid_items),
                        cache_path.name,
                    )

                except Exception as exc:
                    logger.warning(
                        "O Bardo OPENCLIP VISUAL CACHE WRITE FAILED | "
                        "candidate={} | error={}",
                        candidate_id,
                        exc,
                    )

        if not visual_rank:
            raise RuntimeError(
                "OpenCLIP returned no frames."
            )

        best = visual_rank[0]

        result = {
            "openclip_best": best,
            "openclip_top": visual_rank,
            "openclip_score": float(
                best.get(
                    "semantic_score",
                    0.0,
                )
                or 0.0
            ),
            "best_timestamp": float(
                best.get(
                    "timestamp",
                    0.0,
                )
                or 0.0
            ),
            "openai_evaluations": [],
            "openai_score": None,
            "openai_rejected": False,
        }

    if not run_openai:
        return result

    # --------------------------------------------------------
    # Persistent OpenCLIP cache entries deliberately do not
    # retain frame_path because OBARDO_FRAMES_DIR is temporary.
    #
    # Only when an editorial consumer actually needs physical
    # JPEGs do we recreate the few winning timestamps.
    # --------------------------------------------------------
    for frame_index, frame in enumerate(
        visual_rank[
            : max(
                int(openai_top_k),
                1,
            )
        ],
        start=1,
    ):
        if _obardo_frame_path(
            frame
        ):
            continue

        try:
            materialized_path = (
                _obardo_materialize_frame_at_timestamp(
                    video_path,
                    timestamp=float(
                        frame.get(
                            "timestamp",
                            0.0,
                        )
                        or 0.0
                    ),
                    candidate_id=str(
                        candidate_id
                    ),
                    frame_index=frame_index,
                )
            )

            frame[
                "frame_path"
            ] = materialized_path

        except Exception as exc:
            from loguru import logger

            logger.warning(
                "O Bardo OPENCLIP CACHE FRAME MATERIALIZATION FAILED | "
                "candidate={} | timestamp={} | error={}",
                candidate_id,
                frame.get(
                    "timestamp"
                ),
                exc,
            )

    try:
        from app.services import (
            obardo_openai_editorial,
        )

        if not obardo_openai_editorial.is_enabled():
            return result

        evaluations = []

        for frame in visual_rank[
            : max(
                int(openai_top_k),
                1,
            )
        ]:
            frame_path = _obardo_frame_path(
                frame
            )

            if not frame_path:
                continue

            evaluation = (
                obardo_openai_editorial
                .evaluate_image_candidate(
                    frame_path,
                    beat_text=beat_text,
                    context_text=context_text,
                    visual_plan=visual_plan or {},
                )
                or {}
            )

            if not evaluation:
                continue

            evaluation = dict(
                evaluation
            )

            evaluation[
                "timestamp"
            ] = frame.get(
                "timestamp"
            )

            evaluation[
                "openclip_score"
            ] = frame.get(
                "semantic_score"
            )

            evaluations.append(
                evaluation
            )

        result[
            "openai_evaluations"
        ] = evaluations

        if not evaluations:
            return result

        def editorial_score(item):
            relevance = float(
                item.get(
                    "relevance_score",
                    0,
                )
                or 0
            )

            historical = float(
                item.get(
                    "historical_score",
                    0,
                )
                or 0
            )

            documentary = float(
                item.get(
                    "documentary_value",
                    0,
                )
                or 0
            )

            bonus = (
                15.0
                if item.get(
                    "object_match"
                )
                else 0.0
            )

            penalty = (
                100.0
                if item.get(
                    "anachronism"
                )
                else 0.0
            )

            if (
                item.get("decision")
                == "reject"
            ):
                penalty += 100.0

            return (
                relevance * 0.50
                + historical * 0.30
                + documentary * 0.20
                + bonus
                - penalty
            )

        best_openai = max(
            evaluations,
            key=editorial_score,
        )

        result[
            "openai_best"
        ] = best_openai

        result[
            "openai_score"
        ] = editorial_score(
            best_openai
        )

        result[
            "openai_rejected"
        ] = (
            best_openai.get(
                "anachronism"
            )
            or best_openai.get(
                "decision"
            )
            == "reject"
        )

        # Prefer timestamp of the frame that
        # OpenAI judged best.
        if (
            best_openai.get(
                "timestamp"
            )
            is not None
        ):
            result[
                "best_timestamp"
            ] = float(
                best_openai[
                    "timestamp"
                ]
            )

    except Exception as exc:
        result[
            "openai_error"
        ] = (
            f"{type(exc).__name__}: "
            f"{exc}"
        )

    return result

def resolve_stock_video_for_beat(
    beat_text: str,
    *,
    context_text: str | None = None,
    duration_seconds: float = 4.0,
    video_aspect="16:9",
    output_name: str | None = None,
    cleanup_source: bool = True,
    avoid_media_keys: list[str] | None = None,
    visual_plan: dict | None = None,
) -> dict:
    """
    Resolve one beat from free stock providers.

    Providers:
        Pexels
        Pixabay
        Coverr

    All providers are searched when available, giving the
    editor provider variety instead of always choosing Pexels.
    """
    import hashlib
    import re
    from pathlib import Path
    from loguru import logger

    from app.models.schema import VideoAspect
    from app.services.material import (
        search_videos_pexels,
        search_videos_pixabay,
        search_videos_coverr,
        save_video,
    )

    beat_text = str(
        beat_text or ""
    ).strip()

    if not beat_text:
        raise ValueError(
            "beat_text is required."
        )

    duration_seconds = max(
        float(duration_seconds),
        0.5,
    )

    # Accept enum or plain aspect string.
    aspect_value = getattr(
        video_aspect,
        "value",
        video_aspect,
    )

    aspect_value = str(
        aspect_value or "16:9"
    )

    if aspect_value == "9:16":
        aspect = VideoAspect.portrait
        target_width = 1080
        target_height = 1920

    elif aspect_value == "1:1":
        aspect = VideoAspect.square
        target_width = 1080
        target_height = 1080

    else:
        aspect = VideoAspect.landscape
        target_width = 1920
        target_height = 1080

    queries = build_stock_query_candidates(
        beat_text,
        context_text=context_text,
    )

    
    # Period-safe contextual fallback.
    # Search for visually neutral historical environments rather
    # than modern crowds, tourists or contemporary activity.
    if (
        isinstance(visual_plan, dict)
        and visual_plan.get("fallback_mode")
        == "period_safe_background"
    ):
        period_safe_queries = []

        for query in queries:
            query = str(query or "").strip()

            if not query:
                continue

            period_safe_queries.append(
                f"{query} empty no people no tourists"
            )

        if period_safe_queries:
            queries = period_safe_queries
    providers = [
        (
            "pexels",
            search_videos_pexels,
        ),
        (
            "pixabay",
            search_videos_pixabay,
        ),
        (
            "coverr",
            search_videos_coverr,
        ),
    ]

    candidate_pool = []
    provider_errors = []

    minimum_duration = max(
        int(round(duration_seconds)),
        1,
    )

    for provider_name, search_fn in providers:
        provider_found = []

        for query in queries[:2]:
            try:
                found = search_fn(
                    search_term=query,
                    minimum_duration=minimum_duration,
                    video_aspect=aspect,
                ) or []

            except Exception as exc:
                provider_errors.append(
                    {
                        "provider": provider_name,
                        "query": query,
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                    }
                )
                continue

            for candidate in found:
                source_info = dict(
                    getattr(
                        candidate,
                        "source_info",
                        None,
                    )
                    or {}
                )

                rendition = dict(
                    source_info.get(
                        "rendition"
                    )
                    or {}
                )

                try:
                    rendition_width = int(
                        rendition.get(
                            "width"
                        )
                        or 0
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    rendition_width = 0

                # Avoid automatically choosing giant 4K
                # stock files for a 1080p final project.
                if (
                    target_width <= 1920
                    and rendition_width > 1920
                ):
                    continue

                provider_found.append(
                    {
                        "provider": provider_name,
                        "query": query,
                        "material": candidate,
                    }
                )

            if provider_found:
                break

        # Limit each provider so one API cannot dominate
        # the global pool.
        candidate_pool.extend(
            provider_found[:3]
        )

    avoided_keys = set(
        avoid_media_keys
        or []
    )

    preferred_candidate_pool = [
        item
        for item in candidate_pool
        if (
            str(
                item.get(
                    "provider"
                )
                or ""
            ).strip().lower()
            + ":"
            + str(
                item["material"].url
            ).strip()
        )
        not in avoided_keys
    ]

    # O Bardo strict global dedup.
    #
    # A source already used anywhere in the current video must
    # never become eligible again. If every candidate has already
    # been used, this resolver must fail and let the caller try
    # another source/fallback instead of recycling old footage.
    candidate_pool = preferred_candidate_pool

    if not candidate_pool:
        raise RuntimeError(
            "No free-stock candidate found. "
            f"Queries: {queries}; "
            f"errors: {provider_errors}"
        )

    # ---------------------------------------------------------
    # Local visual tournament.
    #
    # Stock previously selected candidates by deterministic
    # hash. Now we actually inspect their visual content.
    # ---------------------------------------------------------
    import hashlib

    digest = hashlib.sha256(
        beat_text.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()

    stock_dir = (
        Path("storage")
        / "obardo_temp"
        / "stock"
    )

    stock_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    identity = detect_historical_identity(
        beat_text
    )

    # Prefer stock/context queries for OpenCLIP because
    # they are usually more visually specific than the
    # raw narration sentence.
    if queries:
        visual_query = str(
            queries[0]
        ).strip()
    else:
        visual_query = build_visual_search_query(
            beat_text,
            identity=identity,
        )

    supplied_visual_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else None
    )

    if supplied_visual_plan is not None:
        visual_plan = supplied_visual_plan

    else:
        visual_plan = {}

        try:
            from app.services import (
                obardo_openai_editorial,
            )

            if obardo_openai_editorial.is_enabled():
                visual_plan = (
                    obardo_openai_editorial
                    .build_visual_plan(
                        beat_text,
                        context_text=context_text,
                    )
                    or {}
                )

        except Exception:
            visual_plan = {}

    primary_queries = (
        visual_plan.get(
            "primary_queries"
        )
        or []
    )

    context_queries = (
        visual_plan.get(
            "context_queries"
        )
        or []
    )

    if primary_queries:
        visual_query = str(
            primary_queries[0]
        )

    elif context_queries:
        visual_query = str(
            context_queries[0]
        )

    tournament = []

    # Limit bandwidth: evaluate at most six stock candidates.
    # Candidate pool already contains multiple providers.
    for stock_index, item in enumerate(
        candidate_pool[:3],
        1,
    ):
        material_item = item[
            "material"
        ]

        source_path_string = ""

        try:
            _obardo_t_stock = __import__("time").perf_counter()

            source_path_string = save_video(
                material_item.url,
                save_dir=str(stock_dir),
            )

            _obardo_t_stock_saved = __import__("time").perf_counter()

            if not source_path_string:
                continue

            source_candidate_path = Path(
                source_path_string
            )

            if not source_candidate_path.exists():
                continue

            evaluation = (
                evaluate_local_video_candidate(
                    source_candidate_path,
                    beat_text=beat_text,
                    context_text=context_text,
                    visual_query=visual_query,
                    visual_plan=visual_plan,
                    candidate_id=(
                        f"stock-{stock_index}"
                    ),
                    interval_seconds=3.0,
                    max_frames=40,
                    openai_top_k=1,
                    run_openai=False,
                )
            )

            _obardo_t_stock_evaluated = __import__("time").perf_counter()

            logger.info(
                "O Bardo TIMING | stock_candidate="
                f"{stock_index} | "
                f"save={_obardo_t_stock_saved - _obardo_t_stock:.2f}s | "
                f"evaluate={_obardo_t_stock_evaluated - _obardo_t_stock_saved:.2f}s | "
                f"total={_obardo_t_stock_evaluated - _obardo_t_stock:.2f}s"
            )

            # Local OpenCLIP is always available.
            local_score = float(
                evaluation.get(
                    "openclip_score",
                    0.0,
                )
                or 0.0
            )

            openai_score = (
                evaluation.get(
                    "openai_score"
                )
            )

            if (
                openai_score
                is not None
            ):
                # Keep OpenCLIP important while allowing
                # OpenAI to correct broad-but-wrong matches.
                combined_score = (
                    local_score
                    + float(
                        openai_score
                    ) / 100.0
                )

            else:
                combined_score = (
                    local_score
                )

            if evaluation.get(
                "openai_rejected"
            ):
                combined_score -= 2.0

            tournament.append(
                {
                    **item,
                    "local_path": str(
                        source_candidate_path
                    ),
                    "evaluation": evaluation,
                    "combined_score": (
                        combined_score
                    ),
                }
            )

        except Exception as exc:
            logger.warning(
                "O Bardo STOCK ERROR | "
                f"candidate={stock_index} | "
                f"provider={item.get('provider')} | "
                f"{type(exc).__name__}: {exc}"
            )

            provider_errors.append(
                {
                    "provider": item.get(
                        "provider"
                    ),
                    "query": item.get(
                        "query"
                    ),
                    "error": (
                        "visual tournament: "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                }
            )

    if not tournament:
        raise RuntimeError(
            "No stock candidate survived "
            "the visual tournament."
        )

    tournament.sort(
        key=lambda item: item[
            "combined_score"
        ],
        reverse=True,
    )

    # ---------------------------------------------------------
    # OpenAI editorial rejection gate.
    #
    # When OpenAI is active, do not knowingly choose a stock
    # candidate that the editorial judge classified as wrong.
    #
    # If every candidate is rejected, raise a recoverable
    # error so resolve_media_for_beat() continues to YouTube.
    # ---------------------------------------------------------
    openai_active = False

    try:
        from app.services import (
            obardo_openai_editorial,
        )

        openai_active = (
            obardo_openai_editorial
            .is_enabled()
        )

    except Exception:
        openai_active = False

    # ---------------------------------------------------------
    # OpenAI editorial acceptance sweep.
    #
    # Rank locally first, then ask OpenAI to validate candidates
    # one by one until one survives. This avoids throwing away
    # the whole stock route just because the top local candidate
    # was visually strong but historically unsafe for the current
    # policy.
    # ---------------------------------------------------------
    rejection_reasons = []

    if not tournament:
        raise RuntimeError(
            "No stock candidate survived the visual tournament."
        )

    selected = None

    # ---------------------------------------------------------
    # O Bardo Turbo v1 - parallel OpenAI stock finalists.
    #
    # Local OpenCLIP ranking remains authoritative for order.
    # Up to two editorial reviews run concurrently, but final
    # acceptance still happens below in original rank order.
    # ---------------------------------------------------------
    stock_openai_prefetch = {}

    if openai_active:
        from concurrent.futures import ThreadPoolExecutor

        stock_openai_finalists = list(
            enumerate(
                tournament[:2],
                1,
            )
        )

        def _evaluate_stock_openai_finalist(
            ranked_candidate,
        ):
            (
                prefetch_rank,
                prefetch_item,
            ) = ranked_candidate

            prefetch_local_evaluation = (
                prefetch_item.get(
                    "evaluation"
                )
                or {}
            )

            try:
                prefetch_path = Path(
                    prefetch_item[
                        "local_path"
                    ]
                )

                prefetch_evaluation = (
                    evaluate_local_video_candidate(
                        prefetch_path,
                        beat_text=beat_text,
                        context_text=context_text,
                        visual_query=visual_query,
                        visual_plan=visual_plan,
                        candidate_id=(
                            f"stock-winner-{prefetch_rank}"
                        ),
                        interval_seconds=3.0,
                        max_frames=40,
                        openai_top_k=1,
                        run_openai=True,
                        precomputed_local=(
                            prefetch_local_evaluation
                        ),
                    )
                )

                return (
                    prefetch_rank,
                    prefetch_evaluation,
                    None,
                )

            except Exception as exc:
                return (
                    prefetch_rank,
                    None,
                    exc,
                )

        if stock_openai_finalists:
            with ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                stock_openai_results = list(
                    executor.map(
                        _evaluate_stock_openai_finalist,
                        stock_openai_finalists,
                    )
                )

            for (
                prefetch_rank,
                prefetch_evaluation,
                prefetch_error,
            ) in stock_openai_results:
                if (
                    isinstance(
                        prefetch_evaluation,
                        dict,
                    )
                    and prefetch_evaluation
                ):
                    stock_openai_prefetch[
                        prefetch_rank
                    ] = (
                        prefetch_evaluation
                    )

                    logger.info(
                        "O Bardo TURBO STOCK OPENAI "
                        "PARALLEL ADD | rank={}",
                        prefetch_rank,
                    )

                elif prefetch_error is not None:
                    logger.warning(
                        "O Bardo TURBO STOCK OPENAI "
                        "PARALLEL FAILED | rank={} | {}",
                        prefetch_rank,
                        prefetch_error,
                    )

    for candidate_rank, candidate_item in enumerate(
        tournament[:2],
        1,
    ):
        candidate_evaluation = (
            candidate_item.get("evaluation")
            or {}
        )

        if openai_active:
            prefetched_evaluation = (
                stock_openai_prefetch.get(
                    candidate_rank
                )
            )

            if prefetched_evaluation:
                candidate_evaluation = (
                    prefetched_evaluation
                )

                logger.info(
                    "O Bardo TURBO STOCK OPENAI "
                    "PARALLEL HIT | rank={}",
                    candidate_rank,
                )

            else:
                candidate_path = Path(
                    candidate_item["local_path"]
                )

                candidate_evaluation = (
                    evaluate_local_video_candidate(
                        candidate_path,
                        beat_text=beat_text,
                        context_text=context_text,
                        visual_query=visual_query,
                        visual_plan=visual_plan,
                        candidate_id=(
                            f"stock-winner-{candidate_rank}"
                        ),
                        interval_seconds=3.0,
                        max_frames=40,
                        openai_top_k=1,
                        run_openai=True,
                        precomputed_local=candidate_evaluation,
                    )
                )

            candidate_item[
                "evaluation"
            ] = candidate_evaluation

            local_score = float(
                candidate_evaluation.get(
                    "openclip_score",
                    0.0,
                )
                or 0.0
            )

            openai_score = (
                candidate_evaluation.get(
                    "openai_score"
                )
            )

            if openai_score is not None:
                candidate_item[
                    "combined_score"
                ] = (
                    local_score
                    + float(openai_score)
                    / 100.0
                )
            else:
                candidate_item[
                    "combined_score"
                ] = local_score

            openai_best = (
                candidate_evaluation.get(
                    "openai_best"
                )
                or {}
            )

            editorial_decision = str(
                openai_best.get(
                    "decision"
                )
                or ""
            ).strip().lower()

            if (
                candidate_evaluation.get(
                    "openai_rejected"
                )
                or editorial_decision == "weak"
            ):
                rejection_reasons.append(
                    "provider="
                    f"{candidate_item.get('provider')}; "
                    "query="
                    f"{candidate_item.get('query')}; "
                    "openclip_score="
                    f"{candidate_evaluation.get('openclip_score')}; "
                    "openai_score="
                    f"{candidate_evaluation.get('openai_score')}; "
                    "reason="
                    f"{candidate_evaluation.get('openai_best', {}).get('reason')}"
                )
                continue

        selected = candidate_item
        break

    if selected is None:
        raise RuntimeError(
            "No stock candidate passed OpenAI editorial review. "
            + " | ".join(rejection_reasons[:3])
        )

    material = selected[
        "material"
    ]

    source_path = Path(
        selected[
            "local_path"
        ]
    )

    selected_evaluation = (
        selected.get(
            "evaluation"
        )
        or {}
    )

    source_duration = float(
        getattr(
            material,
            "duration",
            0,
        )
        or 0
    )

    usable_window = max(
        source_duration
        - duration_seconds,
        0.0,
    )

    best_timestamp = float(
        selected_evaluation.get(
            "best_timestamp",
            0.0,
        )
        or 0.0
    )

    # Center the final stock clip around the
    # visually strongest frame.
    clip_start = max(
        0.0,
        best_timestamp
        - (
            duration_seconds
            / 2.0
        ),
    )

    clip_start = min(
        clip_start,
        usable_window,
    )

    output_dir = (
        Path("storage")
        / "obardo_temp"
        / "clips"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_name:
        filename = str(
            output_name
        )

    else:
        safe_beat = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            beat_text,
        ).strip("_")

        filename = (
            f"stock-"
            f"{safe_beat[:50] or 'beat'}"
            ".mp4"
        )

    output_path = (
        output_dir
        / filename
    )

    final_clip = None

    try:
        final_clip = extract_hq_clip(
            source_path,
            output_path,
            start_seconds=clip_start,
            duration_seconds=duration_seconds,
            width=target_width,
            height=target_height,
            fps=30,
            keep_source_audio=False,
        )

    finally:
        if (
            cleanup_source
            and source_path.exists()
        ):
            try:
                source_path.unlink()
            except OSError:
                pass

    source_info = dict(
        getattr(
            material,
            "source_info",
            None,
        )
        or {}
    )

    return {
        "type": "resolved_stock_video_beat",
        "provider": selected[
            "provider"
        ],
        "beat_text": beat_text,
        "search_query": selected[
            "query"
        ],
        "queries": queries,
        "source_page": source_info.get(
            "source_page"
        ),
        "source_url": material.url,
        "source_info": source_info,
        "source_duration": (
            source_duration
        ),
        "clip_start_seconds": (
            clip_start
        ),
        "clip_duration_seconds": (
            duration_seconds
        ),
        "final_clip": final_clip,
        "candidate_count": len(
            candidate_pool
        ),
        "visual_query": visual_query,
        "openclip_score": (
            selected_evaluation.get(
                "openclip_score"
            )
        ),
        "best_timestamp": (
            selected_evaluation.get(
                "best_timestamp"
            )
        ),
        "openai_editorial": (
            selected_evaluation.get(
                "openai_best"
            )
        ),
        "combined_score": (
            selected.get(
                "combined_score"
            )
        ),
        "tournament": [
            {
                "provider": item.get(
                    "provider"
                ),
                "query": item.get(
                    "query"
                ),
                "combined_score": (
                    item.get(
                        "combined_score"
                    )
                ),
                "openclip_score": (
                    item.get(
                        "evaluation",
                        {},
                    ).get(
                        "openclip_score"
                    )
                ),
                "openai_score": (
                    item.get(
                        "evaluation",
                        {},
                    ).get(
                        "openai_score"
                    )
                ),
                "openai_rejected": (
                    item.get(
                        "evaluation",
                        {},
                    ).get(
                        "openai_rejected"
                    )
                ),
            }
            for item in tournament
        ],
        "provider_errors": (
            provider_errors
        ),
    }

# ------------------------------------------------------------
# O Bardo Turbo v1 - Wikimedia editorial evaluation cache.
#
# Reuses OpenAI evaluation only when the same Wikimedia asset
# is judged against the same authoritative narration/context.
# Search/ranking and final quality gates remain untouched.
# ------------------------------------------------------------
_OBARDO_WIKIMEDIA_EDITORIAL_CACHE = {}

_OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE_LOADED = False
_OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE = {}

_OBARDO_CACHE_DIR = (
    PROJECT_ROOT
    / "storage"
    / "obardo_cache"
)

_OBARDO_WIKIMEDIA_EDITORIAL_CACHE_FILE = (
    _OBARDO_CACHE_DIR
    / "wikimedia_editorial.json"
)


def _obardo_wikimedia_editorial_disk_key(
    cache_key,
) -> str:
    serialized = json.dumps(
        list(cache_key),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return (
        __import__("hashlib")
        .sha256(
            serialized.encode(
                "utf-8",
                errors="ignore",
            )
        )
        .hexdigest()
    )


def _obardo_load_wikimedia_editorial_disk_cache():
    global _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE_LOADED
    global _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE

    if _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE_LOADED:
        return

    _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE_LOADED = True
    _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE = {}

    try:
        if not _OBARDO_WIKIMEDIA_EDITORIAL_CACHE_FILE.exists():
            return

        payload = json.loads(
            _OBARDO_WIKIMEDIA_EDITORIAL_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(payload, dict):
            return

        entries = payload.get("entries")

        if not isinstance(entries, dict):
            return

        _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE = entries

    except Exception:
        # Fail-open:
        # malformed/unavailable cache must never stop production.
        _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE = {}


def _obardo_wikimedia_editorial_cache_get(
    cache_key,
):
    cached = (
        _OBARDO_WIKIMEDIA_EDITORIAL_CACHE.get(
            cache_key
        )
    )

    if cached is not None:
        return __import__("copy").deepcopy(
            cached
        )

    _obardo_load_wikimedia_editorial_disk_cache()

    try:
        disk_key = (
            _obardo_wikimedia_editorial_disk_key(
                cache_key
            )
        )

        entry = (
            _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE.get(
                disk_key
            )
        )

        if not isinstance(entry, dict):
            return None

        stored_key = entry.get("key")

        if stored_key != list(cache_key):
            return None

        evaluation = entry.get("evaluation")

        if not isinstance(evaluation, dict):
            return None

        evaluation = __import__(
            "copy"
        ).deepcopy(
            evaluation
        )

        _OBARDO_WIKIMEDIA_EDITORIAL_CACHE[
            cache_key
        ] = __import__(
            "copy"
        ).deepcopy(
            evaluation
        )

        return evaluation

    except Exception:
        return None


def _obardo_wikimedia_editorial_cache_set(
    cache_key,
    evaluation,
) -> None:
    if not isinstance(evaluation, dict):
        return

    if not evaluation:
        return

    evaluation_copy = __import__(
        "copy"
    ).deepcopy(
        evaluation
    )

    _OBARDO_WIKIMEDIA_EDITORIAL_CACHE[
        cache_key
    ] = evaluation_copy

    try:
        _obardo_load_wikimedia_editorial_disk_cache()

        disk_key = (
            _obardo_wikimedia_editorial_disk_key(
                cache_key
            )
        )

        _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE[
            disk_key
        ] = {
            "key": list(cache_key),
            "evaluation": __import__(
                "copy"
            ).deepcopy(
                evaluation_copy
            ),
        }

        _OBARDO_CACHE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "version": 1,
            "entries": (
                _OBARDO_WIKIMEDIA_EDITORIAL_DISK_CACHE
            ),
        }

        temporary_file = (
            _OBARDO_WIKIMEDIA_EDITORIAL_CACHE_FILE
            .with_suffix(".json.tmp")
        )

        temporary_file.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temporary_file.replace(
            _OBARDO_WIKIMEDIA_EDITORIAL_CACHE_FILE
        )

    except Exception:
        # Fail-open:
        # disk persistence must never invalidate RAM cache
        # or interrupt media resolution.
        pass


def resolve_historical_image_for_beat(
    beat_text: str,
    *,
    search_query: str | None = None,
    duration_seconds: int = 4,
    output_directory: str | None = None,
    context_text: str | None = None,
    avoid_media_keys: list[str] | None = None,
    visual_plan: dict | None = None,
) -> dict:
    """
    Resolve a historically specific Wikimedia image for one narration beat.

    Wikimedia search/ranking remains owned by material.py.
    This layer adds a final identity/context check before downloading.
    """
    import html
    import re
    import unicodedata
    from pathlib import Path

    from app.models.schema import VideoAspect
    from app.services.material import (
        search_images_wikimedia,
        _download_wikimedia_image,
    )

    def _download_historical_candidate(
        candidate,
        *,
        save_dir: str,
    ):
        """
        Download dispatcher for historical-image providers.

        Wikimedia keeps its dedicated downloader.
        Other providers use ordinary HTTP downloading.
        """
        import hashlib
        import io
        import os

        import requests
        from PIL import Image

        candidate_url = str(
            getattr(
                candidate,
                "url",
                "",
            )
            or ""
        ).strip()

        if not candidate_url:
            return None

        source_info = dict(
            getattr(
                candidate,
                "source_info",
                None,
            )
            or {}
        )

        provider = str(
            source_info.get("provider")
            or getattr(
                candidate,
                "provider",
                "",
            )
            or ""
        ).strip().lower()

        # Wikimedia retains its specialized downloader because of
        # Commons/CDN rate-limit handling.
        if provider in {
            "",
            "wikimedia",
            "wikimedia_commons",
            "commons",
        }:
            return _download_wikimedia_image(
                image_url=candidate_url,
                save_dir=save_dir,
            )

        headers = {
            "User-Agent": (
                "O-Bardo-MoneyPrinterTurbo/1.0 "
                "(historical documentary media downloader)"
            ),
            "Accept": "image/*,*/*;q=0.8",
        }

        try:
            response = requests.get(
                candidate_url,
                headers=headers,
                timeout=(15, 60),
            )

            if response.status_code == 429:
                logger.warning(
                    "O Bardo HISTORICAL DOWNLOAD 429 | "
                    f"provider={provider!r} | "
                    f"url={candidate_url!r}"
                )
                return None

            response.raise_for_status()

            content_type = str(
                response.headers.get(
                    "Content-Type",
                    "",
                )
                or ""
            ).lower()

            if (
                content_type
                and "image" not in content_type
            ):
                logger.warning(
                    "O Bardo HISTORICAL DOWNLOAD REJECT | "
                    f"provider={provider!r} | "
                    f"content_type={content_type!r} | "
                    f"url={candidate_url!r}"
                )
                return None

            raw = response.content

            if not raw:
                return None

            image = Image.open(
                io.BytesIO(raw)
            )

            image.load()

            if (
                image.width < 320
                or image.height < 240
            ):
                logger.warning(
                    "O Bardo HISTORICAL DOWNLOAD TOO SMALL | "
                    f"provider={provider!r} | "
                    f"size={image.width}x{image.height}"
                )
                return None

            if image.mode not in (
                "RGB",
                "RGBA",
            ):
                image = image.convert(
                    "RGB"
                )

            os.makedirs(
                save_dir,
                exist_ok=True,
            )

            digest = hashlib.sha256(
                candidate_url.encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest()[:32]

            safe_provider = (
                provider
                or "historical"
            ).replace(
                "/",
                "_",
            )

            output_path = os.path.join(
                save_dir,
                f"{safe_provider}-"
                f"{digest}.jpg",
            )

            if image.mode == "RGBA":
                background = Image.new(
                    "RGB",
                    image.size,
                    "white",
                )

                background.paste(
                    image,
                    mask=image.getchannel(
                        "A"
                    ),
                )

                image = background

            image.save(
                output_path,
                format="JPEG",
                quality=94,
                optimize=True,
            )

            logger.info(
                "O Bardo HISTORICAL DOWNLOAD OK | "
                f"provider={provider!r} | "
                f"path={output_path!r}"
            )

            return output_path

        except Exception as exc:
            logger.warning(
                "O Bardo HISTORICAL DOWNLOAD FAILED | "
                f"provider={provider!r} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
            return None


    def normalize(value: str) -> str:
        value = html.unescape(
            str(value or "")
        ).lower()

        value = re.sub(
            r"<[^>]+>",
            " ",
            value,
        )

        value = unicodedata.normalize(
            "NFKD",
            value,
        )

        value = "".join(
            char
            for char in value
            if not unicodedata.combining(char)
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

        return value

    from loguru import logger

    beat_text = str(beat_text or "").strip()

    director_visual_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    if not beat_text:
        raise ValueError("beat_text is required.")

    search_query = str(
        search_query or ""
    ).strip()

    identity = detect_historical_identity(
        f"{beat_text} {context_text or ''}"
    )

    query_candidates = (
        build_wikimedia_query_candidates(
            beat_text,
            identity=identity,
            context_text=context_text,
        )
    )

    # O Bardo semantics:
    # narration is authoritative; the planner query is retrieval-only.
    if search_query:
        query_candidates = list(
            dict.fromkeys(
                [search_query]
                + list(query_candidates)
            )
        )

    candidates = []
    search_term = None
    attempted_queries = []
    successful_queries = []
    seen_candidate_urls = set()

    # ------------------------------------------------------------
    # O BARDO MULTISOURCE HISTORICAL CANDIDATES
    # ------------------------------------------------------------
    #
    # Normalize non-Wikimedia providers into the same lightweight
    # interface already consumed by the existing historical ranker:
    #
    #     candidate.url
    #     candidate.source_info
    #
    # This lets Met/Openverse/etc participate in the existing
    # historical ranking and OpenAI editorial tournament without
    # duplicating that logic.
    #
    try:
        from types import SimpleNamespace

        from app.services.obardo_historical_providers import (
            search_historical_providers,
        )

        multisource_query = ""

        if query_candidates:
            multisource_query = str(
                query_candidates[0]
                or ""
            ).strip()

        if not multisource_query:
            multisource_query = str(
                search_query
                or beat_text
                or ""
            ).strip()

        if multisource_query:
            multisource_results = (
                search_historical_providers(
                    multisource_query,
                    limit_per_provider=12,
                )
            )

            multisource_added = 0

            for item in multisource_results:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                image_url = str(
                    item.get("image_url")
                    or ""
                ).strip()

                if not image_url:
                    continue

                url_key = (
                    image_url
                    .split("?")[0]
                    .strip()
                )

                if (
                    url_key
                    and url_key
                    in seen_candidate_urls
                ):
                    continue

                if url_key:
                    seen_candidate_urls.add(
                        url_key
                    )

                provider_name = str(
                    item.get("provider")
                    or "historical_provider"
                ).strip()

                provider_score = float(
                    item.get(
                        "provider_relevance_score",
                        0.0,
                    )
                    or 0.0
                )

                source_info = {
                    "provider": provider_name,
                    "title": item.get("title"),
                    "description": item.get(
                        "description"
                    ),
                    "creator": item.get("creator"),
                    "license": item.get("license"),
                    "license_url": item.get(
                        "license_url"
                    ),
                    "source_page": item.get(
                        "source_url"
                    ),
                    "source_url": item.get(
                        "source_url"
                    ),
                    "thumbnail_url": item.get(
                        "thumbnail_url"
                    ),
                    "matched_query": item.get(
                        "matched_query"
                    )
                    or multisource_query,

                    # Feed the provider pre-ranking into the existing
                    # historical semantic/base score.
                    "semantic_score": (
                        provider_score
                    ),

                    "provider_relevance_score": (
                        provider_score
                    ),
                    "provider_relevance_reasons": (
                        item.get(
                            "provider_relevance_reasons"
                        )
                    ),
                    "provider_subject_match": (
                        item.get(
                            "provider_subject_match"
                        )
                    ),
                    "historical_provider": True,
                }

                candidate = SimpleNamespace(
                    url=image_url,
                    source_info=source_info,
                    provider=provider_name,
                )

                candidates.append(
                    candidate
                )

                multisource_added += 1

            logger.info(
                "O Bardo HISTORICAL MULTISOURCE "
                "POOL ADD | "
                f"query={multisource_query!r} | "
                f"added={multisource_added} | "
                f"pool={len(candidates)}"
            )

    except Exception as multisource_exc:
        # Multi-source is additive. Wikimedia remains available even
        # if one of the external providers fails.
        logger.warning(
            "O Bardo HISTORICAL MULTISOURCE "
            "POOL FAILED | "
            f"{type(multisource_exc).__name__}: "
            f"{multisource_exc}"
        )

    # Search multiple intent-specific Wikimedia queries instead
    # of stopping as soon as the first query returns something.
    #
    # This gives the editorial tournament a broader but still
    # historically focused candidate pool.
    specific_queries = query_candidates[:7]
    fallback_queries = query_candidates[7:]

    for query_candidate in specific_queries:
        attempted_queries.append(
            query_candidate
        )

        try:
            found = search_images_wikimedia(
                search_term=query_candidate,
                minimum_duration=max(
                    int(duration_seconds),
                    1,
                ),
                video_aspect=VideoAspect.landscape,
            )

        except Exception:
            found = []

        if not found:
            continue

        if search_term is None:
            search_term = query_candidate

        successful_queries.append(
            query_candidate
        )

        for candidate in found:
            candidate_url = str(
                getattr(
                    candidate,
                    "url",
                    "",
                )
                or ""
            ).strip()

            if (
                candidate_url
                and candidate_url
                in seen_candidate_urls
            ):
                continue

            if candidate_url:
                seen_candidate_urls.add(
                    candidate_url
                )

            candidates.append(
                candidate
            )

        # Once Wikimedia has already supplied a sufficiently large
        # unique candidate pool, stop expanding additional high-level
        # query variations. Downstream ranking/editorial review does
        # not benefit from repeatedly querying Commons here.
        if len(candidates) >= 12:
            logger.info(
                "O Bardo TURBO WIKIMEDIA OUTER EARLY STOP | "
                f"candidates={len(candidates)} | "
                f"last_query={query_candidate!r}"
            )
            break

    # Broad/raw narration fallbacks are used only when all
    # intent-specific queries failed to find anything.
    if not candidates:
        for query_candidate in fallback_queries:
            attempted_queries.append(
                query_candidate
            )

            try:
                found = search_images_wikimedia(
                    search_term=query_candidate,
                    minimum_duration=max(
                        int(duration_seconds),
                        1,
                    ),
                    video_aspect=VideoAspect.landscape,
                )

            except Exception:
                found = []

            if not found:
                continue

            if search_term is None:
                search_term = query_candidate

            successful_queries.append(
                query_candidate
            )

            for candidate in found:
                candidate_url = str(
                    getattr(
                        candidate,
                        "url",
                        "",
                    )
                    or ""
                ).strip()

                if (
                    candidate_url
                    and candidate_url
                    in seen_candidate_urls
                ):
                    continue

                if candidate_url:
                    seen_candidate_urls.add(
                        candidate_url
                    )

                candidates.append(
                    candidate
                )

            if candidates:
                break

    if not candidates:
        raise RuntimeError(
            "No Wikimedia candidates found. "
            f"Queries tried: {attempted_queries}"
        )

    roman_context = any(
        term in normalize(
            f"{beat_text} {search_term}"
        )
        for term in [
            "roma",
            "rome",
            "roman",
            "romano",
            "romana",
        ]
    )

    # ---------------------------------------------------------
    # O Bardo 4.16B - human-action metadata evidence
    #
    # Human-action beats should give a modest ranking advantage
    # to candidates whose OWN visual metadata explicitly describes
    # people/action.
    #
    # Important:
    # - matched_query must NOT count as evidence;
    # - Internet Archive scanned-book OCR must NOT count as visual
    #   evidence because surrounding page text frequently mentions
    #   spectators/people even when the extracted image is only a
    #   plan, ruin or architectural diagram.
    # ---------------------------------------------------------
    human_action_text = normalize(
        f"{beat_text} {context_text or ''}"
    )

    plan_human_action_required = bool(
        director_visual_plan.get(
            "human_action_required"
        )
    )

    human_action_context = (
        plan_human_action_required
        or any(
            marker in human_action_text
            for marker in (
            "espectador",
            "espectadores",
            "publico",
            "plateia",
            "multidao",
            "banhista",
            "banhistas",
            "vendedor",
            "vendedores",
            "soldado",
            "soldados",
            "cidadao",
            "cidadaos",
            "pessoas",
            "crowd",
            "spectator",
            "spectators",
            "audience",
            "bathers",
            "merchant",
            "merchants",
            "vendors",
            "soldier",
            "soldiers",
            "citizens",
            "people",
            )
        )
    )

    market_context = any(
        marker in human_action_text
        for marker in (
            "mercado",
            "mercados",
            "comercio",
            "comerciante",
            "comerciantes",
            "vendedor",
            "vendedores",
            "mercadoria",
            "mercadorias",
            "market",
            "markets",
            "merchant",
            "merchants",
            "vendor",
            "vendors",
            "trade",
            "trading",
            "goods",
            "shop",
            "shops",
        )
    )

    ranked = []

    for index, candidate in enumerate(
        candidates
    ):
        source_info = dict(
            getattr(
                candidate,
                "source_info",
                None,
            )
            or {}
        )

        title = normalize(
            source_info.get("title")
        )

        description = normalize(
            source_info.get("description")
        )

        matched_query = normalize(
            source_info.get("matched_query")
        )

        haystack = (
            f"{title} "
            f"{description} "
            f"{matched_query}"
        )

        base_score = float(
            source_info.get(
                "semantic_score",
                0,
            )
            or 0
        )

        adjustment = 0.0
        reasons = []

        # Human-action evidence must come from actual candidate metadata,
        # never from the retrieval query.
        #
        # Wikimedia Internet Archive book-image descriptions embed OCR
        # from text before/after the extracted image. Those words are not
        # reliable evidence of what is visually present.
        description_is_scanned_book_ocr = any(
            marker in description
            for marker in (
                "text appearing before image",
                "text appearing after image",
                "view book page",
                "about this book",
                "all images from book",
                "note about images",
                "book viewer",
                "digitizing sponsor",
            )
        )

        actual_visual_metadata = (
            f"{title} {description}"
        )

        human_visual_markers = (
            "crowd",
            "spectator",
            "spectators",
            "audience",
            "people",
            "person",
            "persons",
            "citizen",
            "citizens",
            "vestal",
            "vestals",
            "virgin",
            "virgins",
            "gladiator",
            "gladiators",
            "bather",
            "bathers",
            "merchant",
            "merchants",
            "vendor",
            "vendors",
            "soldier",
            "soldiers",
            "seated",
            "sitting",
            "front-row seats",
        )

        human_visual_matches = [
            marker
            for marker in human_visual_markers
            if marker in actual_visual_metadata
        ]

        if (
            human_action_context
            and not description_is_scanned_book_ocr
            and human_visual_matches
        ):
            adjustment += 16.0
            reasons.append(
                "+16 human_action_visual_metadata:"
                + ",".join(
                    human_visual_matches[:4]
                )
            )

        # Market/trade narration needs candidate-specific evidence.
        #
        # Evidence in the candidate TITLE is much stronger than generic
        # contextual wording buried in the Wikimedia description.
        if market_context:
            market_title_markers = (
                "market",
                "marketplace",
                "merchant",
                "merchants",
                "vendor",
                "vendors",
                "seller",
                "sellers",
                "shop",
                "shops",
                "stall",
                "stalls",
                "trade",
                "trading",
                "commerce",
                "commercial",
                "goods",
            )

            market_description_markers = (
                "market",
                "marketplace",
                "merchant",
                "merchants",
                "vendor",
                "vendors",
                "seller",
                "sellers",
                "shop",
                "shops",
                "stall",
                "stalls",
                "trade",
                "trading",
                "commerce",
                "commercial",
                "goods",
            )

            title_market_matches = [
                marker
                for marker in market_title_markers
                if marker in title
            ]

            description_market_matches = [
                marker
                for marker in market_description_markers
                if marker in description
            ]

            if title_market_matches:
                adjustment += 32.0
                reasons.append(
                    "+32 market_title_metadata:"
                    + ",".join(
                        title_market_matches[:4]
                    )
                )

            elif (
                not description_is_scanned_book_ocr
                and description_market_matches
            ):
                adjustment += 10.0
                reasons.append(
                    "+10 market_description_metadata:"
                    + ",".join(
                        description_market_matches[:4]
                    )
                )

            unrelated_market_markers = (
                "saint ",
                "sarcophagus",
                "tombstone",
                "cemetery",
                "funerary",
                "funeral",
            )

            unrelated_title_matches = [
                marker.strip()
                for marker in unrelated_market_markers
                if marker in title
            ]

            unrelated_description_matches = [
                marker.strip()
                for marker in unrelated_market_markers
                if marker in description
            ]

            if unrelated_title_matches:
                adjustment -= 28.0
                reasons.append(
                    "-28 market_unrelated_title:"
                    + ",".join(
                        unrelated_title_matches[:3]
                    )
                )

            elif (
                not description_is_scanned_book_ocr
                and unrelated_description_matches
            ):
                adjustment -= 12.0
                reasons.append(
                    "-12 market_unrelated_description:"
                    + ",".join(
                        unrelated_description_matches[:3]
                    )
                )

        if roman_context:
            if "roman" in description:
                adjustment += 12.0
                reasons.append(
                    "+12 explicit_roman_description"
                )

            elif "roman" in title:
                adjustment += 8.0
                reasons.append(
                    "+8 explicit_roman_title"
                )

            if (
                "greek" in description
                and "roman" not in description
            ):
                adjustment -= 20.0
                reasons.append(
                    "-20 greek_not_roman"
                )

        canonical = normalize(
            identity.get("canonical")
        )

        if (
            canonical
            and canonical in haystack
        ):
            adjustment += 8.0
            reasons.append(
                "+8 canonical_identity"
            )

        # Prefer straightforward reusable licensing when scores are close.
        license_name = normalize(
            source_info.get("license")
        )

        if license_name == "cc0":
            adjustment += 2.0
            reasons.append("+2 cc0")

        elif "public domain" in license_name:
            adjustment += 2.0
            reasons.append("+2 public_domain")

        final_score = (
            base_score
            + adjustment
        )

        ranked.append(
            {
                "candidate": candidate,
                "original_rank": index + 1,
                "base_score": base_score,
                "adjustment": adjustment,
                "final_score": final_score,
                "reasons": reasons,
            }
        )

    ranked.sort(
        key=lambda item: (
            item["final_score"],
            item["base_score"],
            -item["original_rank"],
        ),
        reverse=True,
    )

    for debug_rank, debug_item in enumerate(
        ranked[:8],
        1,
    ):
        debug_source_info = dict(
            getattr(
                debug_item["candidate"],
                "source_info",
                None,
            )
            or {}
        )

        logger.info(
            "O Bardo TURBO WIKIMEDIA FINAL RANK | "
            f"rank={debug_rank} | "
            f"final={debug_item['final_score']:.1f} | "
            f"base={debug_item['base_score']:.1f} | "
            f"adjustment={debug_item['adjustment']:.1f} | "
            f"title={debug_source_info.get('title')!r} | "
            f"reasons={debug_item['reasons']!r}"
        )

    avoided_keys = set(
        avoid_media_keys
        or []
    )

    # Strict global dedup.
    ranked = [
        item
        for item in ranked
        if (
            "wikimedia:"
            + str(
                item["candidate"].url
            ).strip()
        )
        not in avoided_keys
    ]

    if not ranked:
        raise RuntimeError(
            "All Wikimedia candidates were already used."
        )

    try:
        from app.services import (
            obardo_openai_editorial,
        )

        openai_enabled = (
            obardo_openai_editorial
            .is_enabled()
        )

    except Exception:
        obardo_openai_editorial = None
        openai_enabled = False

    save_dir = Path(
        output_directory
        or "storage/obardo_temp/images"
    )

    save_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    rejection_reasons = []

    winner_info = None
    winner = None
    filepath = None
    openai_evaluation = {}

    # Wikimedia editorial sweep.
    #
    # Later paintings, engravings and illustrations are allowed
    # when they genuinely depict the narrated historical period.
    # Images representing a different period are rejected.
    #
    # O Bardo Turbo v1 - Wikimedia Parallel Editorial Prefetch.
    #
    # Evaluate uncached finalists concurrently with a conservative
    # two-worker pool. Results are written into the existing cache
    # on the main thread. The original ranked sweep below remains
    # authoritative, preserving candidate priority and all gates.
    # Files successfully downloaded during editorial prefetch are
    # reused by the main tournament. This prevents downloading the
    # same Wikimedia candidate twice.
    wikimedia_prefetched_files = {}

    if openai_enabled:
        try:
            from concurrent.futures import ThreadPoolExecutor

            historical_visual_plan_prefetch = {
                "fallback_mode": (
                    "historical_image_review"
                ),
                "subject": beat_text,
                "period": str(
                    context_text
                    or ""
                ),
                "location": str(
                    context_text
                    or ""
                ),
                "must_show": beat_text,
            }

            prefetch_jobs = []

            for prefetch_rank, prefetch_item in enumerate(
                ranked[:6],
                1,
            ):
                prefetch_candidate = prefetch_item[
                    "candidate"
                ]

                prefetch_filepath = (
                    _download_historical_candidate(
                        prefetch_candidate,
                        save_dir=str(save_dir),
                    )
                )

                if not prefetch_filepath:
                    continue

                prefetch_url_key = str(
                    getattr(
                        prefetch_candidate,
                        "url",
                        "",
                    )
                    or ""
                ).split("?")[0].strip()

                if prefetch_url_key:
                    wikimedia_prefetched_files[
                        prefetch_url_key
                    ] = prefetch_filepath

                prefetch_cache_key = (
                    str(
                        getattr(
                            prefetch_candidate,
                            "url",
                            "",
                        )
                        or prefetch_filepath
                    ).split("?")[0].strip(),
                    " ".join(
                        str(
                            beat_text
                            or ""
                        ).lower().split()
                    ),
                    " ".join(
                        str(
                            context_text
                            or ""
                        ).lower().split()
                    ),
                    str(
                        historical_visual_plan_prefetch.get(
                            "fallback_mode"
                        )
                        or ""
                    ),
                )

                if (
                    _obardo_wikimedia_editorial_cache_get(
                        prefetch_cache_key
                    )
                    is not None
                ):
                    continue

                prefetch_jobs.append(
                    (
                        prefetch_rank,
                        prefetch_candidate,
                        prefetch_filepath,
                        prefetch_cache_key,
                    )
                )

            def _evaluate_wikimedia_prefetch(job):
                (
                    prefetch_rank,
                    prefetch_candidate,
                    prefetch_filepath,
                    prefetch_cache_key,
                ) = job

                try:
                    evaluation = (
                        obardo_openai_editorial
                        .evaluate_image_candidate(
                            prefetch_filepath,
                            beat_text=beat_text,
                            context_text=context_text,
                            visual_plan=(
                                historical_visual_plan_prefetch
                            ),
                        )
                        or {}
                    )

                except Exception:
                    evaluation = {}

                return (
                    prefetch_rank,
                    prefetch_candidate,
                    prefetch_cache_key,
                    evaluation,
                )

            if prefetch_jobs:
                with ThreadPoolExecutor(
                    max_workers=2
                ) as executor:
                    prefetch_results = list(
                        executor.map(
                            _evaluate_wikimedia_prefetch,
                            prefetch_jobs,
                        )
                    )

                for (
                    prefetch_rank,
                    prefetch_candidate,
                    prefetch_cache_key,
                    prefetch_evaluation,
                ) in prefetch_results:
                    if not prefetch_evaluation:
                        continue

                    _obardo_wikimedia_editorial_cache_set(
                        prefetch_cache_key,
                        prefetch_evaluation,
                    )

                    prefetch_source_info = dict(
                        getattr(
                            prefetch_candidate,
                            "source_info",
                            None,
                        )
                        or {}
                    )

                    logger.info(
                        "O Bardo TURBO WIKIMEDIA PARALLEL "
                        "CACHE ADD | "
                        f"rank={prefetch_rank} | "
                        f"title={prefetch_source_info.get('title')!r}"
                    )

        except Exception as prefetch_exc:
            logger.warning(
                "O Bardo TURBO WIKIMEDIA PARALLEL "
                "PREFETCH FAILED | "
                f"{type(prefetch_exc).__name__}: "
                f"{prefetch_exc}"
            )

    for candidate_rank, ranked_item in enumerate(
        ranked[:12],
        1,
    ):
        candidate = ranked_item[
            "candidate"
        ]

        candidate_source_info = dict(
            getattr(
                candidate,
                "source_info",
                None,
            )
            or {}
        )

        candidate_url_key = str(
            getattr(
                candidate,
                "url",
                "",
            )
            or ""
        ).split("?")[0].strip()

        candidate_filepath = (
            wikimedia_prefetched_files.get(
                candidate_url_key
            )
        )

        if candidate_filepath:
            logger.info(
                "O Bardo TURBO WIKIMEDIA PREFETCH REUSE | "
                f"rank={candidate_rank} | "
                f"url={candidate_url_key!r}"
            )
        else:
            candidate_filepath = (
                _download_historical_candidate(
                    candidate,
                    save_dir=str(save_dir),
                )
            )

        if not candidate_filepath:
            rejection_reasons.append(
                f"rank={candidate_rank}; "
                "reason=download_failed"
            )
            continue

        candidate_evaluation = {}

        if openai_enabled:
            historical_visual_plan = {
                "fallback_mode": (
                    "historical_image_review"
                ),
                "subject": beat_text,
                "period": str(
                    context_text
                    or ""
                ),
                "location": str(
                    context_text
                    or ""
                ),
                "must_show": beat_text,
            }

            cache_key = (
                str(
                    getattr(
                        candidate,
                        "url",
                        "",
                    )
                    or candidate_filepath
                ).split("?")[0].strip(),
                " ".join(
                    str(
                        beat_text
                        or ""
                    ).lower().split()
                ),
                " ".join(
                    str(
                        context_text
                        or ""
                    ).lower().split()
                ),
                str(
                    historical_visual_plan.get(
                        "fallback_mode"
                    )
                    or ""
                ),
            )

            cached_evaluation = (
                _obardo_wikimedia_editorial_cache_get(
                    cache_key
                )
            )

            if cached_evaluation is not None:
                candidate_evaluation = (
                    __import__(
                        "copy"
                    ).deepcopy(
                        cached_evaluation
                    )
                )

                logger.info(
                    "O Bardo TURBO WIKIMEDIA EDITORIAL "
                    "CACHE HIT | "
                    f"title={candidate_source_info.get('title')!r}"
                )

            else:
                try:
                    candidate_evaluation = (
                        obardo_openai_editorial
                        .evaluate_image_candidate(
                            candidate_filepath,
                            beat_text=beat_text,
                            context_text=context_text,
                            visual_plan=historical_visual_plan,
                        )
                        or {}
                    )

                except Exception:
                    candidate_evaluation = {}

                if candidate_evaluation:
                    _obardo_wikimedia_editorial_cache_set(
                        cache_key,
                        candidate_evaluation,
                    )

                    logger.info(
                        "O Bardo TURBO WIKIMEDIA EDITORIAL "
                        "CACHE ADD | "
                        f"title={candidate_source_info.get('title')!r}"
                    )

        if candidate_evaluation:
            decision = str(
                candidate_evaluation.get(
                    "decision"
                )
                or ""
            ).strip().lower()

            anachronism = bool(
                candidate_evaluation.get(
                    "anachronism"
                )
            )

            relevance_score = float(
                candidate_evaluation.get(
                    "relevance_score"
                )
                or 0
            )

            historical_score = float(
                candidate_evaluation.get(
                    "historical_score"
                )
                or 0
            )

            documentary_value = float(
                candidate_evaluation.get(
                    "documentary_value"
                )
                or 0
            )

            # Explicit human-action beats need a stronger visual match.
            #
            # A historically correct building, ruin, plan or artifact may be
            # useful as contextual fallback, but it must not satisfy narration
            # that explicitly asks for crowds, spectators, bathers, merchants,
            # soldiers or other visible human activity.
            import unicodedata

            def _normalize_human_action_text(value: str) -> str:
                value = unicodedata.normalize(
                    "NFKD",
                    str(value or "").lower(),
                )
                return "".join(
                    char
                    for char in value
                    if not unicodedata.combining(char)
                )

            human_action_text = _normalize_human_action_text(
                f"{beat_text} {context_text or ''}"
            )

            human_action_required = any(
                marker in human_action_text
                for marker in (
                    "espectador",
                    "espectadores",
                    "publico",
                    "plateia",
                    "multidao",
                    "arquibancada",
                    "arquibancadas",
                    "banhista",
                    "banhistas",
                    "vendedor",
                    "vendedores",
                    "comerciante",
                    "comerciantes",
                    "soldado",
                    "soldados",
                    "cidadao",
                    "cidadaos",
                    "pessoas",
                    "crowd",
                    "spectator",
                    "spectators",
                    "audience",
                    "bathers",
                    "merchant",
                    "merchants",
                    "vendors",
                    "soldier",
                    "soldiers",
                    "citizens",
                    "people",
                )
            )

            if human_action_required:
                # Preferred case: the image actually depicts the
                # narrated human action.
                strong_action_match = (
                    relevance_score >= 85
                    and historical_score >= 50
                    and documentary_value >= 65
                )

                # Documentary contextual fallback:
                #
                # If motion/video could not provide a safe scene,
                # allow a historically strong establishing/location
                # image even when it does not literally depict the
                # narrated human action.
                #
                # Example: an authentic Coliseum image may illustrate
                # narration about crowds approaching the amphitheatre.
                contextual_historical_fallback = (
                    decision == "weak"
                    and not anachronism
                    and relevance_score >= 50
                    and historical_score >= 70
                    and documentary_value >= 55
                )

                score_gate_failed = not (
                    strong_action_match
                    or contextual_historical_fallback
                )
            else:
                score_gate_failed = (
                    relevance_score < 55
                    or historical_score < 50
                    or documentary_value < 45
                )

            if (
                anachronism
                or decision == "reject"
                or score_gate_failed
            ):
                rejection_reasons.append(
                    f"rank={candidate_rank}; "
                    f"title={candidate_source_info.get('title')}; "
                    f"decision={decision}; "
                    f"anachronism={anachronism}; "
                    f"relevance={relevance_score}; "
                    f"historical={historical_score}; "
                    f"documentary={documentary_value}; "
                    f"score_gate_failed={score_gate_failed}; "
                    f"human_action_required={human_action_required}; "
                    f"reason={candidate_evaluation.get('reason')}"
                )
                continue

        winner_info = ranked_item
        winner = candidate
        filepath = candidate_filepath
        openai_evaluation = (
            candidate_evaluation
        )
        break

    if (
        winner_info is None
        or winner is None
        or not filepath
    ):
        raise RuntimeError(
            "No historical candidate passed editorial review. "
            + " | ".join(
                rejection_reasons[:5]
            )
        )

    source_info = dict(
        getattr(
            winner,
            "source_info",
            None,
        )
        or {}
    )

    save_dir = Path(
        output_directory
        or "storage/obardo_temp/images"
    )

    save_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    filepath = _download_wikimedia_image(
        image_url=winner.url,
        save_dir=str(save_dir),
    )

    if not filepath:
        raise RuntimeError(
            "Wikimedia image download failed."
        )

    winner_provider = str(
        source_info.get("provider")
        or "wikimedia"
    ).strip().lower()

    return {
        "media_type": "historical_image",
        "provider": winner_provider,
        "beat_text": beat_text,
        "identity": identity,
        "search_query": search_term,
        "filepath": str(filepath),
        "duration": float(duration_seconds),
        "source_url": winner.url,
        "source_page": source_info.get(
            "source_page"
        ),
        "title": source_info.get("title"),
        "creator": source_info.get(
            "creator"
        ),
        "license": source_info.get(
            "license"
        ),
        "license_url": source_info.get(
            "license_url"
        ),
        "description": source_info.get(
            "description"
        ),
        "rendition": source_info.get(
            "rendition"
        ),
        # Generic multi-provider historical ranking metadata.
        "historical_base_score": winner_info[
            "base_score"
        ],
        "historical_adjustment": winner_info[
            "adjustment"
        ],
        "historical_final_score": winner_info[
            "final_score"
        ],
        "historical_reasons": winner_info[
            "reasons"
        ],
        "historical_original_rank": winner_info[
            "original_rank"
        ],

        # Backward-compatible aliases.
        # Keep temporarily while older code/logs may still expect
        # Wikimedia-prefixed result fields.
        "wikimedia_base_score": winner_info[
            "base_score"
        ],
        "wikimedia_adjustment": winner_info[
            "adjustment"
        ],
        "wikimedia_final_score": winner_info[
            "final_score"
        ],
        "wikimedia_reasons": winner_info[
            "reasons"
        ],
        "wikimedia_original_rank": winner_info[
            "original_rank"
        ],
        "openai_editorial": openai_evaluation,
    }

def resolve_media_for_beat(
    beat_text: str,
    *,
    context_text: str | None = None,
    search_query: str | None = None,
    video_aspect="16:9",
    execute: bool = True,
    search_results: int = 20,
    video_candidate_count: int = 5,
    clip_duration_seconds: float = 4.0,
    max_preview_height: int = 360,
    max_hq_height: int = 720,
    output_name: str | None = None,
    cleanup_hq_source: bool = True,
    avoid_media_keys: list[str] | None = None,
    script_text: str | None = None,
    previous_text: str = "",
    next_text: str = "",
    beat_index: int | None = None,
    global_context: dict | None = None,
    visual_plan: dict | None = None,
) -> dict:
    """
    Master O Bardo media resolver.

    Pipeline:
        narration beat
            -> media router
            -> VIDEO:
                   YouTube candidates
                   contextual/historical gates
                   preview tournament
                   OpenCLIP
                   fine timestamp refinement
                   HQ source
                   local H.264 clip
            -> HISTORICAL_IMAGE:
                   Wikimedia
                   semantic/historical ranking
                   licensing metadata
                   local image

    When execute=False, only the routing decision is returned.
    """

    from loguru import logger

    beat_text = str(beat_text or "").strip()

    if not beat_text:
        raise ValueError("beat_text is required.")

    # ---------------------------------------------------------
    # Optional OpenAI editorial planning.
    #
    # OFF:
    #     zero API calls.
    #
    # ASSIST:
    #     enrich metadata but preserve existing resolver logic.
    #
    # STRICT:
    #     may reject clearly unsuitable historical media.
    #
    # Any API/configuration failure remains non-fatal.
    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # Historical Visual Director V2.3
    #
    # Ollama/local AI is the primary visual planner.
    # OpenAI planning remains fallback only.
    # ---------------------------------------------------------

    historical_visual_plan = {}

    try:
        from app.services import (
            obardo_historical_visual_director,
        )

        director_script_text = str(
            script_text
            or context_text
            or beat_text
            or ""
        ).strip()

        historical_visual_plan = (
            dict(visual_plan)
            if (
                isinstance(visual_plan, dict)
                and visual_plan
            )
            else (
                obardo_historical_visual_director
                .plan_visual_for_beat(
                    script_text=director_script_text,
                    beat_text=beat_text,
                    previous_text=str(
                        previous_text or ""
                    ),
                    next_text=str(
                        next_text or ""
                    ),
                    beat_index=beat_index,
                    global_context=(
                        global_context
                        if isinstance(global_context, dict)
                        else None
                    ),
                )
                or {}
            )
        )

    except Exception as director_exc:
        historical_visual_plan = {}

        logger.warning(
            "O Bardo HISTORICAL DIRECTOR FAILED | "
            f"{type(director_exc).__name__}: "
            f"{director_exc}"
        )

    openai_visual_plan = {}

    if not historical_visual_plan:
        try:
            from app.services import (
                obardo_openai_editorial,
            )

            if obardo_openai_editorial.is_enabled():
                openai_visual_plan = (
                    obardo_openai_editorial
                    .build_visual_plan(
                        beat_text,
                        context_text=context_text,
                    )
                    or {}
                )

        except Exception:
            openai_visual_plan = {}

    selected_visual_plan = (
        historical_visual_plan
        if historical_visual_plan
        else openai_visual_plan
    )

    if historical_visual_plan:
        logger.info(
            "O Bardo VISUAL PLAN | "
            "planner=ollama_historical_director | "
            f"scene_type="
            f"{historical_visual_plan.get('scene_type')!r} | "
            f"location_policy="
            f"{historical_visual_plan.get('location_policy')!r} | "
            f"human_action_required="
            f"{historical_visual_plan.get('human_action_required')!r}"
        )

    elif openai_visual_plan:
        logger.info(
            "O Bardo VISUAL PLAN | "
            "planner=openai_fallback"
        )


    decision = decide_media_source_for_beat(
        beat_text,
        search_query=(
            search_query
            or None
        ),
        search_results=max(
            int(search_results),
            1,
        ),
        visual_plan=selected_visual_plan,
    )

    # ---------------------------------------------------------
    # V2.3.12 STRUCTURED ROUTE AUTHORITY
    #
    # Legacy routing primarily considers historical identity and
    # available video candidates. The structured Historical
    # Director plan is more specific and must be authoritative
    # for clear document/object/person/action beats.
    # ---------------------------------------------------------
    if isinstance(decision, dict):
        decision = dict(decision)
    else:
        decision = {}

    route_plan = (
        selected_visual_plan
        if isinstance(selected_visual_plan, dict)
        else {}
    )

    plan_scene_type = str(
        route_plan.get("scene_type")
        or ""
    ).strip().lower()

    plan_human_action = bool(
        route_plan.get("human_action_required")
    )

    plan_specific_object = bool(
        route_plan.get("specific_object_required")
    )

    plan_media_types = [
        str(item or "").strip().lower()
        for item in (
            route_plan.get("ideal_media_types")
            or route_plan.get("preferred_asset_types")
            or []
        )
        if str(item or "").strip()
    ]

    still_scene_types = {
        "object_focus",
        "document",
        "document_focus",
        "printed_material",
        "institutional_document",
        "historical_person",
        "portrait",
        "artifact",
    }

    action_scene_types = {
        "human_action",
        "procedure",
        "physical_action",
        "crowd_action",
    }

    still_markers = (
        "photo",
        "photograph",
        "portrait",
        "document",
        "manual",
        "book",
        "page",
        "newspaper",
        "journal",
        "letter",
        "poster",
        "painting",
        "engraving",
        "illustration",
        "artwork",
    )

    video_markers = (
        "video",
        "footage",
        "reenactment",
        "reconstruction_video",
    )

    has_still_media = any(
        any(
            marker in media_type
            for marker in still_markers
        )
        for media_type in plan_media_types
    )

    has_video_media = any(
        any(
            marker in media_type
            for marker in video_markers
        )
        for media_type in plan_media_types
    )

    structured_route = None
    structured_reason = None

    # Strongest rule:
    # an explicitly required historical object/document/person
    # should not be replaced by generic moving footage.
    if plan_specific_object:
        structured_route = "historical_image"
        structured_reason = (
            "structured_specific_object_prefers_image"
        )

    elif plan_scene_type in still_scene_types:
        structured_route = "historical_image"
        structured_reason = (
            "structured_still_scene_prefers_image"
        )

    # Explicit visible action requires moving footage first.
    elif (
        plan_human_action
        or plan_scene_type in action_scene_types
    ):
        structured_route = "video"
        structured_reason = (
            "structured_human_action_prefers_video"
        )

    # Media-type preference is used only when unambiguous.
    elif has_still_media and not has_video_media:
        structured_route = "historical_image"
        structured_reason = (
            "structured_media_types_prefer_image"
        )

    elif has_video_media and not has_still_media:
        structured_route = "video"
        structured_reason = (
            "structured_media_types_prefer_video"
        )

    if structured_route:
        legacy_route = decision.get(
            "recommended_media_type"
        )

        legacy_reason = decision.get("reason")

        decision[
            "legacy_recommended_media_type"
        ] = legacy_route

        decision[
            "legacy_reason"
        ] = legacy_reason

        decision[
            "recommended_media_type"
        ] = structured_route

        decision[
            "reason"
        ] = structured_reason

        decision[
            "structured_route_override"
        ] = True

        logger.info(
            "O Bardo V2.3.12 STRUCTURED ROUTE AUTHORITY | "
            f"legacy={legacy_route!r} -> "
            f"final={structured_route!r} | "
            f"reason={structured_reason} | "
            f"scene_type={plan_scene_type!r} | "
            f"specific_object={plan_specific_object!r} | "
            f"human_action={plan_human_action!r}"
        )

    media_type = decision.get(
        "recommended_media_type"
    )

    base_result = {
        "beat_text": beat_text,
        "historical_visual_plan": (
            historical_visual_plan
            if historical_visual_plan
            else None
        ),
        "visual_planner": (
            "ollama_historical_director"
            if historical_visual_plan
            else (
                "openai_fallback"
                if openai_visual_plan
                else "deterministic_fallback"
            )
        ),
        "openai_visual_plan": (
            openai_visual_plan
            if openai_visual_plan
            else None
        ),
        "route": media_type,
        "decision": decision,
        "executed": False,
    }

    if not execute:
        return base_result

    # ---------------------------------------------------------
    # Historical still / artifact path
    # ---------------------------------------------------------
    # Last-resort historically safe media for this beat.
    #
    # A weak Wikimedia candidate may still be preferable to
    # aborting the whole video when stock and YouTube both fail.
    safe_historical_fallback = None

    if media_type == "historical_image":
        try:
            image_result = (
                resolve_historical_image_for_beat(
                    beat_text,
                    search_query=(
                        search_query
                        or decision.get(
                            "search_query"
                        )
                    ),
                    duration_seconds=max(
                        int(round(
                            clip_duration_seconds
                        )),
                        1,
                    ),
                    context_text=context_text,
                    avoid_media_keys=avoid_media_keys,
                    visual_plan=selected_visual_plan,
                )
            )

            # -------------------------------------------------
            # Optional OpenAI visual review.
            #
            # We evaluate only the already-selected historical
            # image, so the existing Wikimedia search/ranking
            # remains untouched.
            # -------------------------------------------------
            try:
                from app.services import (
                    obardo_openai_editorial,
                )

                if (
                    obardo_openai_editorial.is_enabled()
                    and image_result.get("filepath")
                ):
                    openai_evaluation = (
                        obardo_openai_editorial
                        .evaluate_image_candidate(
                            image_result["filepath"],
                            beat_text=beat_text,
                            context_text=context_text,
                            visual_plan=selected_visual_plan,
                        )
                        or {}
                    )

                    if openai_evaluation:
                        image_result[
                            "openai_editorial"
                        ] = openai_evaluation

                        # ASSIST never changes the current
                        # resolver decision.
                        #
                        # STRICT may reject the candidate,
                        # which naturally enters the existing
                        # stock/video fallback below.
                        editorial_mode = (
                            obardo_openai_editorial
                            .editorial_mode()
                        )

                        relevance_score = int(
                            openai_evaluation.get(
                                "relevance_score",
                                0,
                            )
                            or 0
                        )

                        editorial_decision = str(
                            openai_evaluation.get(
                                "decision",
                                "",
                            )
                            or ""
                        ).strip().lower()

                        # ASSIST may reroute obviously weak
                        # primary media, but API failures remain
                        # completely non-fatal.
                        assist_should_fallback = (
                            editorial_mode == "assist"
                            and (
                                editorial_decision == "reject"
                                or (
                                    editorial_decision == "weak"
                                    and relevance_score < 55
                                )
                            )
                        )

                        strict_should_fallback = (
                            editorial_mode == "strict"
                            and obardo_openai_editorial
                            .should_reject(
                                openai_evaluation
                            )
                        )

                        if (
                            assist_should_fallback
                            or strict_should_fallback
                        ):
                            # ---------------------------------------------
                            # Preserve a historically safe candidate as a
                            # last-resort fallback.
                            #
                            # It may be editorially weak for this exact beat,
                            # but it must not be anachronistic and must still
                            # have reasonable historical confidence.
                            # ---------------------------------------------
                            historical_score = int(
                                openai_evaluation.get(
                                    "historical_score",
                                    0,
                                )
                                or 0
                            )

                            has_anachronism = bool(
                                openai_evaluation.get(
                                    "anachronism"
                                )
                            )

                            if (
                                not has_anachronism
                                and historical_score >= 55
                                and image_result.get(
                                    "filepath"
                                )
                            ):
                                safe_historical_fallback = dict(
                                    image_result
                                )

                                safe_historical_fallback[
                                    "fallback_quality"
                                ] = "historically_safe"

                                safe_historical_fallback[
                                    "fallback_reason"
                                ] = (
                                    openai_evaluation.get(
                                        "reason"
                                    )
                                    or
                                    "editorially weak but "
                                    "historically acceptable"
                                )

                            raise RuntimeError(
                                "OpenAI editorial fallback: "
                                + str(
                                    openai_evaluation.get(
                                        "reason"
                                    )
                                    or
                                    "historical candidate was "
                                    "not relevant enough"
                                )
                            )

            except RuntimeError:
                raise

            except Exception as editorial_exc:
                image_result[
                    "openai_editorial_error"
                ] = (
                    f"{type(editorial_exc).__name__}: "
                    f"{editorial_exc}"
                )

            return {
                **base_result,
                "executed": True,
                "result": image_result,
            }

        except Exception as image_exc:
            # Wikimedia failure must not terminate the whole
            # AutoVideo job. Fall through to external video.
            base_result[
                "historical_image_error"
            ] = (
                f"{type(image_exc).__name__}: "
                f"{image_exc}"
            )

            media_type = "video"
            base_result["route"] = "video"
            base_result["fallback_from"] = (
                "historical_image"
            )

    # ---------------------------------------------------------
    # External-video path
    # ---------------------------------------------------------
    if media_type == "video":
        # Prefer reusable stock footage before YouTube.
        # YouTube remains an additional fallback/source,
        # not the default provider.
        # -----------------------------------------------------
        # O Bardo Turbo v1 - speculative YouTube preflight.
        #
        # Only search/ranking/HQ metadata preflight runs here.
        # No preview, OpenCLIP tournament or HQ media download
        # occurs while stock is being evaluated.
        # -----------------------------------------------------
        from concurrent.futures import ThreadPoolExecutor

        youtube_preflight_executor = (
            ThreadPoolExecutor(
                max_workers=1
            )
        )

        youtube_preflight_future = (
            youtube_preflight_executor.submit(
                resolve_video_for_beat,
                beat_text,
                context_text=context_text,
                visual_plan=selected_visual_plan,
                search_query=(
                    decision.get(
                        "search_query"
                    )
                    or search_query
                ),
                search_results=max(
                    int(search_results),
                    1,
                ),
                candidate_count=max(
                    int(video_candidate_count),
                    1,
                ),
                max_hq_height=int(
                    max_hq_height
                ),
                avoid_media_keys=avoid_media_keys,
                preflight_only=True,
            )
        )

        logger.info(
            "O Bardo TURBO YOUTUBE SPECULATIVE "
            "PREFLIGHT START"
        )

        try:
            stock_result = resolve_stock_video_for_beat(
                beat_text,
                context_text=context_text,
                duration_seconds=float(
                    clip_duration_seconds
                ),
                video_aspect=video_aspect,
                output_name=output_name,
                cleanup_source=True,
                avoid_media_keys=avoid_media_keys,
                visual_plan=selected_visual_plan,
            )

            base_result[
                "route"
            ] = "video"

            base_result[
                "provider"
            ] = stock_result.get(
                "provider"
            )

            youtube_preflight_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )

            return {
                **base_result,
                "executed": True,
                "result": stock_result,
            }

        except Exception as stock_exc:
            base_result[
                "stock_video_error"
            ] = (
                f"{type(stock_exc).__name__}: "
                f"{stock_exc}"
            )

        try:
            try:
                youtube_preflight_result = (
                    youtube_preflight_future.result()
                )

                logger.info(
                    "O Bardo TURBO YOUTUBE SPECULATIVE "
                    "PREFLIGHT JOIN | candidates={}",
                    (
                        youtube_preflight_result.get(
                            "candidate_count"
                        )
                        if isinstance(
                            youtube_preflight_result,
                            dict,
                        )
                        else None
                    ),
                )

            except Exception as preflight_exc:
                logger.warning(
                    "O Bardo TURBO YOUTUBE SPECULATIVE "
                    "PREFLIGHT FAILED | {}: {}",
                    type(preflight_exc).__name__,
                    preflight_exc,
                )

            finally:
                youtube_preflight_executor.shutdown(
                    wait=False,
                    cancel_futures=True,
                )

            video_result = resolve_video_for_beat(
                beat_text,
                context_text=context_text,
                visual_plan=selected_visual_plan,
                search_query=(
                    decision.get(
                        "search_query"
                    )
                    or search_query
                ),
                search_results=max(
                    int(search_results),
                    1,
                ),
                candidate_count=max(
                    int(video_candidate_count),
                    1,
                ),
                clip_duration_seconds=float(
                    clip_duration_seconds
                ),
                max_preview_height=int(
                    max_preview_height
                ),
                max_hq_height=int(
                    max_hq_height
                ),
                output_name=output_name,
                cleanup_hq_source=bool(
                    cleanup_hq_source
                ),
                avoid_media_keys=avoid_media_keys,
            )

            return {
                **base_result,
                "executed": True,
                "result": video_result,
            }

        except Exception as video_exc:
            base_result[
                "youtube_video_error"
            ] = (
                f"{type(video_exc).__name__}: "
                f"{video_exc}"
            )


            # ---------------------------------------------
            # Period-safe contextual background fallback.
            #
            # Specific stock and YouTube have both failed.
            # Try neutral period-compatible footage with no
            # modern people/tourists before falling back to
            # the historical still image.
            # ---------------------------------------------
            try:
                period_safe_plan = dict(
                    selected_visual_plan
                    if isinstance(
                        selected_visual_plan,
                        dict,
                    )
                    else {}
                )

                period_safe_plan[
                    "fallback_mode"
                ] = "period_safe_background"

                contextual_result = (
                    resolve_stock_video_for_beat(
                        beat_text,
                        context_text=context_text,
                        duration_seconds=float(
                            clip_duration_seconds
                        ),
                        video_aspect=video_aspect,
                        output_name=output_name,
                        cleanup_source=True,
                        avoid_media_keys=avoid_media_keys,
                        visual_plan=period_safe_plan,
                    )
                )

                base_result[
                    "route"
                ] = "video"

                base_result[
                    "provider"
                ] = contextual_result.get(
                    "provider"
                )

                base_result[
                    "fallback_from"
                ] = "specific_media"

                base_result[
                    "period_safe_background"
                ] = True

                return {
                    **base_result,
                    "executed": True,
                    "result": contextual_result,
                }

            except Exception as contextual_exc:
                base_result[
                    "period_safe_background_error"
                ] = (
                    f"{type(contextual_exc).__name__}: "
                    f"{contextual_exc}"
                )


            # ---------------------------------------------
            # Final historical-image fallback.
            #
            # If specific stock, YouTube and period-safe
            # contextual footage all fail, make one fresh
            # Wikimedia/historical-image attempt before
            # giving up on this beat.
            # ---------------------------------------------
            if not safe_historical_fallback:
                try:
                    safe_historical_fallback = (
                        resolve_historical_image_for_beat(
                            beat_text,
                            search_query=(
                                search_query
                                or decision.get(
                                    "search_query"
                                )
                            ),
                            duration_seconds=max(
                                int(round(
                                    float(
                                        clip_duration_seconds
                                    )
                                )),
                                1,
                            ),
                            context_text=context_text,
                            avoid_media_keys=avoid_media_keys,
                            visual_plan=selected_visual_plan,
                        )
                    )

                except Exception as historical_exc:
                    base_result[
                        "final_historical_fallback_error"
                    ] = (
                        f"{type(historical_exc).__name__}: "
                        f"{historical_exc}"
                    )

            if safe_historical_fallback:
                base_result[
                    "route"
                ] = "historical_image"

                base_result[
                    "provider"
                ] = "wikimedia"

                base_result[
                    "fallback_from"
                ] = "video"

                base_result[
                    "safe_fallback_used"
                ] = True

                return {
                    **base_result,
                    "executed": True,
                    "result": (
                        safe_historical_fallback
                    ),
                }

            raise

    raise RuntimeError(
        f"Unsupported media route: {media_type!r}"
    )
