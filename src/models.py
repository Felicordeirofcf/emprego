"""Tipos de domínio compartilhados entre scrapers, matcher, banco e notificador."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator, model_validator


def normalizar_texto(texto: str) -> str:
    """minúsculas, sem acentos e com espaços colapsados — para comparações."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def normalizar_url(url: str) -> str:
    """Remove query string de tracking, fragmento e barra final para deduplicar."""
    partes = urlsplit(url.strip())
    caminho = partes.path.rstrip("/")
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), caminho, "", ""))


def hash_url(url: str) -> str:
    return hashlib.sha256(normalizar_url(url).encode("utf-8")).hexdigest()


def fingerprint_vaga(titulo: str, empresa: str | None) -> str:
    """Identifica a mesma vaga publicada em fontes diferentes (ex.: Gupy + GitHub)."""
    base = f"{normalizar_texto(empresa or '')}|{normalizar_texto(titulo)}"
    base = re.sub(r"[^a-z0-9| ]", "", base)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


@dataclass
class Vaga:
    """Vaga normalizada, independente da fonte de coleta."""

    fonte: str
    url: str
    titulo: str
    empresa: str | None
    descricao: str
    localizacao: str | None = None
    modalidade: str | None = None  # remoto | hibrido | presencial | None (desconhecido)
    publicada_em: datetime | None = None
    tags: list[str] = field(default_factory=list)
    id_externo: str | None = None

    @property
    def url_hash(self) -> str:
        return hash_url(self.url)

    @property
    def fingerprint(self) -> str:
        return fingerprint_vaga(self.titulo, self.empresa)


class MatchResult(BaseModel):
    """Saída estruturada da análise do Claude (espelha o input_schema da tool)."""

    elegivel_estagio: bool  # "elegível para o candidato": estágio OU júnior
    nivel_vaga: str = "outro"  # estagio | junior | outro
    motivo_inelegibilidade: str | None = None
    score: int = Field(ge=0, le=100)
    justificativa: str
    modalidade_detectada: str
    tecnologias_match: list[str]
    lacunas: list[str]
    projeto_portfolio_destacar: str
    pitch_candidatura: str

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v: object) -> int:
        try:
            return max(0, min(100, int(round(float(v)))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    @model_validator(mode="after")
    def _regra_estagio(self) -> "MatchResult":
        # Regra de negócio crítica: vaga inelegível SEMPRE tem score 0,
        # mesmo que o modelo tenha devolvido outra coisa.
        if not self.elegivel_estagio:
            self.score = 0
            if not self.motivo_inelegibilidade:
                self.motivo_inelegibilidade = "Vaga fora do nível (estágio/júnior) ou da região aceita."
        else:
            self.motivo_inelegibilidade = None
        return self


@dataclass
class UsoTokens:
    """Acumulador de uso/custo de tokens de uma execução."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    custo_usd: float = 0.0
    economia_usd: float = 0.0
    chamadas: int = 0

    def somar(self, outro: "UsoTokens") -> None:
        self.input_tokens += outro.input_tokens
        self.output_tokens += outro.output_tokens
        self.cache_write_tokens += outro.cache_write_tokens
        self.cache_read_tokens += outro.cache_read_tokens
        self.custo_usd += outro.custo_usd
        self.economia_usd += outro.economia_usd
        self.chamadas += outro.chamadas
