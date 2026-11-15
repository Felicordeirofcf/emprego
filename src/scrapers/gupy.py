"""Coletor da API pública de busca do portal da Gupy (portal.gupy.io).

Endpoint: GET https://portal.api.gupy.io/api/v1/jobs?jobName=<termo>&limit=<n>&offset=<n>
Resposta: {"data": [{id, name, careerPageName, description, type, workplaceType,
           isRemoteWork, city, state, country, jobUrl, publishedDate, ...}],
           "pagination": {"offset", "limit", "total"}}

O campo ``type`` identifica o nível ("vacancy_type_internship" = estágio,
"vacancy_type_apprentice" = jovem aprendiz, "vacancy_type_effective" = CLT...).
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..models import Vaga, normalizar_texto
from .base import HttpClient, JobScraper, ScraperError, parse_data_iso

log = logging.getLogger(__name__)

API = "https://portal.api.gupy.io/api/v1/jobs"
LIMITE = 50
MAX_PAGINAS = 4

TIPO_ESTAGIO = "vacancy_type_internship"
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
    def normalizar(item: dict) -> Vaga:
        workplace = (item.get("workplaceType") or "").lower()
        modalidade = MAPA_MODALIDADE.get(workplace)
        if modalidade is None and item.get("isRemoteWork"):
            modalidade = "remoto"

        cidade = (item.get("city") or "").strip()
        estado = (item.get("state") or "").strip()
        localizacao = " - ".join(p for p in (cidade, estado) if p) or None

        url = item.get("jobUrl") or ""
        if not url and item.get("careerPageUrl") and item.get("id"):
            url = f"{item['careerPageUrl'].rstrip('/')}/jobs/{item['id']}"

        return Vaga(
            fonte="gupy",
            url=url,
            titulo=(item.get("name") or "").strip(),
            empresa=(item.get("careerPageName") or None),
            descricao=(item.get("description") or "").strip(),
            localizacao=localizacao,
            modalidade=modalidade,
            publicada_em=parse_data_iso(item.get("publishedDate")),
            tags=[t for t in (item.get("type"), workplace) if t],
            id_externo=str(item.get("id")) if item.get("id") is not None else None,
        )

    def buscar_termo(self, termo: str) -> Iterable[Vaga]:
        for pagina in range(MAX_PAGINAS):
            offset = pagina * LIMITE
            resp = self.http.get(API, params={"jobName": termo, "limit": LIMITE, "offset": offset})
            if resp.status_code >= 400:
                raise ScraperError(f"HTTP {resp.status_code} ao buscar '{termo}' na Gupy")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise ScraperError(f"JSON inválido da Gupy para '{termo}'") from exc

            itens = payload.get("data") or []
            for item in itens:
                if not isinstance(item, dict) or not self.eh_estagio(item):
                    continue
                vaga = self.normalizar(item)
                if vaga.url and vaga.titulo:
                    yield vaga

            total = (payload.get("pagination") or {}).get("total")
            if len(itens) < LIMITE or (isinstance(total, int) and offset + LIMITE >= total):
                break

    def coletar(self) -> Iterable[Vaga]:
        for termo in self.settings.gupy_termos:
            try:
                n = 0
                for vaga in self.buscar_termo(termo):
                    n += 1
                    yield vaga
                log.info("[gupy] '%s': %d vaga(s) de estágio", termo, n)
            except ScraperError as exc:
                self.registrar_erro(str(exc))
            except Exception as exc:  # noqa: BLE001
                self.registrar_erro(f"'{termo}': {exc.__class__.__name__}: {exc}")
