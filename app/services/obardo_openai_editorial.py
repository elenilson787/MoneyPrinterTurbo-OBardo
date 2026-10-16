
"""
Optional OpenAI editorial layer for O Bardo.

This module is deliberately fail-open:
if configuration, API access or evaluation fails,
the existing local/media pipeline continues unchanged.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any


DEFAULT_MODEL = os.getenv(
    "OPENAI_EDITORIAL_MODEL",
    "gpt-5.6-luna",
).strip()

DEFAULT_MODE = os.getenv(
    "OPENAI_EDITORIAL_MODE",
    "off",
).strip().lower()

DEFAULT_DETAIL = os.getenv(
    "OPENAI_EDITORIAL_DETAIL",
    "low",
).strip().lower()

DEFAULT_TIMEOUT = float(
    os.getenv(
        "OPENAI_EDITORIAL_TIMEOUT",
        "60",
    )
)


def editorial_mode() -> str:
    if DEFAULT_MODE in {
        "off",
        "assist",
        "strict",
    }:
        return DEFAULT_MODE

    return "off"


def is_enabled() -> bool:
    return (
        editorial_mode() != "off"
        and bool(
            os.getenv(
                "OPENAI_API_KEY",
                "",
            ).strip()
        )
    )


def _client():
    if not is_enabled():
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    return OpenAI(
        api_key=os.getenv(
            "OPENAI_API_KEY",
        ),
        timeout=DEFAULT_TIMEOUT,
    )


def _extract_json(response: Any) -> dict:
    text = str(
        getattr(
            response,
            "output_text",
            "",
        )
        or ""
    ).strip()

    if not text:
        return {}

    try:
        parsed = json.loads(text)

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    except Exception:
        pass

    return {}


def _image_data_url(
    image_path: str | Path,
) -> str:
    path = Path(
        image_path
    )

    mime, _ = mimetypes.guess_type(
        str(path)
    )

    mime = mime or "image/jpeg"

    encoded = base64.b64encode(
        path.read_bytes()
    ).decode("ascii")

    return (
        f"data:{mime};"
        f"base64,{encoded}"
    )


VISUAL_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string"
        },
        "period": {
            "type": "string"
        },
        "location": {
            "type": "string"
        },
        "must_show": {
            "type": "string"
        },
        "preferred_media": {
            "type": "string"
        },
        "primary_queries": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "context_queries": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "avoid": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "notes": {
            "type": "string"
        },
    },
    "required": [
        "subject",
        "period",
        "location",
        "must_show",
        "preferred_media",
        "primary_queries",
        "context_queries",
        "avoid",
        "notes",
    ],
    "additionalProperties": False,
}


def build_visual_plan(
    beat_text: str,
    *,
    context_text: str | None = None,
) -> dict:
    client = _client()

    if client is None:
        return {}

    beat_text = str(
        beat_text or ""
    ).strip()

    if not beat_text:
        return {}

    context_text = str(
        context_text or ""
    ).strip()

    prompt = f"""
You are the visual editor of a serious historical documentary.

Narration beat:
{beat_text}

Nearby narration:
{context_text}

Determine what should visually appear during this narration.

Rules:
- the CURRENT narration beat is authoritative for what must visibly appear;
- nearby narration exists only to disambiguate period, place, identity or meaning;
- DO NOT require an object, person, action or event merely because it appears in a neighboring beat;
- must_show must describe only what is necessary to illustrate the CURRENT beat;
- identify exact objects, people, places or documents when they occur in the current beat;
- distinguish primary subject from contextual background;
- use historically precise terminology;
- detect period and place only when supported;
- avoid anachronisms;
- generate concise English search queries;
- primary_queries are for the current beat's exact visual subject;
- context_queries are for useful supporting footage;
- avoid lists visually misleading material that should not be selected;
- never invent unsupported historical facts.
""".strip()

    try:
        response = client.responses.create(
            model=DEFAULT_MODEL,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "obardo_visual_plan",
                    "strict": True,
                    "schema": VISUAL_PLAN_SCHEMA,
                }
            },
        )

        return _extract_json(
            response
        )

    except Exception:
        return {}


IMAGE_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "historical_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "object_match": {
            "type": "boolean"
        },
        "anachronism": {
            "type": "boolean"
        },
        "documentary_value": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "decision": {
            "type": "string",
            "enum": [
                "accept",
                "weak",
                "reject",
            ],
        },
        "reason": {
            "type": "string"
        },
    },
    "required": [
        "relevance_score",
        "historical_score",
        "object_match",
        "anachronism",
        "documentary_value",
        "decision",
        "reason",
    ],
    "additionalProperties": False,
}


def evaluate_image_candidate(
    image_path: str | Path,
    *,
    beat_text: str,
    context_text: str | None = None,
    visual_plan: dict | None = None,
) -> dict:
    client = _client()

    if client is None:
        return {}

    path = Path(
        image_path
    )

    if not path.exists():
        return {}

    visual_plan = (
        dict(visual_plan)
        if isinstance(visual_plan, dict)
        else {}
    )

    plan = json.dumps(
        visual_plan,
        ensure_ascii=False,
    )

    background_policy = ""

    if (
        visual_plan.get("fallback_mode")
        == "period_safe_background"
    ):
        background_policy = """
