"""Contrato base dos coletores e utilitários HTTP compartilhados.

Para adicionar uma nova fonte (Programathor, CIEE, Nube...):

    class ProgramathorScraper(JobScraper):
        nome = "programathor"

        def coletar(self) -> Iterable[Vaga]:
            resp = self.http.get("https://...")
            for item in resp.json():
                yield Vaga(fonte=self.nome, url=..., titulo=..., empresa=..., descricao=...)

e registre a classe em ``src/scrapers/__init__.py`` (dicionário REGISTRO).
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

import httpx

from ..config import Settings
from ..models import Vaga

log = logging.getLogger(__name__)

USER_AGENT = "robo-vagas-estagio/1.0 (+https://github.com/; monitor pessoal de vagas)"
STATUS_RETENTAVEIS = {408, 425, 429, 500, 502, 503, 504}


class ScraperError(RuntimeError):
    """Erro irrecuperável numa fonte (o orquestrador registra e segue para a próxima)."""


@dataclass
class ResultadoColeta:
    fonte: str
    vagas: list[Vaga] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


class HttpClient:
    """httpx.Client com retry exponencial, Retry-After e intervalo mínimo entre requisições."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        intervalo_minimo: float = 0.5,
        max_tentativas: int = 4,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        base_headers = {"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"}
        base_headers.update(headers or {})
        self._client = httpx.Client(
            headers=base_headers, timeout=timeout, follow_redirects=True, transport=transport
        )
        self.intervalo_minimo = intervalo_minimo
        self.max_tentativas = max_tentativas
        self._ultimo_request = 0.0
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _respeitar_intervalo(self) -> None:
        decorrido = time.monotonic() - self._ultimo_request
        if decorrido < self.intervalo_minimo:
            self._sleep(self.intervalo_minimo - decorrido)
        self._ultimo_request = time.monotonic()

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        valor = resp.headers.get("Retry-After")
        if not valor:
            return None
        try:
            return max(0.0, float(valor))
        except ValueError:
            try:
                alvo = parsedate_to_datetime(valor)
                return max(0.0, (alvo - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                return None

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.max_tentativas + 1):
            self._respeitar_intervalo()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:  # timeout, DNS, conexão recusada...
                ultimo_erro = exc
                espera = min(30.0, 2**tentativa + random.random())
                log.warning("Falha de rede em %s (%s). Tentativa %d/%d, aguardando %.1fs",
                            url, exc.__class__.__name__, tentativa, self.max_tentativas, espera)
                self._sleep(espera)
                continue

            if resp.status_code in STATUS_RETENTAVEIS:
                espera = self._retry_after(resp) or min(60.0, 2**tentativa + random.random())
                if espera > 120:
                    raise ScraperError(f"Rate limit em {url}: Retry-After de {espera:.0f}s é longo demais")
                log.warning("HTTP %d em %s. Tentativa %d/%d, aguardando %.1fs",
                            resp.status_code, url, tentativa, self.max_tentativas, espera)
                ultimo_erro = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                self._sleep(espera)
                continue

            return resp

        raise ScraperError(f"Desisti de {url} após {self.max_tentativas} tentativas: {ultimo_erro}")


class JobScraper(ABC):
    """Interface de todo coletor. Implemente ``coletar`` e defina ``nome``."""

    nome: str = "base"

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        self.http = http or self.criar_http()
        self._erros: list[str] = []

    def criar_http(self) -> HttpClient:
        return HttpClient()

    def registrar_erro(self, mensagem: str) -> None:
        log.error("[%s] %s", self.nome, mensagem)
        self._erros.append(mensagem)

    @abstractmethod
    def coletar(self) -> Iterable[Vaga]:
        """Retorna vagas já normalizadas. Deve tratar erros parciais com registrar_erro."""

    def executar(self) -> ResultadoColeta:
        resultado = ResultadoColeta(fonte=self.nome)
        vistos: set[str] = set()
        try:
            for vaga in self.coletar():
                if vaga.url_hash in vistos:
                    continue
                vistos.add(vaga.url_hash)
                resultado.vagas.append(vaga)
        except ScraperError as exc:
            self.registrar_erro(str(exc))
        except Exception as exc:  # noqa: BLE001 - uma fonte quebrada não derruba o robô
            log.exception("[%s] erro inesperado", self.nome)
            self.registrar_erro(f"{exc.__class__.__name__}: {exc}")
        finally:
            self.http.close()
        resultado.erros = list(self._erros)
        return resultado


def parse_data_iso(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
