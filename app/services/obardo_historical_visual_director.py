from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests


logger = logging.getLogger(__name__)


DIRECTOR_VERSION = "1.0.0"

OLLAMA_BASE_URL = (
    os.getenv(
        "OLLAMA_BASE_URL",
        "http://127.0.0.1:11434",
    )
    .strip()
    .rstrip("/")
)

DEFAULT_TEXT_MODEL = os.getenv(
    "OBARDO_HISTORICAL_TEXT_MODEL",
    "qwen2.5-coder:7b",
).strip()

DEFAULT_VISION_MODEL = os.getenv(
    "OBARDO_HISTORICAL_VISION_MODEL",
    "qwen3-vl:2b",
).strip()

OPENAI_FALLBACK_MODEL = os.getenv(
    "OBARDO_OPENAI_FALLBACK_MODEL",
    "",
).strip()

GLOBAL_CONFIDENCE_THRESHOLD = float(
    os.getenv(
        "OBARDO_HISTORICAL_GLOBAL_CONFIDENCE",
        "0.72",
    )
)

BEAT_CONFIDENCE_THRESHOLD = float(
    os.getenv(
        "OBARDO_HISTORICAL_BEAT_CONFIDENCE",
        "0.72",
    )
)

OLLAMA_TIMEOUT_SECONDS = int(
    os.getenv(
        "OBARDO_OLLAMA_TIMEOUT_SECONDS",
        "180",
    )
)

CACHE_ROOT = Path(
    os.getenv(
        "OBARDO_HISTORICAL_DIRECTOR_CACHE",
        "storage/obardo_cache/"
        "historical_visual_director",
    )
)


GLOBAL_SYSTEM_PROMPT = r"""
You are the Historical Visual Director of a serious
historical documentary production system.

Your job is NOT to write the documentary.

Your job is to understand the complete script before
media search begins.

Identify the most historically plausible:

- historical period
- approximate date range
- political entity
- civilization/culture
- geography
- important people
- social groups
- military groups
- material culture
- architecture
- technologies
- clothing
- weapons
- transportation
- religious context
- visual motifs

Distinguish carefully between:

1. the historical period being narrated;
2. later paintings or reconstructions that depict it;
3. modern photographs of archaeological remains;
4. anachronistic representations.

Do not invent precision unsupported by the script.

For ambiguous information, explicitly lower confidence.

Return ONLY valid JSON.

Required JSON shape:

{
  "historical_period": "",
  "date_range": "",
  "centuries": [],
  "civilization": "",
  "political_entity": "",
  "primary_geography": [],
  "secondary_geography": [],
  "historical_people": [],
  "social_groups": [],
  "military_groups": [],
  "material_culture": [],
  "architecture": [],
  "technology": [],
  "clothing": [],
  "weapons": [],
  "transportation": [],
  "religious_context": [],
  "global_visual_identity": [],
  "strict_anachronisms": [],
  "preferred_visual_sources": [],
  "historical_notes": [],
  "confidence": 0.0
}


location_policy must be exactly one of:

- "required": the specific place or architectural
  setting is essential to visually communicate the beat.

- "optional": the subject, action, or event is more
  important than the exact place. The location may be
  used only when it improves the visual result.

- "forbidden": showing a location or architectural
  environment would distract from or weaken the required
  visual subject, object, detail, or close-up.

Do not mark a location as required merely because it is
historically associated with the subject.

For action-heavy scenes, prefer "optional" unless the
specific location is genuinely necessary.

For object-focused close-ups, prefer "forbidden" when the
surrounding location is not visually necessary.

confidence must be between 0 and 1.
"""


BEAT_SYSTEM_PROMPT = r"""
You are the shot-planning component of a historical
documentary Visual Director.

You receive:

- global historical context for the entire script;
- previous narration;
- current narration;
- next narration.

Your job is to determine what SHOULD APPEAR ON SCREEN
while the current narration is spoken.

Do NOT merely extract keywords.

Resolve pronouns and generic nouns using the global
historical context.

Example:

Global context:
Roman Empire, first century AD.

Current narration:
"The soldiers climbed the hill."

The subject is not generic soldiers.
The intended visual is Roman soldiers from that period
climbing or marching uphill with historically plausible
Roman equipment.

Separate:

- subject identity;
- action;
- setting;
- required visual elements;
- desirable elements;
- forbidden/anachronistic elements.

Search queries must describe what we actually need to
SEE.

Prefer concrete visual searches.

Do not force exact action when archival material is
unlikely to exist. In that case provide:

- ideal queries;
- historically safe fallback queries.

Return ONLY valid JSON.

Required JSON shape:

{
  "visual_intent": "",
  "subject": [],
  "subject_identity": [],
  "action": [],
  "environment": [],
  "location": [],
  "location_policy": "optional",
  "required_elements": [],
  "preferred_elements": [],
  "forbidden_elements": [],
  "anachronism_risks": [],
  "ideal_media_types": [],
  "queries": [],
  "fallback_queries": [],
  "search_concepts": [],
  "object_focus": [],
  "human_action_required": false,
  "specific_object_required": false,
  "historical_specificity": "high",
  "confidence": 0.0,
  "reasoning_summary": ""
}

confidence must be between 0 and 1.

reasoning_summary must be concise and may explain the
visual decision, but must not contain hidden chain of
thought.
"""


