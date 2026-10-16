from pathlib import Path
from uuid import uuid4
import re

from app.models.schema import VideoParams
from app.services import task

# ---------------------------------------------------------
# PILOTO: termos editoriais locais
#
# O config atual aponta o LLM principal para Moonshot, mas
# nao ha API key. Para este piloto o roteiro ja esta pronto,
# portanto nao precisamos gastar/chamar outro LLM apenas para
# gerar search terms.
# ---------------------------------------------------------

PILOT_SEARCH_TERMS = [
    "ancient Rome crowd walking to Colosseum reconstruction",
    "Colosseum spectators Roman crowd historical illustration",
    "Roman Colosseum seating social hierarchy reconstruction",
    "Colosseum corridors stairs vomitoria Roman architecture",
    "Colosseum arena hypogeum underground reconstruction",
    "Roman public execution arena historical illustration",
    "Roman damnatio ad bestias historical art",
    "Roman imperial authority public spectacle amphitheatre",
    "Roman crowd spectators amphitheatre historical painting",
    "Colosseum upper seats Roman social classes reconstruction",
    "Colosseum imperial Rome crowd vendors spectators",
    "Roman amphitheatre entrance crowd reconstruction",
    "Roman public spectacle audience historical illustration",
    "Colosseum power punishment society ancient Rome",
    "Roman Colosseum crowd historical reconstruction",
]

def _pilot_generate_terms(
    task_id,
    params,
    video_script,
):
    print()
    print(
        "O BARDO PILOT: usando termos editoriais locais "
        "(Moonshot ignorado nesta etapa)"
    )
    print(
        f"O BARDO PILOT: {len(PILOT_SEARCH_TERMS)} "
        "termos preparados"
    )

    return list(PILOT_SEARCH_TERMS)

task.generate_terms = _pilot_generate_terms


# ---------------------------------------------------------
# PILOTO O BARDO - ~5 MINUTOS
# ---------------------------------------------------------

subject = (
    "Como Era Assistir a uma Condenação Pública "
    "no Coliseu Romano"
)

script = """
Imagine atravessar Roma em um dia de espetáculo público e perceber que milhares de pessoas caminham na mesma direção. O destino é o Coliseu, o enorme anfiteatro inaugurado no século I, um dos principais espaços de entretenimento da capital imperial.

Para quem vivia em Roma, assistir a uma condenação pública não era uma experiência clandestina ou incomum. Execuções, punições e diferentes formas de espetáculo podiam fazer parte da programação oferecida ao público, misturadas a outros eventos realizados ao longo do dia.

Desde cedo, multidões se dirigiam ao anfiteatro. A posição de cada pessoa nas arquibancadas não era aleatória. A organização dos assentos refletia a própria hierarquia social romana. As áreas mais próximas da arena eram reservadas aos grupos de maior prestígio, enquanto setores superiores recebiam pessoas de posição social mais baixa.

Ao entrar no Coliseu, o espectador atravessava corredores, escadas e passagens projetados para distribuir grandes multidões pelo edifício. A engenharia do anfiteatro permitia que milhares de pessoas ocupassem seus lugares e se deslocassem por uma estrutura monumental de pedra e concreto.

No centro estava a arena. Abaixo dela havia corredores, jaulas, equipamentos e áreas de serviço utilizadas na preparação dos espetáculos. Dependendo do período e do tipo de evento, animais, condenados e trabalhadores podiam permanecer nessas áreas antes de aparecer diante da multidão.

As condenações públicas podiam assumir formas diferentes. Algumas pessoas eram executadas diretamente. Outras podiam ser expostas a animais ou colocadas em situações encenadas de grande violência. Em certos casos, mitos e histórias conhecidas podiam servir como referência para a apresentação da punição.

Para o público, aquilo não era apenas uma sentença judicial. Era também um espetáculo político e social. A punição era exibida diante de milhares de pessoas e ajudava a demonstrar o poder das autoridades e a capacidade do Estado romano de controlar a ordem pública.

O comportamento da multidão também fazia parte do evento. Gritos, reações, comentários e expectativas transformavam a execução em uma experiência coletiva. O anfiteatro amplificava visualmente a relação entre quem estava na arena e quem observava das arquibancadas.

Nem todos os espectadores enxergavam a mesma coisa da mesma maneira. A distância da arena, a posição social e o lugar ocupado no anfiteatro mudavam completamente a experiência. Para alguém sentado nos setores superiores, a cena podia parecer distante. Para membros da elite próximos da arena, a visão era muito mais direta.

Também é importante evitar a imagem de que todos os espetáculos do Coliseu eram idênticos. A programação variou ao longo dos séculos, assim como as práticas de punição, os tipos de apresentações e o próprio uso do anfiteatro.

O Coliseu que vemos hoje é uma ruína monumental, mas durante o Império era um espaço vivo, cheio de funcionários, vendedores, espectadores, animais, equipamentos e sons. A experiência de assistir a uma condenação pública envolvia muito mais do que observar apenas o momento final da punição.

Havia a caminhada até o anfiteatro, a entrada, a procura pelo setor correto, o movimento da multidão, a espera, a expectativa e finalmente a aparição dos condenados na arena.

Para um romano daquela época, aquilo podia representar entretenimento, ritual público, demonstração de autoridade ou simplesmente uma parte normal de um dia de espetáculos.

Para nós, observar essa experiência hoje exige cuidado. Não podemos tratar a violência antiga como simples entretenimento exótico. O Coliseu também foi um espaço onde poder, desigualdade, punição e espetáculo se encontravam diante de uma enorme audiência.

E talvez seja justamente isso que torne o local tão importante para compreender Roma: o anfiteatro não revela apenas como os romanos se divertiam. Ele mostra também como aquela sociedade organizava pessoas, poder e violência diante dos olhos do público.
""".strip()

