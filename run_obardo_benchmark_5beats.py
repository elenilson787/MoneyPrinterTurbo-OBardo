from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from app.models.schema import VideoAspect
from app.services import task
from app.services import obardo_video_source


# ============================================================
# O BARDO TURBO - BENCHMARK REAL DE 5 BEATS
# ============================================================

BEATS = [
    {
        "text": (
            "Desde cedo, multidões se dirigiam ao Coliseu para "
            "acompanhar os espetáculos públicos de Roma."
        ),
        "duration": 6.0,
        "visual_segment": 1,
        "source_search_terms": [
            "ancient Rome crowd walking to Colosseum reconstruction",
            "Colosseum spectators Roman crowd historical illustration",
        ],
    },
    {
        "text": (
            "A posição de cada pessoa nas arquibancadas refletia "
            "a própria hierarquia social romana."
        ),
        "duration": 6.0,
        "visual_segment": 1,
        "source_search_terms": [
            "Roman Colosseum seating social hierarchy reconstruction",
            "Colosseum upper seats Roman social classes reconstruction",
        ],
    },
    {
        "text": (
            "Abaixo da arena havia corredores, jaulas, equipamentos "
            "e áreas de serviço utilizados na preparação dos espetáculos."
        ),
        "duration": 6.0,
        "visual_segment": 1,
        "source_search_terms": [
            "Colosseum hypogeum underground reconstruction",
            "Roman Colosseum underground corridors archaeology",
        ],
    },
    {
        "text": (
            "As condenações públicas podiam incluir execuções ou "
            "a exposição de condenados a animais diante da multidão."
        ),
        "duration": 6.0,
        "visual_segment": 1,
        "source_search_terms": [
            "Roman public execution arena historical illustration",
            "Roman damnatio ad bestias historical art",
        ],
    },
    {
        "text": (
            "A punição pública também demonstrava o poder das "
            "autoridades romanas diante de milhares de espectadores."
        ),
        "duration": 6.0,
        "visual_segment": 1,
        "source_search_terms": [
            "Roman imperial authority public spectacle amphitheatre",
            "Roman crowd spectators amphitheatre historical painting",
        ],
    },
]


# ============================================================
# MEDIÇÃO NÃO INVASIVA
# ============================================================

stats = {
    "master": [],
    "historical": [],
    "video": [],
}


_original_master = (
    obardo_video_source.resolve_media_for_beat
)

_original_historical = (
    obardo_video_source.resolve_historical_image_for_beat
)

_original_video = (
    obardo_video_source.resolve_video_for_beat
)


def timed_master(*args, **kwargs):
    started = time.perf_counter()

    try:
        result = _original_master(
            *args,
            **kwargs,
        )

        return result

    finally:
        elapsed = (
            time.perf_counter()
            - started
        )

        route = ""

        try:
            route = str(
                result.get("route") or ""
            )
        except Exception:
            pass

        stats["master"].append(
            {
                "seconds": elapsed,
                "route": route,
            }
        )

        print(
            "\n[BENCH] MASTER "
            f"{elapsed:.2f}s "
            f"route={route!r}"
        )


def timed_historical(*args, **kwargs):
    started = time.perf_counter()

    try:
        return _original_historical(
            *args,
            **kwargs,
        )

    finally:
        elapsed = (
            time.perf_counter()
            - started
        )

        stats["historical"].append(
            elapsed
        )

        print(
            "[BENCH] WIKIMEDIA "
            f"{elapsed:.2f}s"
        )


def timed_video(*args, **kwargs):
    started = time.perf_counter()

    try:
        return _original_video(
            *args,
            **kwargs,
        )

    finally:
        elapsed = (
            time.perf_counter()
            - started
        )

        stats["video"].append(
            elapsed
        )

        print(
            "[BENCH] VIDEO "
            f"{elapsed:.2f}s"
        )


obardo_video_source.resolve_media_for_beat = (
    timed_master
)

obardo_video_source.resolve_historical_image_for_beat = (
    timed_historical
)

obardo_video_source.resolve_video_for_beat = (
    timed_video
)


# ============================================================
# EXECUÇÃO
# ============================================================

task_id = (
    "obardo-benchmark-5beats-"
    + uuid4().hex[:8]
)

print("=" * 72)
print("O BARDO TURBO - BENCHMARK 5 BEATS")
print("=" * 72)
print("TASK ID:", task_id)
print("BEATS:", len(BEATS))
print(
    "DURACAO VISUAL TOTAL:",
    sum(
        float(item["duration"])
        for item in BEATS
    ),
    "s",
)
print("=" * 72)

benchmark_start = time.perf_counter()

try:
    paths = (
        task._download_obardo_resolver_by_timeline(
            task_id=task_id,
            editorial_timeline=BEATS,
            video_aspect=VideoAspect.landscape,
        )
    )

finally:
    total_elapsed = (
        time.perf_counter()
        - benchmark_start
    )


# ============================================================
# RELATÓRIO
# ============================================================

print()
print("=" * 72)
print("O BARDO TURBO - RESULTADO DO BENCHMARK")
print("=" * 72)

print(
    f"TEMPO TOTAL: {total_elapsed:.2f}s "
    f"({total_elapsed / 60:.2f} min)"
)

print(
    f"MEDIA POR BEAT: "
    f"{total_elapsed / len(BEATS):.2f}s"
)

print()
print("MASTER RESOLVER:")

for index, item in enumerate(
    stats["master"],
    1,
):
    print(
        f"  chamada {index}: "
        f"{item['seconds']:.2f}s "
        f"route={item['route']!r}"
    )

print()
print(
    "MASTER TOTAL:",
    f"{sum(x['seconds'] for x in stats['master']):.2f}s",
)

print(
    "WIKIMEDIA:",
    f"{len(stats['historical'])} chamadas | "
    f"{sum(stats['historical']):.2f}s",
)

print(
    "VIDEO:",
    f"{len(stats['video'])} chamadas | "
    f"{sum(stats['video']):.2f}s",
)

print()
print("ARQUIVOS GERADOS:")

for index, path in enumerate(
    paths,
    1,
):
    path_obj = Path(path)

    print(
        f"  {index}. {path_obj}"
    )

    if path_obj.exists():
        print(
            "     tamanho:",
            f"{path_obj.stat().st_size / 1024 / 1024:.2f} MB",
        )

print()
print("=" * 72)
print("BENCHMARK FINALIZADO")
print("=" * 72)