def _safe_string(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def _safe_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in value:
        item_text = _safe_string(item)

        if not item_text:
            continue

        key = item_text.casefold()

        if key in seen:
            continue

        seen.add(key)
        result.append(item_text)

    return result


def _safe_confidence(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        score = float(value)
    except Exception:
        score = default

    return max(
        0.0,
        min(1.0, score),
    )


def _stable_hash(*parts: str) -> str:
    joined = "\n\n".join(
        str(part or "")
        for part in parts
    )

    return hashlib.sha256(
        joined.encode(
            "utf-8",
            errors="replace",
        )
    ).hexdigest()


def _extract_json_object(text: str) -> dict[str, Any]:
    text = _safe_string(text)

    if not text:
        raise ValueError(
            "empty model response"
        )

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        value = json.loads(text)

        if isinstance(value, dict):
            return value
    except Exception:
        pass

    first = text.find("{")
    last = text.rfind("}")

    if (
        first >= 0
        and last > first
    ):
        candidate = text[
            first:last + 1
        ]

        value = json.loads(candidate)

        if isinstance(value, dict):
            return value

    raise ValueError(
        "model output does not contain "
        "a valid JSON object"
    )


def _ollama_chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 0.15,
) -> dict[str, Any]:
    url = (
        f"{OLLAMA_BASE_URL}/api/chat"
    )

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "options": {
            "temperature": min(
                float(temperature),
                0.10,
            ),
            "num_predict": 1024,
            "num_ctx": 4096,
        },
    }

    response = requests.post(
        url,
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    data = response.json()

    message = (
        data.get("message")
        or {}
    )

    content = _safe_string(
        message.get("content")
    )

    return _extract_json_object(
        content
    )


def _openai_chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    if not os.getenv(
        "OPENAI_API_KEY"
    ):
        raise RuntimeError(
            "OPENAI_API_KEY unavailable"
        )

    if not OPENAI_FALLBACK_MODEL:
        raise RuntimeError(
            "OBARDO_OPENAI_FALLBACK_MODEL "
            "not configured"
        )

    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(
            "OpenAI Python package unavailable"
        ) from exc

    client = OpenAI()

    response = client.responses.create(
        model=OPENAI_FALLBACK_MODEL,
        input=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    content = _safe_string(
        getattr(
            response,
            "output_text",
            "",
        )
    )

    return _extract_json_object(
        content
    )


def _call_json_ai(
    *,
    system_prompt: str,
    user_prompt: str,
    confidence_threshold: float,
    task_name: str,
    model: str = DEFAULT_TEXT_MODEL,
) -> tuple[
    dict[str, Any],
    str,
]:
    local_errors: list[str] = []

    for attempt in range(1, 3):
        try:
            started = time.time()

            result = _ollama_chat_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
            )

            confidence = _safe_confidence(
                result.get("confidence")
            )

            elapsed = (
                time.time()
                - started
            )

            logger.info(
                "O Bardo HISTORICAL DIRECTOR "
                "OLLAMA OK | "
                f"task={task_name!r} | "
                f"attempt={attempt} | "
                f"model={model!r} | "
                f"confidence={confidence:.2f} | "
                f"elapsed={elapsed:.1f}s"
            )

            if (
                confidence
                >= confidence_threshold
            ):
                return (
                    result,
                    "ollama",
                )

            local_errors.append(
                "low_confidence:"
                f"{confidence:.2f}"
            )

        except Exception as exc:
            error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            local_errors.append(
                error
            )

            logger.warning(
                "O Bardo HISTORICAL DIRECTOR "
                "OLLAMA FAILED | "
                f"task={task_name!r} | "
                f"attempt={attempt} | "
                f"error={error}"
            )

    try:
        started = time.time()

        result = _openai_chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        elapsed = (
            time.time()
            - started
        )

        logger.warning(
            "O Bardo HISTORICAL DIRECTOR "
            "OPENAI FALLBACK USED | "
            f"task={task_name!r} | "
            f"elapsed={elapsed:.1f}s | "
            f"local_errors={local_errors!r}"
        )

        return (
            result,
            "openai",
        )

    except Exception as exc:
        logger.warning(
            "O Bardo HISTORICAL DIRECTOR "
            "OPENAI FALLBACK UNAVAILABLE | "
            f"task={task_name!r} | "
            f"error={type(exc).__name__}: "
            f"{exc}"
        )

    raise RuntimeError(
        "Historical Visual Director failed. "
        f"task={task_name}; "
        f"local_errors={local_errors!r}"
    )


def _normalize_global_context(
    value: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "historical_period": _safe_string(
            value.get(
                "historical_period"
            )
        ),
        "date_range": _safe_string(
            value.get("date_range")
        ),
        "centuries": _safe_list(
            value.get("centuries")
        ),
        "civilization": _safe_string(
            value.get("civilization")
        ),
        "political_entity": _safe_string(
            value.get(
                "political_entity"
            )
        ),
        "primary_geography": _safe_list(
            value.get(
                "primary_geography"
            )
        ),
        "secondary_geography": _safe_list(
            value.get(
                "secondary_geography"
            )
        ),
        "historical_people": _safe_list(
            value.get(
                "historical_people"
            )
        ),
        "social_groups": _safe_list(
            value.get(
                "social_groups"
            )
        ),
        "military_groups": _safe_list(
            value.get(
                "military_groups"
            )
        ),
        "material_culture": _safe_list(
            value.get(
                "material_culture"
            )
        ),
        "architecture": _safe_list(
            value.get("architecture")
        ),
        "technology": _safe_list(
            value.get("technology")
        ),
        "clothing": _safe_list(
            value.get("clothing")
        ),
        "weapons": _safe_list(
            value.get("weapons")
        ),
        "transportation": _safe_list(
            value.get(
                "transportation"
            )
        ),
        "religious_context": _safe_list(
            value.get(
                "religious_context"
            )
        ),
        "global_visual_identity": _safe_list(
            value.get(
                "global_visual_identity"
            )
        ),
        "strict_anachronisms": _safe_list(
            value.get(
                "strict_anachronisms"
            )
        ),
        "preferred_visual_sources": _safe_list(
            value.get(
                "preferred_visual_sources"
            )
        ),
        "historical_notes": _safe_list(
            value.get(
                "historical_notes"
            )
        ),
        "confidence": _safe_confidence(
            value.get("confidence")
        ),
    }

    return result



ACTION_TERMS = {
    "climb",
    "climbing",
    "ascend",
    "ascending",
    "march",
    "marching",
    "advance",
    "advancing",
    "walk",
    "walking",
    "run",
    "running",
    "cross",
    "crossing",
    "attack",
    "attacking",
    "fight",
    "fighting",
    "battle",
    "charge",
    "charging",
    "retreat",
    "retreating",
    "carry",
    "carrying",
    "ride",
    "riding",
    "sail",
    "sailing",
    "enter",
    "entering",
    "leave",
    "leaving",
    "observe",
    "observing",
    "build",
    "building",
    "work",
    "working",
}

STATIC_LOCATION_HINTS = {
    "camp",
    "fortification",
    "temple",
    "palace",
    "house",
    "market",
    "forum",
    "bath",
    "baths",
    "harbor",
    "port",
    "arena",
    "amphitheatre",
    "amphitheater",
}

GENERIC_LOCATION_TOKENS = {
    "roman",
    "rome",
    "military",
    "ancient",
    "historical",
    "historic",
    "classical",
    "imperial",
    "empire",
    "army",
    "soldier",
    "soldiers",
}

def _contains_action(
    values: list[str],
) -> bool:
    haystack = " ".join(
        _safe_list(values)
    ).lower()

    return any(
        term in haystack
        for term in ACTION_TERMS
    )


def _query_variants_from_plan(
    *,
    subjects: list[str],
    subject_identity: list[str],
    actions: list[str],
    environment: list[str],
) -> list[str]:
    identities = (
        _safe_list(subject_identity)
        or _safe_list(subjects)
    )

    actions = _safe_list(actions)
    environment = _safe_list(environment)

    queries: list[str] = []

    for identity in identities[:2]:
        for action in actions[:2]:
            queries.append(
                f"{identity} {action}"
            )

            for env in environment[:2]:
                queries.append(
                    f"{identity} {action} {env}"
                )

    return _safe_list(queries)



ACTION_TERMS = {
    "climb",
    "climbing",
    "ascend",
    "ascending",
    "march",
    "marching",
    "advance",
    "advancing",
    "walk",
    "walking",
    "run",
    "running",
    "cross",
    "crossing",
    "attack",
    "attacking",
    "fight",
    "fighting",
    "battle",
    "charge",
    "charging",
    "retreat",
    "retreating",
    "carry",
    "carrying",
    "ride",
    "riding",
    "sail",
    "sailing",
    "enter",
    "entering",
    "leave",
    "leaving",
    "observe",
    "observing",
    "build",
    "building",
    "work",
    "working",
}

STATIC_LOCATION_HINTS = {
    "camp",
    "fortification",
    "temple",
    "palace",
    "house",
    "market",
    "forum",
    "bath",
    "baths",
    "harbor",
    "port",
    "arena",
    "amphitheatre",
    "amphitheater",
}

def _contains_action(
    values: list[str],
) -> bool:
    haystack = " ".join(
        _safe_list(values)
    ).lower()

    return any(
        term in haystack
        for term in ACTION_TERMS
    )


def _query_variants_from_plan(
    *,
    subjects: list[str],
    subject_identity: list[str],
    actions: list[str],
    environment: list[str],
) -> list[str]:
    identities = (
        _safe_list(subject_identity)
        or _safe_list(subjects)
    )

    actions = _safe_list(actions)
    environment = _safe_list(environment)

    queries: list[str] = []

    for identity in identities[:2]:
        for action in actions[:2]:
            queries.append(
                f"{identity} {action}"
            )

            for env in environment[:2]:
                queries.append(
                    f"{identity} {action} {env}"
                )

    return _safe_list(queries)


def _normalize_beat_plan(
    value: dict[str, Any],
) -> dict[str, Any]:
    location_policy = _safe_string(
        value.get(
            "location_policy"
        )
    ).lower()

    if location_policy not in {
        "required",
        "optional",
        "forbidden",
    }:
        location_policy = "optional"

    specificity = _safe_string(
        value.get(
            "historical_specificity"
        )
    ).lower()

    if specificity not in {
        "low",
        "medium",
        "high",
    }:
        specificity = "medium"

    result = {
        "visual_intent": _safe_string(
            value.get(
                "visual_intent"
            )
        ),
        "subject": _safe_list(
            value.get("subject")
        ),
        "subject_identity": _safe_list(
            value.get(
                "subject_identity"
            )
        ),
        "action": _safe_list(
            value.get("action")
        ),
        "environment": _safe_list(
            value.get("environment")
        ),
        "location": _safe_list(
            value.get("location")
        ),
        "location_policy": (
            location_policy
        ),
        "required_elements": _safe_list(
            value.get(
                "required_elements"
            )
        ),
        "preferred_elements": _safe_list(
            value.get(
                "preferred_elements"
            )
        ),
        "forbidden_elements": _safe_list(
            value.get(
                "forbidden_elements"
            )
        ),
        "anachronism_risks": _safe_list(
            value.get(
                "anachronism_risks"
            )
        ),
        "ideal_media_types": _safe_list(
            value.get(
                "ideal_media_types"
            )
        ),
        "queries": _safe_list(
            value.get("queries")
        ),
        "fallback_queries": _safe_list(
            value.get(
                "fallback_queries"
            )
        ),
        "search_concepts": _safe_list(
            value.get(
                "search_concepts"
            )
        ),
        "object_focus": _safe_list(
            value.get(
                "object_focus"
            )
        ),
        "human_action_required": bool(
            value.get(
                "human_action_required"
            )
        ),
        "specific_object_required": bool(
            value.get(
                "specific_object_required"
            )
        ),
        "historical_specificity": (
            specificity
        ),
        "confidence": _safe_confidence(
            value.get("confidence")
        ),
        "reasoning_summary": _safe_string(
            value.get(
                "reasoning_summary"
            )
        ),
    }

    inferred_action_required = (
        bool(
            result[
                "human_action_required"
            ]
        )
        or _contains_action(
            result["action"]
        )
    )

    result[
        "human_action_required"
    ] = inferred_action_required

    if inferred_action_required:
        result["action_priority"] = "high"
        result["scene_type"] = (
            "human_action"
        )
    elif result["specific_object_required"]:
        result["action_priority"] = "low"
        result["scene_type"] = (
            "object_focus"
        )
    else:
        result["action_priority"] = "medium"
        result["scene_type"] = (
            "contextual_scene"
        )

    generated_queries = (
        _query_variants_from_plan(
            subjects=result["subject"],
            subject_identity=(
                result[
                    "subject_identity"
                ]
            ),
            actions=result["action"],
            environment=(
                result["environment"]
            ),
        )
    )

    result["queries"] = _safe_list(
        result["queries"]
        + generated_queries
    )

    # AI LOCATION POLICY V2.2
    #
    # required:
    #     Preserve the location because the AI judged
    #     it visually essential.
    #
    # optional:
    #     Allow normal Action Intelligence filtering.
    #
    # forbidden:
    #     Remove location entirely so architecture does
    #     not contaminate the visual search.

    if result["location_policy"] == "forbidden":
        result["location"] = []

    # Avoid allowing an inferred architectural
    # location to dominate an action-heavy beat.
    if (
        inferred_action_required
        and result["location_policy"] != "required"
    ):
        required_text = " ".join(
            result["required_elements"]
        ).lower()

        filtered_locations = []

        for location in result["location"]:
            location_lower = (
                location.lower()
            )

            static_only = any(
                term in location_lower
                for term
                in STATIC_LOCATION_HINTS
            )

            location_tokens = [
                token
                for token in re.findall(
                    r"[a-z]+",
                    location_lower,
                )
                if (
                    token
                    and token
                    not in GENERIC_LOCATION_TOKENS
                )
            ]

            matched_tokens = [
                token
                for token in location_tokens
                if token in required_text
            ]

            required_match = bool(
                matched_tokens
            )

            if (
                not static_only
                or required_match
            ):
                filtered_locations.append(
                    location
                )

        result["location"] = (
            filtered_locations
        )

    all_queries = (
        result["queries"]
        + result["fallback_queries"]
    )

    result["all_queries"] = _safe_list(
        all_queries
    )

    return result


def _cache_path(
    namespace: str,
    cache_key: str,
) -> Path:
    folder = (
        CACHE_ROOT
        / namespace
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    return (
        folder
        / f"{cache_key}.json"
    )


def _load_cache(
    path: Path,
) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, dict):
            return data

    except Exception:
        logger.exception(
            "O Bardo HISTORICAL DIRECTOR "
            "CACHE READ ERROR"
        )

    return None


def _save_cache(
    path: Path,
    data: dict[str, Any],
) -> None:
    try:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    except Exception:
        logger.exception(
            "O Bardo HISTORICAL DIRECTOR "
            "CACHE WRITE ERROR"
        )


def analyze_complete_script(
    script_text: str,
    *,
    force: bool = False,
    model: str = DEFAULT_TEXT_MODEL,
) -> dict[str, Any]:
    script_text = _safe_string(
        script_text
    )

    if len(script_text) < 40:
        raise ValueError(
            "script is too short for "
            "global historical analysis"
        )

    cache_key = _stable_hash(
        DIRECTOR_VERSION,
        "global",
        model,
        script_text,
    )

    cache_path = _cache_path(
        "global",
        cache_key,
    )

    if not force:
        cached = _load_cache(
            cache_path
        )

        if cached:
            cached["director_source"] = (
                "cache"
            )

            return cached

    user_prompt = (
        "Analyze the COMPLETE documentary "
        "script below before any media "
        "retrieval begins.\n\n"
        "DOCUMENTARY SCRIPT:\n"
        "-------------------\n"
        f"{script_text}\n"
        "-------------------\n\n"
        "Return the global historical "
        "context as JSON."
    )

    raw_result, source = (
        _call_json_ai(
            system_prompt=(
                GLOBAL_SYSTEM_PROMPT
            ),
            user_prompt=user_prompt,
            confidence_threshold=(
                GLOBAL_CONFIDENCE_THRESHOLD
            ),
            task_name="global_script",
            model=model,
        )
    )

    result = (
        _normalize_global_context(
            raw_result
        )
    )

    result.update(
        {
            "director_version": (
                DIRECTOR_VERSION
            ),
            "director_source": source,
            "director_model": (
                model
                if source == "ollama"
                else OPENAI_FALLBACK_MODEL
            ),
            "cache_key": cache_key,
        }
    )

    _save_cache(
        cache_path,
        result,
    )

    return result


def plan_visual_for_beat(
    *,
    script_text: str,
    beat_text: str,
    previous_text: str = "",
    next_text: str = "",
    beat_index: int | None = None,
    global_context: (
        dict[str, Any] | None
    ) = None,
    force: bool = False,
    model: str = DEFAULT_TEXT_MODEL,
) -> dict[str, Any]:
    script_text = _safe_string(
        script_text
    )

    beat_text = _safe_string(
        beat_text
    )

    if not beat_text:
        raise ValueError(
            "beat_text is required"
        )

    if global_context is None:
        global_context = (
            analyze_complete_script(
                script_text,
                force=force,
                model=model,
            )
        )

    global_context_for_prompt = {
        key: value
        for key, value
        in global_context.items()
        if not key.startswith(
            "director_"
        )
        and key != "cache_key"
    }

    cache_key = _stable_hash(
        DIRECTOR_VERSION,
        "beat",
        model,
        json.dumps(
            global_context_for_prompt,
            ensure_ascii=False,
            sort_keys=True,
        ),
        previous_text,
        beat_text,
        next_text,
    )

    cache_path = _cache_path(
        "beats",
        cache_key,
    )

    if not force:
        cached = _load_cache(
            cache_path
        )

        if cached:
            cached["director_source"] = (
                "cache"
            )

            return cached

    user_prompt = (
        "GLOBAL HISTORICAL CONTEXT:\n"
        f"{json.dumps(global_context_for_prompt, ensure_ascii=False, indent=2)}\n\n"
        "PREVIOUS NARRATION:\n"
        f"{previous_text or '[none]'}\n\n"
        "CURRENT NARRATION:\n"
        f"{beat_text}\n\n"
        "NEXT NARRATION:\n"
        f"{next_text or '[none]'}\n\n"
        "Determine what should be shown "
        "on screen during CURRENT "
        "NARRATION.\n\n"
        "Queries should be written "
        "primarily in English because "
        "the historical image providers "
        "are indexed mostly in English."
    )

    raw_result, source = (
        _call_json_ai(
            system_prompt=(
                BEAT_SYSTEM_PROMPT
            ),
            user_prompt=user_prompt,
            confidence_threshold=(
                BEAT_CONFIDENCE_THRESHOLD
            ),
            task_name=(
                f"beat_{beat_index}"
                if beat_index is not None
                else "beat"
            ),
            model=model,
        )
    )

    result = _normalize_beat_plan(
        raw_result
    )

    # Guarantee that global anachronisms
    # also reach the beat-level gate.
    result["forbidden_elements"] = (
        _safe_list(
            result[
                "forbidden_elements"
            ]
            + _safe_list(
                global_context.get(
                    "strict_anachronisms"
                )
            )
        )
    )

    result.update(
        {
            "beat_index": beat_index,
            "beat_text": beat_text,
            "previous_text": (
                _safe_string(
                    previous_text
                )
            ),
            "next_text": (
                _safe_string(
                    next_text
                )
            ),
            "director_version": (
                DIRECTOR_VERSION
            ),
            "director_source": source,
            "director_model": (
                model
                if source == "ollama"
                else OPENAI_FALLBACK_MODEL
            ),
            "cache_key": cache_key,
        }
    )

    _save_cache(
        cache_path,
        result,
    )

    return result


def plan_visuals_for_beats(
    *,
    script_text: str,
    beats: list[str],
    force: bool = False,
    model: str = DEFAULT_TEXT_MODEL,
) -> dict[str, Any]:
    global_context = (
        analyze_complete_script(
            script_text,
            force=force,
            model=model,
        )
    )

    plans: list[
        dict[str, Any]
    ] = []

    for index, beat_text in enumerate(
        beats
    ):
        previous_text = (
            beats[index - 1]
            if index > 0
            else ""
        )

        next_text = (
            beats[index + 1]
            if index + 1 < len(beats)
            else ""
        )

        plan = plan_visual_for_beat(
            script_text=script_text,
            beat_text=beat_text,
            previous_text=previous_text,
            next_text=next_text,
            beat_index=index,
            global_context=(
                global_context
            ),
            force=force,
            model=model,
        )

        plans.append(
            plan
        )

    return {
        "director_version": (
            DIRECTOR_VERSION
        ),
        "global_context": (
            global_context
        ),
        "beats": plans,
    }


__all__ = [
    "analyze_complete_script",
    "plan_visual_for_beat",
    "plan_visuals_for_beats",
    "DEFAULT_TEXT_MODEL",
    "DEFAULT_VISION_MODEL",
]
