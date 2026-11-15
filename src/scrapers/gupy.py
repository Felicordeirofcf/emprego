"""Coletor da busca pública do portal da Gupy (portal.gupy.io).

Em out/2026 a Gupy desligou os hosts antigos (portal.api.gupy.io e
employability-portal.gupy.io: 404 em todos os caminhos). O endpoint atual é o mesmo
que a busca do próprio portal usa — não é documentado e pode mudar sem aviso:

    GET https://portal.gupy.io/api/job-search/jobs?jobName=<termo>&type=<tipo>&limit=<n>&offset=<n>

- ``limit`` vai até 100 (101 devolve 400); ``offset`` >= 10000 devolve 400.
- Resposta: {"data": [{id, name, careerPageName, description, type, workplaceType,
  city, state, jobUrl|url, publishedDate, ...}], "pagination": {"offset", "limit", "total"}}
  (os campos isRemoteWork, country e companyId deixaram de existir: o remoto vem de
  workplaceType). Resultados do mais novo para o mais antigo.
- ``type`` identifica o nível ("vacancy_type_internship" = estágio,
  "vacancy_type_apprentice" = jovem aprendiz, "vacancy_type_effective" = CLT...).
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from ..models import Vaga, normalizar_texto
from .base import HttpClient, JobScraper, ScraperError, parse_data_iso

log = logging.getLogger(__name__)

API = "https://portal.gupy.io/api/job-search/jobs"
LIMITE = 100  # máximo aceito pelo servidor
MAX_PAGINAS = 3

TIPO_ESTAGIO = "vacancy_type_internship"
RE_CONFIDENCIAL = re.compile(r"^(?:empresa\s+)?confidencial$", re.IGNORECASE)
MAPA_MODALIDADE = {"remote": "remoto", "hybrid": "hibrido", "on-site": "presencial", "onsite": "presencial"}


class GupyScraper(JobScraper):
    nome = "gupy"

    def criar_http(self) -> HttpClient:
        # A API é pública, mas educação com o servidor: 1 req/s.
        return HttpClient(headers={"Accept": "application/json"}, intervalo_minimo=1.0)

    @staticmethod
    def eh_estagio(item: dict) -> bool:
        tipo = (item.get("type") or "").lower()
        if tipo == TIPO_ESTAGIO:
            return True
        if tipo and tipo != TIPO_ESTAGIO and "intern" not in tipo:
            # Tipo explícito diferente de estágio (CLT, aprendiz, temporário...)
            return False
        return "estagi" in normalizar_texto(item.get("name", ""))

    @staticmethod
    def eh_junior(item: dict) -> bool:
        tipo = (item.get("type") or "").lower()
        if "apprentice" in tipo:
            return False
        return bool(re.search(r"\b(junior|jr)\b", normalizar_texto(item.get("name", ""))))

    @staticmethod
    def normalizar(item: dict) -> Vaga:
        workplace = (item.get("workplaceType") or "").lower()
        modalidade = MAPA_MODALIDADE.get(workplace)
        if modalidade is None and item.get("isRemoteWork"):
            modalidade = "remoto"

        cidade = (item.get("city") or "").strip()
        estado = (item.get("state") or "").strip()
        localizacao = " - ".join(p for p in (cidade, estado) if p) or None

        url = item.get("jobUrl") or item.get("url") or ""
        if not url and item.get("careerPageUrl") and item.get("id"):
            url = f"{item['careerPageUrl'].rstrip('/')}/jobs/{item['id']}"

        return Vaga(
            fonte="gupy",
            url=url,
            titulo=(item.get("name") or "").strip(),
            empresa=_empresa(item.get("careerPageName")),
            descricao=(item.get("description") or "").strip(),
            localizacao=localizacao,
            modalidade=modalidade,
            publicada_em=parse_data_iso(item.get("publishedDate")),
            tags=[t for t in (item.get("type"), workplace) if t],
            id_externo=str(item.get("id")) if item.get("id") is not None else None,
        )

    def buscar_termo(self, termo: str, so_estagio: bool = True) -> Iterable[Vaga]:
        # so_estagio=True: pede só estágio ao servidor (type) e confere localmente.
        # so_estagio=False: busca de júnior, sem filtro de tipo; aceita títulos com Júnior/Jr.
        filtrar_tipo = so_estagio
        for pagina in range(MAX_PAGINAS):
            offset = pagina * LIMITE
            params = {"jobName": termo, "limit": LIMITE, "offset": offset}
            if filtrar_tipo:
                params["type"] = TIPO_ESTAGIO
            resp = self.http.get(API, params=params)
            if resp.status_code == 400 and filtrar_tipo:
                log.warning("[gupy] filtro type recusado (400); repetindo sem ele e filtrando localmente")
                filtrar_tipo = False
                params.pop("type")
                resp = self.http.get(API, params=params)
            if resp.status_code >= 400:
                trecho = resp.text[:200].replace("\n", " ")
                raise ScraperError(f"HTTP {resp.status_code} ao buscar '{termo}' na Gupy ({API}): {trecho}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise ScraperError(f"JSON inválido da Gupy para '{termo}'") from exc

            itens = payload.get("data") or []
            for item in itens:
                if not isinstance(item, dict):
                    continue
                aceito = self.eh_estagio(item) if so_estagio else (self.eh_junior(item) or self.eh_estagio(item))
                if not aceito:
                    continue
                vaga = self.normalizar(item)
                if vaga.url and vaga.titulo:
                    yield vaga

            total = (payload.get("pagination") or {}).get("total")
            if len(itens) < LIMITE or (isinstance(total, int) and offset + LIMITE >= total):
                break

    def coletar(self) -> Iterable[Vaga]:
        buscas = [(t, True) for t in self.settings.gupy_termos]
        if self.settings.incluir_junior:
            buscas += [(t, False) for t in self.settings.gupy_termos_junior]
        for termo, so_estagio in buscas:
            try:
                n = 0
                for vaga in self.buscar_termo(termo, so_estagio=so_estagio):
                    n += 1
                    yield vaga
                log.info("[gupy] '%s': %d vaga(s) de %s", termo, n, "estágio" if so_estagio else "júnior")
            except ScraperError as exc:
                self.registrar_erro(str(exc))
            except Exception as exc:  # noqa: BLE001
                self.registrar_erro(f"'{termo}': {exc.__class__.__name__}: {exc}")


def _empresa(nome: str | None) -> str | None:
    nome = (nome or "").strip()
    if not nome or RE_CONFIDENCIAL.match(nome):
        return None  # vaga de empresa confidencial
    return nome