# ---------------------------------------------------------
# O Bardo Resolver - fonte confirmada pelo task.py
# ---------------------------------------------------------

video_source = "obardo_resolver"

print("=" * 70)
print("O BARDO - PILOTO 5 MIN")
print("=" * 70)
print("Tema:", subject)
print("video_source:", video_source)

# ---------------------------------------------------------
# Monta somente campos realmente existentes no VideoParams
# ---------------------------------------------------------

fields = VideoParams.model_fields

kwargs = {}

def put(name, value):
    if name in fields:
        kwargs[name] = value
        print(f"param: {name} = {value!r}")

put("video_subject", subject)

if "video_script" not in fields:
    print("\nCampos disponíveis:")
    print(sorted(fields.keys()))
    raise RuntimeError(
        "VideoParams nao possui campo video_script."
    )

put("video_script", script)
put("video_source", video_source)
put("match_materials_to_script", True)

put("voice_name", "elevenlabs:liAlPCvGDJ0qsfPupueo:Paulo Becker - Strong and Sentimental")
put("voice_rate", 1.0)

put("video_count", 1)
put("video_clip_duration", 6)

put("video_language", "pt-BR")

# Sem musica neste primeiro teste, se esses campos existirem.
put("bgm_type", "none")
put("bgm_volume", 0.0)

# ---------------------------------------------------------
# Landscape / 16:9
# ---------------------------------------------------------

if "video_aspect" in fields:
    try:
        from app.models.schema import VideoAspect
        kwargs["video_aspect"] = VideoAspect.landscape
        print(
            "param: video_aspect =",
            VideoAspect.landscape,
        )
    except Exception:
        kwargs["video_aspect"] = "16:9"
        print("param: video_aspect = '16:9'")

params = VideoParams(**kwargs)

task_id = (
    "obardo-coliseu-piloto-"
    + uuid4().hex[:8]
)

print()
print("TASK ID:", task_id)
print()
print("INICIANDO PIPELINE REAL...")
print("=" * 70)

result = task.start(
    task_id,
    params,
    stop_at="video",
)

print()
print("=" * 70)
print("PIPELINE FINALIZADO")
print("TASK ID:", task_id)
print("RESULTADO:")
print(result)
print("=" * 70)
