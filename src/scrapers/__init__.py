"""Registro de coletores. Para adicionar uma fonte, crie a classe e inclua no REGISTRO."""

from __future__ import annotations

from ..config import Settings
from .base import HttpClient, JobScraper, ResultadoColeta, ScraperError
from .github_vagas import GitHubVagasScraper
from .gupy import GupyScraper

REGISTRO: dict[str, type[JobScraper]] = {
    GitHubVagasScraper.nome: GitHubVagasScraper,
    GupyScraper.nome: GupyScraper,
    # "programathor": ProgramathorScraper,
    # "ciee": CieeScraper,
    # "nube": NubeScraper,
}


def criar_scrapers(settings: Settings, fontes: list[str] | None = None) -> list[JobScraper]:
    nomes = fontes or settings.fontes_ativas
    desconhecidas = [n for n in nomes if n not in REGISTRO]
    if desconhecidas:
        raise ValueError(f"Fonte(s) desconhecida(s): {desconhecidas}. Disponíveis: {sorted(REGISTRO)}")
    return [REGISTRO[n](settings) for n in nomes]


__all__ = [
    "REGISTRO",
    "HttpClient",
    "JobScraper",
    "ResultadoColeta",
    "ScraperError",
    "criar_scrapers",
]