SPECIAL BACKGROUND POLICY:
- this candidate is contextual historical background;
- prefer empty architecture, ruins, archaeological sites, landscapes,
  artifacts, interiors and environmental details;
- clearly visible modern people, tourists or contemporary clothing
  are not allowed;
- modern cars, buses, motorcycles, electronics, prominent signs
  or other strongly contemporary elements are not allowed;
- reject when such elements are clearly visible;
- a surviving historical site filmed today is acceptable only when
  the selected frame remains visually period-neutral;
- the image must remain compatible with the narration's supported
  historical period and place.
""".strip()

    elif (
        visual_plan.get("fallback_mode")
        == "historical_image_review"
    ):
        background_policy = """
SPECIAL HISTORICAL IMAGE POLICY:
- the candidate may be a primary historical artifact, fresco, mosaic,
  relief, sculpture, archaeological remain or surviving monument;
- the candidate may also be a later painting, engraving, drawing,
  academic illustration or other artistic reconstruction;
- do NOT reject an artwork merely because it was created centuries
  after the historical period being narrated;
- for a later artwork, judge the historical period represented by
  the scene, not simply the artwork's creation date;
- accept later artistic depictions when the visible clothing,
  architecture, objects, people and setting are reasonably compatible
  with the narrated historical period;
- reject a later artwork when it actually depicts the artist's own
  later period, or another incompatible period, while merely including
  ancient ruins or historical references;
- distinguish an authentic period artifact from a later historical
  reconstruction, but both may be useful documentary visuals;
- reject images whose visible content would materially mislead the
  viewer about the narrated period, place or subject.
""".strip()

    prompt = f"""
You are judging a candidate visual for a historical documentary.

Narration:
{beat_text}

Nearby narration:
{context_text or ""}

Visual plan:
{plan}

Additional candidate policy:
{background_policy}

Judge the supplied image.

Important:
- correspondence to the exact narration matters more than broad thematic similarity;
- if a specific artifact, person, monument or document is required,
  verify whether it is actually visible;
- a Roman monument is not automatically suitable for every Ancient Rome beat;
- penalize true historical anachronisms and semantically misleading imagery;
- for normal specific-subject documentary footage, incidental tourists,
  cars, roads, signs or modern buildings do not automatically make
  surviving historical subjects unusable;
- however, when fallback_mode=period_safe_background, enforce the
  SPECIAL BACKGROUND POLICY and reject clearly visible modern people
  or intrusive contemporary elements;
- outside that special background mode, mark anachronism=true only when
  the modern element makes the historical subject misleading, incorrect
  or falsely presented as belonging to the period;
- distinguish "historical reconstruction" from "present-day documentary footage
  of a historical subject";
- assess documentary usefulness.
""".strip()

    try:
        response = client.responses.create(
            model=DEFAULT_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": _image_data_url(
                                path
                            ),
                            "detail": DEFAULT_DETAIL,
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "obardo_image_score",
                    "strict": True,
                    "schema": IMAGE_SCORE_SCHEMA,
                }
            },
        )

        return _extract_json(
            response
        )

    except Exception:
        return {}


def should_reject(
    evaluation: dict,
) -> bool:
    if not evaluation:
        return False

    if evaluation.get(
        "anachronism"
    ):
        return True

    if evaluation.get(
        "decision"
    ) == "reject":
        return True

    if editorial_mode() == "strict":
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

        if relevance < 60:
            return True

        if historical < 55:
            return True

    return False
