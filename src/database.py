"""Persistência em SQLite via SQLAlchemy 2.0.

Tabelas:
- vagas: toda vaga coletada (analisada ou não), com o resultado do Claude.
- execucoes: uma linha por rodada do robô (métricas para logs e dashboard).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .models import MatchResult, UsoTokens, Vaga

# Status possíveis de uma vaga no pipeline
STATUS_PENDENTE = "pendente"  # coletada, aguardando análise do Claude
STATUS_ANALISADA = "analisada"  # Claude respondeu
STATUS_DESCARTADA = "descartada_prefiltro"  # descartada por regra local (sem gastar tokens)
STATUS_ERRO = "erro"  # falhou após MAX_TENTATIVAS
MAX_TENTATIVAS = 3


def agora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class VagaDB(Base):
    __tablename__ = "vagas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(String(40), index=True)
    id_externo: Mapped[str | None] = mapped_column(String(120))
    titulo: Mapped[str] = mapped_column(Text)
    empresa: Mapped[str | None] = mapped_column(Text)
    localizacao: Mapped[str | None] = mapped_column(Text)
    modalidade: Mapped[str | None] = mapped_column(String(20))
    descricao: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    publicada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coletada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    status: Mapped[str] = mapped_column(String(30), default=STATUS_PENDENTE, index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_erro: Mapped[str | None] = mapped_column(Text)

    analisada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modelo: Mapped[str | None] = mapped_column(String(60))
    elegivel_estagio: Mapped[bool | None] = mapped_column(Boolean)
    motivo_inelegibilidade: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer, index=True)
    justificativa: Mapped[str | None] = mapped_column(Text)
    modalidade_detectada: Mapped[str | None] = mapped_column(String(20))
    tecnologias_match_json: Mapped[str] = mapped_column(Text, default="[]")
    lacunas_json: Mapped[str] = mapped_column(Text, default="[]")
    projeto_portfolio_destacar: Mapped[str | None] = mapped_column(Text)
    pitch_candidatura: Mapped[str | None] = mapped_column(Text)
    carta_apresentacao: Mapped[str | None] = mapped_column(Text)  # gerada pelo modelo de pitch
    modelo_pitch: Mapped[str | None] = mapped_column(String(60))

    notificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- helpers de leitura ---------------------------------------------
    @property
    def tecnologias_match(self) -> list[str]:
        return json.loads(self.tecnologias_match_json or "[]")

    @property
    def lacunas(self) -> list[str]:
        return json.loads(self.lacunas_json or "[]")

    @property
    def tags(self) -> list[str]:
        return json.loads(self.tags_json or "[]")

    @property
    def pitch_final(self) -> str | None:
        return self.carta_apresentacao or self.pitch_candidatura

    def to_vaga(self) -> Vaga:
        return Vaga(
            fonte=self.fonte,
            url=self.url,
            titulo=self.titulo,
            empresa=self.empresa,
            descricao=self.descricao,
            localizacao=self.localizacao,
            modalidade=self.modalidade,
            publicada_em=self.publicada_em,
            tags=self.tags,
            id_externo=self.id_externo,
        )


class ExecucaoDB(Base):
    __tablename__ = "execucoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    finalizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encontradas: Mapped[int] = mapped_column(Integer, default=0)
    novas: Mapped[int] = mapped_column(Integer, default=0)
    descartadas_prefiltro: Mapped[int] = mapped_column(Integer, default=0)
    analisadas: Mapped[int] = mapped_column(Integer, default=0)
    elegiveis: Mapped[int] = mapped_column(Integer, default=0)
    aprovadas: Mapped[int] = mapped_column(Integer, default=0)
    notificadas: Mapped[int] = mapped_column(Integer, default=0)
    erros: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    custo_usd: Mapped[float] = mapped_column(Float, default=0.0)
    economia_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fontes_json: Mapped[str] = mapped_column(Text, default="{}")  # {"gupy": 12, "github": 30}
    observacoes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Engine / sessão
# ---------------------------------------------------------------------------


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_conn.cursor()
    # DELETE (e não WAL) para o arquivo .db ficar autocontido ao ser commitado no git
    cur.execute("PRAGMA journal_mode=DELETE")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def criar_engine(caminho: Path | str) -> Engine:
    caminho = Path(caminho)
    if str(caminho) != ":memory:":
        caminho.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite://" if str(caminho) == ":memory:" else f"sqlite:///{caminho}"
    engine = create_engine(url, future=True)
    event.listen(engine, "connect", _sqlite_pragmas)
    Base.metadata.create_all(engine)
    return engine


def criar_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Repositório
# ---------------------------------------------------------------------------


class Repositorio:
    """Operações de alto nível usadas pelo orquestrador."""

    def __init__(self, session: Session):
        self.s = session

    # ---- duplicidade ------------------------------------------------------
    def ja_existe(self, vaga: Vaga) -> bool:
        """True se a URL já foi vista OU se a mesma vaga (empresa+título) já existe."""
        stmt = select(VagaDB.id).where(
            (VagaDB.url_hash == vaga.url_hash) | (VagaDB.fingerprint == vaga.fingerprint)
        )
        return self.s.execute(stmt.limit(1)).first() is not None

    def hashes_existentes(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        rows = self.s.execute(select(VagaDB.url_hash).where(VagaDB.url_hash.in_(hashes))).scalars()
        return set(rows)

    # ---- inserção ---------------------------------------------------------
    def inserir_vaga(self, vaga: Vaga, status: str = STATUS_PENDENTE, motivo: str | None = None) -> VagaDB:
        row = VagaDB(
            url_hash=vaga.url_hash,
            fingerprint=vaga.fingerprint,
            url=vaga.url,
            fonte=vaga.fonte,
            id_externo=vaga.id_externo,
            titulo=vaga.titulo[:500],
            empresa=vaga.empresa,
            localizacao=vaga.localizacao,
            modalidade=vaga.modalidade,
            descricao=vaga.descricao,
            tags_json=json.dumps(vaga.tags, ensure_ascii=False),
            publicada_em=vaga.publicada_em,
            status=status,
        )
        if status == STATUS_DESCARTADA:
            row.elegivel_estagio = False
            row.score = 0
            row.motivo_inelegibilidade = motivo
            row.analisada_em = agora()
            row.modelo = "prefiltro-local"
        self.s.add(row)
        self.s.flush()
        return row

    # ---- análise ----------------------------------------------------------
    def pendentes_de_analise(self, limite: int) -> list[VagaDB]:
        stmt = (
            select(VagaDB)
            .where(
                (VagaDB.status == STATUS_PENDENTE)
                | ((VagaDB.status == STATUS_ERRO) & (VagaDB.tentativas < MAX_TENTATIVAS))
            )
            .order_by(VagaDB.publicada_em.desc().nulls_last(), VagaDB.id.desc())
            .limit(limite)
        )
        return list(self.s.execute(stmt).scalars())

    def salvar_analise(self, row: VagaDB, resultado: MatchResult, modelo: str) -> None:
        row.status = STATUS_ANALISADA
        row.analisada_em = agora()
        row.modelo = modelo
        row.tentativas += 1
        row.ultimo_erro = None
        row.elegivel_estagio = resultado.elegivel_estagio
        row.motivo_inelegibilidade = resultado.motivo_inelegibilidade
        row.score = resultado.score
        row.justificativa = resultado.justificativa
        row.modalidade_detectada = resultado.modalidade_detectada
        row.tecnologias_match_json = json.dumps(resultado.tecnologias_match, ensure_ascii=False)
        row.lacunas_json = json.dumps(resultado.lacunas, ensure_ascii=False)
        row.projeto_portfolio_destacar = resultado.projeto_portfolio_destacar
        row.pitch_candidatura = resultado.pitch_candidatura

    def salvar_carta(self, row: VagaDB, carta: str, modelo: str) -> None:
        row.carta_apresentacao = carta
        row.modelo_pitch = modelo

    def registrar_erro(self, row: VagaDB, erro: str) -> None:
        row.tentativas += 1
        row.status = STATUS_ERRO
        row.ultimo_erro = erro[:2000]

    # ---- notificação ------------------------------------------------------
    def aprovadas_nao_notificadas(self, min_score: int) -> list[VagaDB]:
        stmt = (
            select(VagaDB)
            .where(
                VagaDB.status == STATUS_ANALISADA,
                VagaDB.elegivel_estagio.is_(True),
                VagaDB.score >= min_score,
                VagaDB.notificado_em.is_(None),
            )
            .order_by(VagaDB.score.desc())
        )
        return list(self.s.execute(stmt).scalars())

    def marcar_notificada(self, row: VagaDB) -> None:
        row.notificado_em = agora()

    # ---- execuções --------------------------------------------------------
    def iniciar_execucao(self) -> ExecucaoDB:
        exe = ExecucaoDB(iniciada_em=agora())
        self.s.add(exe)
        self.s.flush()
        return exe

    def finalizar_execucao(self, exe: ExecucaoDB, uso: UsoTokens) -> None:
        exe.finalizada_em = agora()
        exe.input_tokens = uso.input_tokens
        exe.output_tokens = uso.output_tokens
        exe.cache_write_tokens = uso.cache_write_tokens
        exe.cache_read_tokens = uso.cache_read_tokens
        exe.custo_usd = round(uso.custo_usd, 6)
        exe.economia_usd = round(uso.economia_usd, 6)

    # ---- leitura para dashboard ------------------------------------------
    def todas_vagas(self) -> Iterator[VagaDB]:
        yield from self.s.execute(select(VagaDB).order_by(VagaDB.coletada_em.desc())).scalars()

    def todas_execucoes(self) -> list[ExecucaoDB]:
        return list(self.s.execute(select(ExecucaoDB).order_by(ExecucaoDB.iniciada_em)).scalars())
