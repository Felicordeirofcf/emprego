"""Pré-filtro determinístico (aceita estágio e júnior), aplicado ANTES do Claude para não gastar tokens à toa.

Só descarta o que é inequívoco. Qualquer dúvida segue para o Claude, que é quem
dá a palavra final sobre elegibilidade.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import Vaga, normalizar_texto

RE_NIVEL_ACEITO = re.compile(r"\b(estagi\w*|intern|internship|junior|jr|trainee)\b")
RE_NIVEL_SUPERIOR = re.compile(
    r"\b(senior|sr|pleno|especialista|tech lead|lider tecnico|coordenador|"
    r"gerente|staff engineer|principal engineer|head of)\b"
)
RE_APRENDIZ = re.compile(r"\b(jovem aprendiz|menor aprendiz|aprendiz)\b")


def avaliar(vaga: Vaga, settings: Settings) -> tuple[bool, str | None]:
    """Retorna (seguir_para_claude, motivo_descarte)."""
    titulo = normalizar_texto(vaga.titulo)

    if vaga.publicada_em is not None:
        limite = datetime.now(timezone.utc) - timedelta(days=settings.dias_maximos_vaga)
        if vaga.publicada_em < limite:
            return False, f"Publicada há mais de {settings.dias_maximos_vaga} dias."

    if RE_APRENDIZ.search(titulo):
        return False, "Vaga de jovem aprendiz, não de estágio."

    # "Júnior ou Pleno", "Estágio/Júnior" etc. seguem para o Claude; só "Pleno", "Sênior"... sem
    # estágio/júnior no título são descartados aqui.
    nivel_aceito = bool(RE_NIVEL_ACEITO.search(titulo))
    if RE_NIVEL_SUPERIOR.search(titulo) and not nivel_aceito:
        nivel = RE_NIVEL_SUPERIOR.search(titulo).group(1)  # type: ignore[union-attr]
        return False, f"Nível '{nivel}' no título — não é estágio nem júnior."

    if vaga.modalidade in {"presencial", "hibrido"} and vaga.localizacao:
        loc = normalizar_texto(vaga.localizacao)
        uf = normalizar_texto(settings.uf_presencial)
        estado = normalizar_texto(settings.estado_presencial)
        if estado not in loc and not re.search(rf"\b{re.escape(uf)}\b", loc):
            return False, f"Vaga {vaga.modalidade} fora do {settings.uf_presencial} ({vaga.localizacao})."

    return True, None
