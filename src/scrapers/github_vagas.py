"""Coletor das issues de vagas nos repositórios da comunidade (backend-br, frontendbr, react-brasil).

Os repositórios usam labels como "Estágio", "Remoto", "Híbrido", "Presencial", "RJ"...,
mas os nomes variam entre repositórios e ao longo do tempo. Por isso, em vez de depender
do nome exato da label, buscamos as issues abertas recentes e filtramos localmente por
labels/título que indiquem estágio (com e sem acento).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..models import Vaga, normalizar_texto
from .base import HttpClient, JobScraper, ScraperError, parse_data_iso

log = logging.getLogger(__name__)

API = "https://api.github.com"
POR_PAGINA = 100
MAX_PAGINAS = 5

RE_ESTAGIO = re.compile(r"\b(estagi\w*|intern|internship)\b")
RE_JUNIOR = re.compile(r"\b(junior|jr)\b")
RE_EMPRESA = re.compile(r"(?:\s@\s*|\s(?:na|no|at|em)\s+)([^\[\]\(\)|@]{2,60})$", re.IGNORECASE)
RE_PREFIXO_COLCHETES = re.compile(r"^\s*(\[[^\]]*\]\s*)+")


def detectar_modalidade(textos: list[str]) -> str | None:
    blob = " ".join(normalizar_texto(t) for t in textos)
    if re.search(r"\bhibrid|\bhybrid", blob):
        return "hibrido"
    if re.search(r"\bremot|\bremote|\bhome ?office|\banywhere", blob):
        return "remoto"
    if re.search(r"\bpresencial|\bon-?site|\balocado", blob):
        return "presencial"
    return None


CIDADES = r"sao paulo|belo horizonte|curitiba|porto alegre|recife|florianopolis|brasilia|campinas|salvador|fortaleza|goiania"
UFS = r"sp|mg|pr|rs|pe|sc|df|ba|ce|go|es|am|pa"


def detectar_localizacao(labels: list[str], titulo: str = "") -> str | None:
    """Localização a partir das labels (curadas) e do título.

    Siglas de UF só são aceitas nas labels: no título, "Go" ou "PE" costumam ser outra coisa.
    """
    blob_labels = " ".join(normalizar_texto(t) for t in labels)
    blob_titulo = normalizar_texto(titulo)
    blob_total = f"{blob_labels} {blob_titulo}"
    if re.search(r"\brio de janeiro\b|\brj\b|\bniteroi\b", blob_total):
        return "Rio de Janeiro - RJ"
    m = re.search(rf"\b({CIDADES})\b", blob_total)
    if m:
        return m.group(1).title()
    m = re.search(rf"\b({UFS})\b", blob_labels)
    if m:
        return m.group(1).upper()
    return None


def extrair_empresa(titulo: str) -> str | None:
    limpo = RE_PREFIXO_COLCHETES.sub("", titulo).strip()
    m = RE_EMPRESA.search(limpo)
    if m:
        return m.group(1).strip(" -–—:")
    return None


class GitHubVagasScraper(JobScraper):
    nome = "github"

    def criar_http(self) -> HttpClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return HttpClient(headers=headers, intervalo_minimo=0.3)

    def _checar_rate_limit(self, resp) -> None:
        restante = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if restante is not None and int(restante) == 0 and reset:
            espera = int(reset) - int(time.time())
            if 0 < espera <= 60:
                log.warning("[github] rate limit esgotado; aguardando %ds", espera)
                time.sleep(espera + 1)
            elif espera > 60:
                raise ScraperError(
                    f"Rate limit da API do GitHub esgotado por {espera // 60} min. "
                    "Defina GITHUB_TOKEN para ter 5.000 req/h."
                )

    def eh_nivel_aceito(self, issue: dict) -> bool:
        """Estágio sempre; Júnior quando INCLUIR_JUNIOR (padrão). Olha labels e título."""
        padroes = [RE_ESTAGIO] + ([RE_JUNIOR] if self.settings.incluir_junior else [])
        textos = [normalizar_texto(lbl.get("name", "")) for lbl in issue.get("labels", [])]
        textos.append(normalizar_texto(issue.get("title", "")))
        return any(rx.search(t) for rx in padroes for t in textos)

    def normalizar(self, repo: str, issue: dict) -> Vaga:
        labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
        titulo = issue.get("title", "").strip()
        textos_meta = labels + [titulo]
        return Vaga(
            fonte=f"github:{repo}",
            url=issue["html_url"],
            titulo=titulo,
            empresa=extrair_empresa(titulo),
            descricao=(issue.get("body") or "").strip(),
            localizacao=detectar_localizacao(labels, titulo),
            modalidade=detectar_modalidade(textos_meta),
            publicada_em=parse_data_iso(issue.get("created_at")),
            tags=labels,
            id_externo=str(issue.get("number")),
        )

    def coletar_repo(self, repo: str) -> Iterable[Vaga]:
        desde = datetime.now(timezone.utc) - timedelta(days=self.settings.dias_maximos_vaga)
        for pagina in range(1, MAX_PAGINAS + 1):
            resp = self.http.get(
                f"{API}/repos/{repo}/issues",
                params={
                    "state": "open",
                    "sort": "created",
                    "direction": "desc",
                    "since": desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "per_page": POR_PAGINA,
                    "page": pagina,
                },
            )
            self._checar_rate_limit(resp)
            if resp.status_code == 404:
                raise ScraperError(f"Repositório {repo} não encontrado (404)")
            if resp.status_code in (401, 403):
                raise ScraperError(f"Acesso negado à API do GitHub para {repo} (HTTP {resp.status_code})")
            resp.raise_for_status()

            issues = resp.json()
            if not isinstance(issues, list):
                raise ScraperError(f"Resposta inesperada da API do GitHub para {repo}")

            for issue in issues:
                if "pull_request" in issue:
                    continue
                criada = parse_data_iso(issue.get("created_at"))
                if criada and criada < desde:
                    continue
                if self.eh_nivel_aceito(issue):
                    yield self.normalizar(repo, issue)

            if len(issues) < POR_PAGINA:
                break

    def coletar(self) -> Iterable[Vaga]:
        for repo in self.settings.github_repos:
            try:
                encontradas = 0
                for vaga in self.coletar_repo(repo):
                    encontradas += 1
                    yield vaga
                log.info("[github] %s: %d vaga(s) de estágio/júnior", repo, encontradas)
            except ScraperError as exc:
                self.registrar_erro(f"{repo}: {exc}")
            except Exception as exc:  # noqa: BLE001
                self.registrar_erro(f"{repo}: {exc.__class__.__name__}: {exc}")
