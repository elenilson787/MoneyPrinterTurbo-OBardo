from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image


OLLAMA_URL = os.getenv(
    "OBARDO_OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat",
)

OLLAMA_MODEL = os.getenv(
    "OBARDO_LOCAL_VISION_MODEL",
    "qwen3-vl:2b-instruct",
)

LOCAL_VISION_MODE = os.getenv(
    "OBARDO_LOCAL_VISION_MODE",
    "assist",
).strip().lower()

IMAGE_SIZE = int(
    os.getenv("OBARDO_LOCAL_VISION_SIZE", "224")
)

TIMEOUT_SECONDS = int(
    os.getenv("OBARDO_LOCAL_VISION_TIMEOUT", "40")
)


def _prepare_image(image_path: str | Path) -> bytes:
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"Local vision image not found: {path}")

    cache_dir = path.parent / "obardo_local_vision_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached = cache_dir / (
        f"{path.stem}-{IMAGE_SIZE}px.jpg"
    )

    if not cached.exists() or cached.stat().st_mtime < path.stat().st_mtime:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail(
                (IMAGE_SIZE, IMAGE_SIZE),
                Image.Resampling.LANCZOS,
            )
            img.save(
                cached,
                "JPEG",
                quality=82,
                optimize=True,
            )

    return cached.read_bytes()


def _extract_explicit_match(text: str) -> bool | None:
    lowered = str(
        text
        or ""
    ).strip().lower()

    # Canonical format:
    # MATCH=true
    # MATCH=false
    explicit = re.search(
        r"\bmatch\s*=\s*(true|false)\b",
        lowered,
    )

    if explicit:
        return explicit.group(1) == "true"

    # O Bardo V2.3.8
    #
    # Some instruct VLMs obey the requested classification
    # but omit the literal "MATCH=" prefix:
    #
    # false; SUBJECT=Roman soldiers; ...
    #
    # Accept a leading standalone boolean as an equivalent
    # structured decision.
    leading_boolean = re.match(
        r"^\s*(true|false)\b",
        lowered,
    )

    if leading_boolean:
        return (
            leading_boolean.group(1)
            == "true"
        )

    negative_patterns = (
        "colosseum is not visible",
        "colosseum itself is not visible",
        "not the colosseum",
        "different structure",
        "roman forum",
        "forum ruins",
        "does not show the colosseum",
        "doesn't show the colosseum",
    )

    positive_patterns = (
        "it is the colosseum",
        "this is the colosseum",
        "structure is the colosseum",
        "shows the colosseum",
        "clearly shows the colosseum",
        "colosseum is visible",
    )

    has_negative = any(
        pattern in lowered
        for pattern in negative_patterns
    )

    has_positive = any(
        pattern in lowered
        for pattern in positive_patterns
    )

    if has_negative and not has_positive:
        return False

    if has_positive and not has_negative:
        return True

    return None


def _extract_subject(text: str, target_subject: str) -> str | None:
    lowered = text.lower()

    if "roman forum" in lowered:
        return "Roman Forum"

    subject_match = re.search(
        r"\bsubject\s*=\s*([^;\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if subject_match:
        return subject_match.group(1).strip()

    return None


def _extract_confidence(text: str) -> int | None:
    match = re.search(
        r"\bconfidence\s*=\s*(\d{1,3})",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return max(0, min(100, int(match.group(1))))


def evaluate_local_vision_frame(
    image_path: str | Path,
    target_subject: str,
    context: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()

    result: dict[str, Any] = {
        "executed": False,
        "mode": LOCAL_VISION_MODE,
        "model": OLLAMA_MODEL,
        "decision": "uncertain",
        "object_match": None,
        "subject": None,
        "confidence": 0,
        "anachronism": None,
        "content": "",
        "thinking": "",
        "elapsed_seconds": 0.0,
        "error": None,
    }

    if LOCAL_VISION_MODE == "off":
        return result

    try:
        image_bytes = _prepare_image(image_path)
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        context_text = (
            context.strip()
            if context and context.strip()
            else "No additional context."
        )

        prompt = f"""
Evaluate one image for a historical documentary.

TARGET SUBJECT:
{target_subject}

NARRATION CONTEXT:
{context_text}

Determine whether the TARGET SUBJECT itself is actually visible.

Important rules:
- Do not accept merely related locations.
- If another historical site is visible, identify it.
- Present-day footage of authentic historical ruins is not an anachronism.
- Be conservative.
- Do not infer the target merely because it is mentioned in the prompt.

Return exactly one short line:
MATCH=true or false; SUBJECT=name; ANACHRONISM=true or false; CONFIDENCE=0-100
""".strip()

        payload = {
            "model": OLLAMA_MODEL,
            "stream": False,

            # O Bardo V2.3.7 DIRECT VLM
            # This task is classification, not reasoning.
            # Prevent Qwen3-VL from spending the generation
            # budget entirely on the thinking channel.
            "think": False,

            "options": {
                "temperature": 0,
                "num_predict": 160,
            },
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
        }

        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")

        data = json.loads(raw)

        message = data.get("message") or {}
        content = str(message.get("content") or "").strip()
        thinking = str(message.get("thinking") or "").strip()

        combined = "\n".join(
            part for part in (content, thinking) if part
        )

        object_match = _extract_explicit_match(combined)

        if object_match is True:
            subject = target_subject
        else:
            subject = _extract_subject(combined, target_subject)

        confidence = _extract_confidence(combined)

        # Thinking-only responses are useful but treated
        # more conservatively than explicit final content.
        if confidence is None:
            if object_match is not None:
                confidence = 80 if content else 72
            else:
                confidence = 0

        decision = "uncertain"

        if object_match is True and confidence >= 70:
            decision = "accept"
        elif object_match is False and confidence >= 65:
            decision = "reject"

        result.update(
            {
                "executed": True,
                "decision": decision,
                "object_match": object_match,
                "subject": subject,
                "confidence": confidence,
                "content": content,
                "thinking": thinking,
                "done_reason": data.get("done_reason"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_count": data.get("eval_count"),
            }
        )

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    except Exception as exc:
        # Fail-open by design:
        # local VLM must never break video generation.
        result["error"] = f"{type(exc).__name__}: {exc}"

    finally:
        result["elapsed_seconds"] = round(
            time.perf_counter() - started,
            2,
        )

    return result


