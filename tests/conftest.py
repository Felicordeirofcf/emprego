from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config import Settings
from src.database import Repositorio, criar_engine, criar_session_factory
from src.models import Vaga

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "vagas.db"))
    monkeypatch.setenv("DASHBOARD_PATH", str(tmp_path / "index.html"))
    perfil_real = tmp_path / "perfil_teste.json"
    dados = json.loads((RAIZ / "data" / "perfil.json").read_text(encoding="utf-8"))
    dados.pop("_exemplo", None)
    perfil_real.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PERFIL_PATH", str(perfil_real))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("MIN_SCORE", "75")
    monkeypatch.setenv("SONNET_PITCH_MIN_SCORE", "85")
    return Settings()


@pytest.fixture
def repo(settings):
    engine = criar_engine(settings.database_path)
    with criar_session_factory(engine)() as s:
        yield Repositorio(s)


@pytest.fixture
def perfil() -> dict:
    return json.loads((RAIZ / "data" / "perfil.json").read_text(encoding="utf-8"))


def nova_vaga(**kw) -> Vaga:
    base = dict(
        fonte="gupy",
        url="https://empresa.gupy.io/jobs/123?utm_source=x",
        titulo="Estágio em Desenvolvimento Full Stack",
        empresa="Empresa X",
        descricao="React, Node.js, PostgreSQL. Remoto. Cursando TI.",
        localizacao="Rio de Janeiro - Rio de Janeiro",
        modalidade="remoto",
        publicada_em=datetime.now(timezone.utc) - timedelta(days=2),
    )
    base.update(kw)
    return Vaga(**base)
