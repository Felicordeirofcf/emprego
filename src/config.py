"""Configuração central, lida de variáveis de ambiente (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Variável {name} deve ser um inteiro, recebido: {raw!r}") from exc


def _list(name: str, default: str, sep: str = ",") -> list[str]:
    raw = os.getenv(name) or default
    return [item.strip() for item in raw.split(sep) if item.strip()]


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default)
    return p if p.is_absolute() else ROOT_DIR / p


# Preço por 1M de tokens (USD) — tabela oficial da Anthropic: (entrada, saída, leitura de cache).
# Escrita de cache (TTL 5 min) = 1,25x o preço de entrada em todos os modelos.
# Haiku 5.5: preço para prompts de até 100K tokens (sempre o caso aqui).
PRECOS_MODELOS: dict[str, tuple[float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
}
MULT_CACHE_WRITE = 1.25


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    model_triagem: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL_TRIAGEM", "claude-haiku-5-5"))
    model_pitch: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL_PITCH", "claude-sonnet-5-5"))
    # Esforço (output_config.effort): low | medium | high | xhigh | max
    effort_triagem: str = field(default_factory=lambda: os.getenv("CLAUDE_EFFORT_TRIAGEM", "medium"))
    effort_pitch: str = field(default_factory=lambda: os.getenv("CLAUDE_EFFORT_PITCH", "medium"))
    enable_sonnet_pitch: bool = field(default_factory=lambda: _bool("ENABLE_SONNET_PITCH", True))
    sonnet_pitch_min_score: int = field(default_factory=lambda: _int("SONNET_PITCH_MIN_SCORE", 85))

    telegram_bot_token: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN") or None)
    telegram_chat_id: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID") or None)
    telegram_enviar_resumo: bool = field(default_factory=lambda: _bool("TELEGRAM_ENVIAR_RESUMO", True))

    min_score: int = field(default_factory=lambda: _int("MIN_SCORE", 75))
    max_vagas_por_execucao: int = field(default_factory=lambda: _int("MAX_VAGAS_POR_EXECUCAO", 60))
    max_concorrencia: int = field(default_factory=lambda: max(1, _int("MAX_CONCORRENCIA", 3)))
    max_descricao_chars: int = field(default_factory=lambda: _int("MAX_DESCRICAO_CHARS", 15000))
    dias_maximos_vaga: int = field(default_factory=lambda: _int("DIAS_MAXIMOS_VAGA", 30))
    uf_presencial: str = field(default_factory=lambda: os.getenv("UF_PRESENCIAL", "RJ"))
    estado_presencial: str = field(default_factory=lambda: os.getenv("ESTADO_PRESENCIAL", "Rio de Janeiro"))

    fontes_ativas: list[str] = field(default_factory=lambda: [f.lower() for f in _list("FONTES_ATIVAS", "github,gupy")])
    github_repos: list[str] = field(
        default_factory=lambda: _list("GITHUB_REPOS", "backend-br/vagas,frontendbr/vagas,react-brasil/vagas")
    )
    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN") or None)
    gupy_termos: list[str] = field(
        default_factory=lambda: _list(
            "GUPY_TERMOS",
            # O filtro de estágio é aplicado no servidor (type=vacancy_type_internship)
            # e de novo localmente, então os termos podem ser só a área/tecnologia.
            "desenvolvimento;desenvolvedor;software;programação;sistemas;dados;python;"
            "javascript;react;node;full stack;front-end;back-end;tecnologia",
            sep=";",
        )
    )

    # Buscas extras para vagas JÚNIOR (sem o filtro de estágio no servidor; o nível é conferido
    # localmente pelo título).
    gupy_termos_junior: list[str] = field(
        default_factory=lambda: _list(
            "GUPY_TERMOS_JUNIOR",
            "desenvolvedor júnior;desenvolvedora júnior;desenvolvedor junior;programador júnior;"
            "dev júnior;full stack júnior;front-end júnior;back-end júnior;python júnior;"
            "react júnior;node júnior;analista de sistemas júnior",
            sep=";",
        )
    )
    incluir_junior: bool = field(default_factory=lambda: _bool("INCLUIR_JUNIOR", True))

    database_path: Path = field(default_factory=lambda: _path("DATABASE_PATH", "data/vagas.db"))
    perfil_path: Path = field(default_factory=lambda: _path("PERFIL_PATH", "data/perfil.json"))
    dashboard_path: Path = field(default_factory=lambda: _path("DASHBOARD_PATH", "docs/index.html"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    @property
    def telegram_configurado(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def get_settings() -> Settings:
    return Settings()
