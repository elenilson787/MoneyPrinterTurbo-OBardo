from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "O-Bardo-MoneyPrinterTurbo/1.0 "
        "(historical documentary media search)"
    )
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def search_openverse(
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search openly licensed historical images through Openverse.

    Anonymous access is supported by Openverse. Keep requests
    conservative because anonymous clients have lower rate limits.
    """
    query = _safe_text(query)

    if not query:
        return []

    limit = max(
        1,
        min(int(limit or 12), 20),
    )

    url = (
        "https://api.openverse.org/v1/images/"
    )

    params = {
        "q": query,
        "page_size": limit,
        "page": 1,

        # O Bardo is intended for monetizable documentary output.
        # Exclude NonCommercial-only media at search time.
        "license_type": "commercial",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=(15, 30),
        )

        if response.status_code == 429:
            logger.warning(
                "O Bardo OPENVERSE 429 | "
                f"query={query!r}"
            )
            return []

        response.raise_for_status()

        payload = response.json()
        raw_results = (
            payload.get("results")
            or []
        )

    except Exception as exc:
        logger.warning(
            "O Bardo OPENVERSE ERROR | "
            f"query={query!r} | "
            f"{type(exc).__name__}: {exc}"
        )
        return []

    results = []

    for item in raw_results:
        image_url = (
            _safe_text(item.get("url"))
            or _safe_text(
                item.get("thumbnail")
            )
        )

        if not image_url:
            continue

        license_name = _safe_text(
            item.get("license")
        )

        license_version = _safe_text(
            item.get("license_version")
        )

        if license_version:
            license_display = (
                f"{license_name} "
                f"{license_version}"
            ).strip()
        else:
            license_display = license_name

        tags = item.get("tags") or []

        tag_names = []

        for tag in tags[:20]:
            if isinstance(tag, dict):
                tag_name = _safe_text(
                    tag.get("name")
                )
            else:
                tag_name = _safe_text(tag)

            if tag_name:
                tag_names.append(tag_name)

        description = " ".join(
            tag_names
        )

        results.append(
            {
                "provider": "openverse",
                "id": _safe_text(
                    item.get("id")
                ),
                "title": _safe_text(
                    item.get("title")
                ),
                "description": description,
                "creator": _safe_text(
                    item.get("creator")
                ),
                "license": license_display,
                "license_url": _safe_text(
                    item.get("license_url")
                ),
                "image_url": image_url,
                "thumbnail_url": _safe_text(
                    item.get("thumbnail")
                ),
                "source_url": (
                    _safe_text(
                        item.get(
                            "foreign_landing_url"
                        )
                    )
                    or _safe_text(
                        item.get("detail_url")
                    )
                ),
                "source": _safe_text(
                    item.get("source")
                ),
                "width": item.get("width"),
                "height": item.get("height"),
                "semantic_score": 0.0,
                "matched_query": query,
            }
        )

    logger.info(
        "O Bardo OPENVERSE SEARCH | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results


def _loc_pick_image_url(
    item: dict[str, Any],
) -> str:
    """
    LOC search records often expose image derivatives in
    item['image_url'] as a list ordered by available size.
    Prefer a useful larger derivative without forcing an
    enormous preservation master.
    """
    urls = item.get("image_url") or []

    if isinstance(urls, str):
        urls = [urls]

    urls = [
        _safe_text(value)
        for value in urls
        if _safe_text(value)
    ]

    if not urls:
        return ""

    # Larger derivatives are commonly later in the list.
    return urls[-1]



def _smithsonian_api_key() -> str:
    import os

    return _safe_text(
        os.getenv(
            "SMITHSONIAN_API_KEY"
        )
    )


def _smithsonian_pick_media(
    content: dict[str, Any],
) -> tuple[str, str]:
    """
    Return:
        (full_image_url, thumbnail_url)

    Smithsonian Open Access records expose online media under
    descriptiveNonRepeating.online_media.media.
    """
    descriptive = (
        content.get(
            "descriptiveNonRepeating"
        )
        or {}
    )

    online_media = (
        descriptive.get(
            "online_media"
        )
        or {}
    )

    media_items = (
        online_media.get("media")
        or []
    )

    full_url = ""
    thumbnail_url = ""

    for media in media_items:
        if not isinstance(
            media,
            dict,
        ):
            continue

        media_type = _safe_text(
            media.get("type")
        ).lower()

        if (
            media_type
            and "image"
            not in media_type
        ):
            continue

        candidate_full = _safe_text(
            media.get("content")
        )

        candidate_thumb = _safe_text(
            media.get("thumbnail")
        )

        if (
            not candidate_full
            and isinstance(
                media.get("resources"),
                list,
            )
        ):
            for resource in (
                media.get("resources")
                or []
            ):
                if not isinstance(
                    resource,
                    dict,
                ):
                    continue

                candidate_full = (
                    _safe_text(
                        resource.get("url")
                    )
                    or _safe_text(
                        resource.get("content")
                    )
                )

                if candidate_full:
                    break

        if (
            candidate_full
            and not full_url
        ):
            full_url = (
                candidate_full
            )

        if (
            candidate_thumb
            and not thumbnail_url
        ):
            thumbnail_url = (
                candidate_thumb
            )

        if (
            full_url
            and thumbnail_url
        ):
            break

    return (
        full_url,
        thumbnail_url,
    )


def _smithsonian_extract_text(
    content: dict[str, Any],
) -> tuple[
    str,
    str,
    str,
    str,
]:
    """
    Return:
        title, description, creator, source_url
    """
    title = _safe_text(
        content.get("title")
    )

    descriptive = (
        content.get(
            "descriptiveNonRepeating"
        )
        or {}
    )

    indexed = (
        content.get("indexedStructured")
        or {}
    )

    freetext = (
        content.get("freetext")
        or {}
    )

    description_parts = []

    for key in (
        "topic",
        "object_type",
        "place",
        "date",
        "culture",
        "name",
    ):
        value = indexed.get(key)

        if isinstance(
            value,
            list,
        ):
            description_parts.extend(
                _safe_text(item)
                for item in value
                if _safe_text(item)
            )

        elif _safe_text(value):
            description_parts.append(
                _safe_text(value)
            )

    for key in (
        "notes",
        "physicalDescription",
        "date",
        "name",
        "topic",
        "place",
    ):
        entries = (
            freetext.get(key)
            or []
        )

        if isinstance(
            entries,
            dict,
        ):
            entries = [entries]

        if not isinstance(
            entries,
            list,
        ):
            continue

        for entry in entries[:10]:
            if isinstance(
                entry,
                dict,
            ):
                value = _safe_text(
                    entry.get("content")
                )
            else:
                value = _safe_text(
                    entry
                )

            if value:
                description_parts.append(
                    value
                )

    creator = ""

    name_entries = (
        freetext.get("name")
        or []
    )

    if isinstance(
        name_entries,
        dict,
    ):
        name_entries = [
            name_entries
        ]

    for entry in name_entries:
        if isinstance(
            entry,
            dict,
        ):
            creator = _safe_text(
                entry.get("content")
            )

            if creator:
                break

    record_link = _safe_text(
        descriptive.get(
            "record_link"
        )
    )

    return (
        title,
        " ".join(
            description_parts
        ),
        creator,
        record_link,
    )



def search_met_museum(
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search The Metropolitan Museum of Art Open Access collection.

    No API key required.

    Only objects explicitly marked public domain and exposing a
    usable primary image are returned.
    """
    query = _safe_text(query)

    if not query:
        return []

    limit = max(
        1,
        min(
            int(limit or 12),
            30,
        ),
    )

    search_url = (
        "https://collectionapi.metmuseum.org/"
        "public/collection/v1.1/search"
    )

    params = {
        "q": query,
        "hasImages": "true",
    }

    try:
        response = requests.get(
            search_url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=(15, 30),
        )

        if response.status_code == 429:
            logger.warning(
                "O Bardo MET 429 | "
                f"query={query!r}"
            )
            return []

        response.raise_for_status()

        payload = response.json()

        object_ids = (
            payload.get("objectIDs")
            or []
        )

    except Exception as exc:
        logger.warning(
            "O Bardo MET SEARCH ERROR | "
            f"query={query!r} | "
            f"{type(exc).__name__}: {exc}"
        )
        return []

    results = []

    for object_id in object_ids[: max(
        limit * 3,
        limit,
    )]:
        object_url = (
            "https://collectionapi.metmuseum.org/"
            "public/collection/v1/objects/"
            f"{object_id}"
        )

        try:
            response = requests.get(
                object_url,
                headers=DEFAULT_HEADERS,
                timeout=(10, 20),
            )

            if response.status_code == 429:
                logger.warning(
                    "O Bardo MET OBJECT 429 | "
                    f"id={object_id}"
                )
                break

            if response.status_code != 200:
                continue

            item = response.json()

        except Exception:
            continue

        is_public_domain = bool(
            item.get("isPublicDomain")
        )

        image_url = _safe_text(
            item.get("primaryImage")
        )

        thumbnail_url = (
            _safe_text(
                item.get(
                    "primaryImageSmall"
                )
            )
            or image_url
        )

        if (
            not is_public_domain
            or not image_url
        ):
            continue

        title = _safe_text(
            item.get("title")
        )

        description_parts = []

        for field in (
            "objectName",
            "culture",
            "period",
            "dynasty",
            "reign",
            "portfolio",
            "artistDisplayName",
            "artistDisplayBio",
            "objectDate",
            "medium",
            "classification",
            "department",
            "country",
            "region",
            "city",
            "excavation",
        ):
            value = _safe_text(
                item.get(field)
            )

            if value:
                description_parts.append(
                    value
                )

        tags = item.get("tags") or []

        for tag in tags[:20]:
            if isinstance(
                tag,
                dict,
            ):
                value = _safe_text(
                    tag.get("term")
                )

                if value:
                    description_parts.append(
                        value
                    )

        source_url = _safe_text(
            item.get("objectURL")
        )

        results.append(
            {
                "provider": "met_museum",
                "id": str(object_id),
                "title": title,
                "description": " ".join(
                    description_parts
                ),
                "creator": _safe_text(
                    item.get(
                        "artistDisplayName"
                    )
                ),
                "license": (
                    "Public Domain / "
                    "The Met Open Access"
                ),
                "license_url": (
                    "https://www.metmuseum.org/"
                    "about-the-met/"
                    "policies-and-documents/"
                    "open-access"
                ),
                "image_url": image_url,
                "thumbnail_url": (
                    thumbnail_url
                ),
                "source_url": source_url,
                "source": "metmuseum",
                "width": None,
                "height": None,
                "semantic_score": 0.0,
                "matched_query": query,
            }
        )

        if len(results) >= limit:
            break

    logger.info(
        "O Bardo MET SEARCH | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results



def _europeana_api_key() -> str:
    import os

    return _safe_text(
        os.getenv(
            "EUROPEANA_API_KEY"
        )
    )


def _europeana_first(
    value,
) -> str:
    """
    Europeana search fields may be scalar, list or language maps.
    Return the first useful textual value.
    """
    if isinstance(value, str):
        return _safe_text(value)

    if isinstance(value, list):
        for item in value:
            result = _europeana_first(
                item
            )
            if result:
                return result
        return ""

    if isinstance(value, dict):
        # Prefer language-neutral value when present.
        for key in (
            "def",
            "en",
            "eng",
        ):
            if key in value:
                result = _europeana_first(
                    value.get(key)
                )
                if result:
                    return result

        for item in value.values():
            result = _europeana_first(
                item
            )
            if result:
                return result

    return _safe_text(value)


def _europeana_join(
    *values,
) -> str:
    parts = []

    for value in values:
        if isinstance(value, list):
            for item in value:
                text_value = (
                    _europeana_first(item)
                )
                if text_value:
                    parts.append(
                        text_value
                    )

        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(
                    item,
                    list,
                ):
                    for nested in item:
                        text_value = (
                            _europeana_first(
                                nested
                            )
                        )
                        if text_value:
                            parts.append(
                                text_value
                            )
                else:
                    text_value = (
                        _europeana_first(
                            item
                        )
                    )
                    if text_value:
                        parts.append(
                            text_value
                        )

        else:
            text_value = (
                _europeana_first(
                    value
                )
            )
            if text_value:
                parts.append(
                    text_value
                )

    # Stable de-duplication.
    unique = []
    seen = set()

    for part in parts:
        key = part.strip().lower()

        if (
            not key
            or key in seen
        ):
            continue

        seen.add(key)
        unique.append(
            part.strip()
        )

    return " ".join(
        unique
    )


def search_europeana(
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search Europeana cultural heritage collections.

    Requires EUROPEANA_API_KEY.

    Only records classified by Europeana as openly reusable,
    with directly available media, are requested.

    Missing API key is a soft skip.
    """
    query = _safe_text(query)

    if not query:
        return []

    api_key = (
        _europeana_api_key()
    )

    if not api_key:
        logger.info(
            "O Bardo EUROPEANA SKIP | "
            "reason=missing_api_key"
        )
        return []

    limit = max(
        1,
        min(
            int(limit or 12),
            50,
        ),
    )

    url = (
        "https://api.europeana.eu/"
        "record/v2/search.json"
    )

    import re

    retrieval_only_terms = {
        "ancient",
        "historical",
        "historic",
        "scene",
        "image",
        "painting",
        "illustration",
        "depiction",
        "reconstruction",
        "artwork",
        "view",
    }

    raw_tokens = [
        token
        for token in re.findall(
            r"[A-Za-z0-9]+",
            query,
        )
        if token
    ]

    simplified_tokens = [
        token
        for token in raw_tokens
        if token.lower()
        not in retrieval_only_terms
    ]

    simplified_query = " ".join(
        simplified_tokens
    ).strip()

    query_variants = []

    for candidate_query in (
        query,
        simplified_query,
    ):
        candidate_query = str(
            candidate_query
            or ""
        ).strip()

        if (
            candidate_query
            and candidate_query
            not in query_variants
        ):
            query_variants.append(
                candidate_query
            )

    raw_items = []
    successful_query = ""

    for europeana_query in query_variants:
        params = {
            "wskey": api_key,
            "query": europeana_query,
            "rows": limit,
            "start": 1,
            "profile": "rich",
            "media": "true",
            "reusability": "open",
            "qf": "TYPE:IMAGE",
        }

        try:
            response = requests.get(
                url,
                params=params,
                headers=DEFAULT_HEADERS,
                timeout=(15, 40),
            )

            if response.status_code == 429:
                logger.warning(
                    "O Bardo EUROPEANA 429 | "
                    f"query={europeana_query!r}"
                )
                continue

            if response.status_code in (
                401,
                403,
            ):
                logger.warning(
                    "O Bardo EUROPEANA AUTH ERROR | "
                    f"status={response.status_code}"
                )
                return []

            response.raise_for_status()

            payload = response.json()

            if not payload.get(
                "success",
                True,
            ):
                continue

            current_items = (
                payload.get("items")
                or []
            )

            logger.info(
                "O Bardo EUROPEANA QUERY | "
                f"query={europeana_query!r} | "
                f"total={payload.get('totalResults')} | "
                f"returned={len(current_items)}"
            )

            if current_items:
                raw_items = current_items
                successful_query = (
                    europeana_query
                )
                break

        except Exception as exc:
            logger.warning(
                "O Bardo EUROPEANA ERROR | "
                f"query={europeana_query!r} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
            continue

    if not raw_items:
        return []

    results = []

    for item in raw_items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        image_url = (
            _europeana_first(
                item.get(
                    "edmIsShownBy"
                )
            )
            or _europeana_first(
                item.get(
                    "edmPreview"
                )
            )
        )

        thumbnail_url = (
            _europeana_first(
                item.get(
                    "edmPreview"
                )
            )
            or image_url
        )

        if not image_url:
            continue

        source_page = (
            _europeana_first(
                item.get(
                    "edmIsShownAt"
                )
            )
        )

        europeana_id = _safe_text(
            item.get("id")
        )

        if (
            not source_page
            and europeana_id
        ):
            source_page = (
                "https://www.europeana.eu/"
                "item"
                + europeana_id
            )

        title = (
            _europeana_first(
                item.get("title")
            )
            or _europeana_first(
                item.get("dcTitle")
            )
        )

        description = _europeana_join(
            item.get(
                "dcDescription"
            ),
            item.get(
                "dcSubject"
            ),
            item.get(
                "what"
            ),
            item.get(
                "where"
            ),
            item.get(
                "when"
            ),
            item.get(
                "year"
            ),
            item.get(
                "dataProvider"
            ),
        )

        creator = (
            _europeana_first(
                item.get("dcCreator")
            )
            or _europeana_first(
                item.get("who")
            )
        )

        rights_url = (
            _europeana_first(
                item.get("rights")
            )
        )

        rights_lower = (
            rights_url.lower()
        )

        if (
            "creativecommons.org/"
            "publicdomain/zero"
            in rights_lower
        ):
            license_name = "CC0"

        elif (
            "creativecommons.org/"
            "publicdomain/mark"
            in rights_lower
        ):
            license_name = (
                "Public Domain"
            )

        elif (
            "/by-sa/"
            in rights_lower
        ):
            license_name = "CC BY-SA"

        elif (
            "/by/"
            in rights_lower
        ):
            license_name = "CC BY"

        else:
            # Search already requests reusability=open,
            # but keep the exact rights statement if Europeana
            # uses another open-rights URI.
            license_name = (
                rights_url
                or "Europeana Open"
            )

        results.append(
            {
                "provider": (
                    "europeana"
                ),
                "id": europeana_id,
                "title": title,
                "description": (
                    description
                ),
                "creator": creator,
                "license": (
                    license_name
                ),
                "license_url": (
                    rights_url
                ),
                "image_url": (
                    image_url
                ),
                "thumbnail_url": (
                    thumbnail_url
                ),
                "source_url": (
                    source_page
                ),
                "source": (
                    "europeana"
                ),
                "width": None,
                "height": None,
                "semantic_score": 0.0,
                "matched_query": (
                    successful_query
                    or query
                ),
            }
        )

    logger.info(
        "O Bardo EUROPEANA SEARCH | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results


def search_smithsonian(
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search Smithsonian Open Access.

    Requires SMITHSONIAN_API_KEY.

    Missing key is a soft skip so the historical pipeline can keep
    running with other providers.
    """
    query = _safe_text(query)

    if not query:
        return []

    api_key = (
        _smithsonian_api_key()
    )

    if not api_key:
        logger.info(
            "O Bardo SMITHSONIAN SKIP | "
            "reason=missing_api_key"
        )
        return []

    limit = max(
        1,
        min(
            int(limit or 12),
            50,
        ),
    )

    url = (
        "https://api.si.edu/"
        "openaccess/api/v1.0/search"
    )

    # Smithsonian supports normal free-text search. We request more
    # results than strictly necessary because records without usable
    # image media are discarded locally.
    params = {
        "q": query,
        "start": 0,
        "rows": limit,
        "sort": "relevancy",
        "api_key": api_key,
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=(15, 40),
        )

        if response.status_code == 429:
            logger.warning(
                "O Bardo SMITHSONIAN 429 | "
                f"query={query!r}"
            )
            return []

        if response.status_code in (
            401,
            403,
        ):
            logger.warning(
                "O Bardo SMITHSONIAN AUTH ERROR | "
                f"status={response.status_code}"
            )
            return []

        response.raise_for_status()

        payload = response.json()

        raw_rows = (
            (
                payload.get("response")
                or {}
            ).get("rows")
            or []
        )

    except Exception as exc:
        logger.warning(
            "O Bardo SMITHSONIAN ERROR | "
            f"query={query!r} | "
            f"{type(exc).__name__}: "
            f"{exc}"
        )
        return []

    results = []

    for row in raw_rows:
        if not isinstance(
            row,
            dict,
        ):
            continue

        content = (
            row.get("content")
            or {}
        )

        if not isinstance(
            content,
            dict,
        ):
            continue

        (
            image_url,
            thumbnail_url,
        ) = _smithsonian_pick_media(
            content
        )

        if not image_url:
            continue

        (
            title,
            description,
            creator,
            source_url,
        ) = _smithsonian_extract_text(
            content
        )

        record_id = _safe_text(
            row.get("id")
        )

        descriptive = (
            content.get(
                "descriptiveNonRepeating"
            )
            or {}
        )

        metadata_usage = (
            descriptive.get(
                "metadata_usage"
            )
            or {}
        )

        access = _safe_text(
            metadata_usage.get(
                "access"
            )
        )

        # For automatic documentary use we only want Smithsonian
        # records explicitly exposed as CC0/Open Access media.
        if access.lower() != "cc0":
            continue

        if (
            not source_url
            and record_id
        ):
            source_url = (
                "https://www.si.edu/object/"
                + record_id
            )

        results.append(
            {
                "provider": (
                    "smithsonian"
                ),
                "id": record_id,
                "title": title,
                "description": (
                    description
                ),
                "creator": creator,
                "license": "CC0",
                "license_url": (
                    "https://creativecommons.org/"
                    "publicdomain/zero/1.0/"
                ),
                "image_url": image_url,
                "thumbnail_url": (
                    thumbnail_url
                    or image_url
                ),
                "source_url": source_url,
                "source": (
                    "smithsonian"
                ),
                "width": None,
                "height": None,
                "semantic_score": 0.0,
                "matched_query": query,
            }
        )

    logger.info(
        "O Bardo SMITHSONIAN SEARCH | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results


def search_library_of_congress(
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search the Library of Congress photo/print/drawing endpoint.
    """
    query = _safe_text(query)

    if not query:
        return []

    limit = max(
        1,
        min(int(limit or 12), 25),
    )

    url = "https://www.loc.gov/photos/"

    params = {
        "q": query,
        "fo": "json",
        "c": limit,
        "sp": 1,
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=(15, 30),
        )

        if response.status_code == 429:
            logger.warning(
                "O Bardo LOC 429 | "
                f"query={query!r}"
            )
            return []

        response.raise_for_status()

        payload = response.json()
        raw_results = (
            payload.get("results")
            or []
        )

    except Exception as exc:
        logger.warning(
            "O Bardo LOC ERROR | "
            f"query={query!r} | "
            f"{type(exc).__name__}: {exc}"
        )
        return []

    results = []

    for item in raw_results:
        image_url = _loc_pick_image_url(
            item
        )

        if not image_url:
            continue

        title = _safe_text(
            item.get("title")
        )

        description_parts = []

        for field in (
            "description",
            "other_title",
            "subject",
            "location",
        ):
            value = item.get(field)

            if isinstance(value, list):
                description_parts.extend(
                    _safe_text(v)
                    for v in value
                    if _safe_text(v)
                )
            elif _safe_text(value):
                description_parts.append(
                    _safe_text(value)
                )

        item_url = (
            _safe_text(item.get("id"))
            or _safe_text(item.get("url"))
        )

        results.append(
            {
                "provider": (
                    "library_of_congress"
                ),
                "id": item_url,
                "title": title,
                "description": " ".join(
                    description_parts
                ),
                "creator": "",
                "license": (
                    "See Library of Congress "
                    "rights/advisory metadata"
                ),
                "license_url": "",
                "image_url": image_url,
                "thumbnail_url": (
                    image_url
                ),
                "source_url": item_url,
                "source": "loc.gov",
                "width": None,
                "height": None,
                "semantic_score": 0.0,
                "matched_query": query,
            }
        )

    logger.info(
        "O Bardo LOC SEARCH | "
        f"query={query!r} | "
        f"results={len(results)}"
    )

    return results


def _normalize_query_text(
    value: Any,
) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize(
        "NFKD",
        _safe_text(value).lower(),
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    ).strip()


def _provider_relevance_score(
    item: dict[str, Any],
    query: str,
) -> float:
    """
    Lightweight provider-agnostic lexical ranking.

    This does NOT replace OpenAI editorial review. Its job is only
    to prevent obviously weak provider results from occupying the
    finalist slots.
    """
    query_text = _normalize_query_text(
        query
    )

    title = _normalize_query_text(
        item.get("title")
    )

    description = _normalize_query_text(
        item.get("description")
    )

    score = 0.0

    stopwords = {
        "the",
        "and",
        "of",
        "in",
        "a",
        "an",
        "de",
        "da",
        "do",
        "dos",
        "das",
        "em",
    }

    query_tokens = [
        token
        for token in query_text.split()
        if (
            len(token) >= 3
            and token not in stopwords
        )
    ]

    context_only_tokens = {
        # Historical / geographic context
        "ancient",
        "roman",
        "romano",
        "romana",
        "rome",
        "roma",
        "greek",
        "grego",
        "grega",
        "greece",
        "historical",
        "historic",

        # Visual-form words are retrieval modifiers,
        # not the historical subject itself.
        "scene",
        "painting",
        "illustration",
        "image",
        "photo",
        "photograph",
        "reconstruction",
        "art",
        "artwork",
        "depiction",
        "view",
    }

    subject_tokens = [
        token
        for token in query_tokens
        if token not in context_only_tokens
    ]

    matched_title = set()
    matched_description = set()

    title_tokens = set(
        title.split()
    )

    description_tokens = set(
        description.split()
    )

    def token_matches(
        query_token: str,
        candidate_tokens: set[str],
    ) -> bool:
        """
        Exact lexical matching with conservative plural support.

        Important:
        strigil -> strigil / strigils
        strigil -> NOT strigillated
        """
        variants = {
            query_token,
            query_token + "s",
            query_token + "es",
        }

        if query_token.endswith("y"):
            variants.add(
                query_token[:-1] + "ies"
            )

        # Conservative reverse plural support.
        if (
            query_token.endswith("s")
            and len(query_token) > 3
        ):
            variants.add(
                query_token[:-1]
            )

        if (
            query_token.endswith("ies")
            and len(query_token) > 4
        ):
            variants.add(
                query_token[:-3] + "y"
            )

        return bool(
            variants
            & candidate_tokens
        )

    for token in query_tokens:
        if token_matches(
            token,
            title_tokens,
        ):
            score += 12.0
            matched_title.add(token)

        elif token_matches(
            token,
            description_tokens,
        ):
            # Description is supporting evidence only.
            score += 2.0
            matched_description.add(token)

    matched_concepts = (
        matched_title
        | matched_description
    )

    # Multiple independent concepts from the query are stronger
    # evidence than repeated generic words in metadata.
    # Cross-concept coverage is most trustworthy when at least
    # one concept is explicitly present in the title.
    if matched_title:
        if len(matched_concepts) >= 2:
            score += 8.0

        if len(matched_concepts) >= 3:
            score += 8.0

    # Multiple concepts in the title are especially strong.
    if len(matched_title) >= 2:
        score += 12.0

    if len(matched_title) >= 3:
        score += 10.0

    # Useful historical evidence.
    historical_markers = (
        "ancient",
        "roman",
        "rome",
        "greek",
        "archaeology",
        "archaeological",
        "antique",
        "artifact",
        "artefact",
        "fresco",
        "relief",
        "ruins",
        "historic",
        "historical",
        "medieval",
    )

    combined = (
        f"{title} {description}"
    )

    historical_matches = [
        marker
        for marker in historical_markers
        if marker in combined
    ]

    score += min(
        len(historical_matches) * 2.0,
        8.0,
    )

    provider_name = _safe_text(
        item.get("provider")
    ).lower()

    provider_bonus = {
        "met_museum": 8.0,
        "smithsonian": 8.0,
        "library_of_congress": 5.0,
        "openverse": 0.0,
    }.get(
        provider_name,
        0.0,
    )

    score += provider_bonus

    combined_tokens = (
        title_tokens
        | description_tokens
    )

    subject_matches = [
        token
        for token in subject_tokens
        if token_matches(
            token,
            combined_tokens,
        )
    ]

    item["provider_subject_match"] = bool(
        subject_matches
        or not subject_tokens
    )

    # ------------------------------------------------------------
    # Broad historical-scene semantic gate
    # ------------------------------------------------------------
    #
    # Terms such as "market" may appear in museum provenance,
    # excavation location or catalog notes without being the visual
    # subject of the image. For market scenes, description-only
    # evidence therefore requires commercial/action support.
    #
    if "market" in subject_tokens:
        market_title_match = token_matches(
            "market",
            title_tokens,
        )

        market_description_match = token_matches(
            "market",
            description_tokens,
        )

        market_activity_tokens = {
            "merchant",
            "merchants",
            "trade",
            "commerce",
            "commercial",
            "vendor",
            "vendors",
            "stall",
            "stalls",
            "shop",
            "shops",
            "marketplace",
            "selling",
            "seller",
            "sellers",
            "buyer",
            "buyers",
        }

        market_activity_match = bool(
            market_activity_tokens
            & description_tokens
        )

        market_subject_valid = bool(
            market_title_match
            or (
                market_description_match
                and market_activity_match
            )
        )

        if not market_subject_valid:
            item["provider_subject_match"] = False

    item["provider_relevance_score"] = (
        score
    )

    item["provider_relevance_reasons"] = {
        "title_matches": sorted(
            matched_title
        ),
        "description_matches": sorted(
            matched_description
        ),
        "matched_concepts": sorted(
            matched_concepts
        ),
        "historical_matches": (
            historical_matches[:5]
        ),
        "provider_bonus": provider_bonus,
        "subject_tokens": subject_tokens,
        "subject_matches": subject_matches,
    }

    return score


def _historical_route(
    query: str,
) -> str:
    text = _normalize_query_text(
        query
    )

    ancient_markers = (
        "ancient",
        "roman",
        "rome",
        "romano",
        "romana",
        "greek",
        "greece",
        "grego",
        "grega",
        "pompeii",
        "pompeia",
        "colosseum",
        "coliseu",
        "gladiator",
        "strigil",
        "thermae",
        "medieval",
        "middle ages",
        "idade media",
    )

    modern_archive_markers = (
        "photograph",
        "photo",
        "photography",
        "fotografia",
        "civil war",
        "world war",
        "wwi",
        "wwii",
        "nineteenth century",
        "19th century",
        "twentieth century",
        "20th century",
        "1800",
        "1900",
    )

    if any(
        marker in text
        for marker in ancient_markers
    ):
        return "ancient"

    if any(
        marker in text
        for marker in modern_archive_markers
    ):
        return "modern_archive"

    return "general"


def search_historical_providers(
    query: str,
    *,
    limit_per_provider: int = 12,
) -> list[dict[str, Any]]:
    """
    Multi-provider historical search with basic source routing.

    Ancient/medieval subjects do not automatically query the
    Library of Congress because it is usually a poor fit for those
    beats. Other archive/museum providers can be inserted here.
    """
    route = _historical_route(
        query
    )

    if route == "ancient":
        provider_calls = (
            search_met_museum,
            search_europeana,
            search_smithsonian,
            search_openverse,
        )

    elif route == "modern_archive":
        provider_calls = (
            search_library_of_congress,
            search_europeana,
            search_smithsonian,
            search_openverse,
        )

    else:
        provider_calls = (
            search_europeana,
            search_smithsonian,
            search_openverse,
            search_library_of_congress,
        )

    combined = []

    for provider_call in provider_calls:
        try:
            provider_results = (
                provider_call(
                    query,
                    limit=limit_per_provider,
                )
            )

            combined.extend(
                provider_results
            )

        except Exception as exc:
            logger.warning(
                "O Bardo HISTORICAL PROVIDER FAILED | "
                f"provider="
                f"{provider_call.__name__} | "
                f"{type(exc).__name__}: {exc}"
            )

    seen = set()
    unique = []

    for item in combined:
        key = (
            _safe_text(
                item.get("image_url")
            ).split("?")[0]
            or _safe_text(
                item.get("source_url")
            )
        )

        if not key or key in seen:
            continue

        seen.add(key)

        _provider_relevance_score(
            item,
            query,
        )

        # Ancient/medieval routes require at least some historical
        # evidence in the candidate metadata. This prevents a modern
        # Market Hall or contemporary artwork from surviving merely
        # because one generic query token matches the title.
        if route == "ancient":
            reasons = (
                item.get(
                    "provider_relevance_reasons"
                )
                or {}
            )

            historical_matches = (
                reasons.get(
                    "historical_matches"
                )
                or []
            )

            if not historical_matches:
                item[
                    "provider_relevance_score"
                ] = (
                    float(
                        item.get(
                            "provider_relevance_score",
                            0,
                        )
                        or 0
                    )
                    - 8.0
                )

        # A specific historical search must contain evidence of its
        # actual subject, not merely the period/location context.
        if not bool(
            item.get(
                "provider_subject_match",
                True,
            )
        ):
            continue

        # Do not spend downloads/editorial calls on extremely weak
        # provider results.
        if float(
            item.get(
                "provider_relevance_score",
                0,
            )
            or 0
        ) < 8.0:
            continue

        unique.append(item)

    unique.sort(
        key=lambda item: (
            float(
                item.get(
                    "provider_relevance_score",
                    0,
                )
                or 0
            ),
            _safe_text(
                item.get("title")
            ),
        ),
        reverse=True,
    )

    logger.info(
        "O Bardo HISTORICAL MULTISOURCE | "
        f"query={query!r} | "
        f"route={route!r} | "
        f"results={len(unique)}"
    )

    return unique
