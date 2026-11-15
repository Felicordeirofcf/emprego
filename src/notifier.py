"""Notificações no Telegram (Bot API) com MarkdownV2 e escape seguro de caracteres."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .database import VagaDB

log = logging.getLogger(__name__)

API_TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_TELEGRAM = 4096
MARGEM = 96  # folga para não estourar o limite após o escape

_ESPECIAIS_MDV2 = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

ROTULO_MODALIDADE = {
    "remoto": "Remoto",
    "hibrido": "Híbrido",
    "presencial": "Presencial",
    "nao_informado": "Não informado",
}


def escapar_md(texto: str | None) -> str:
    """Escapa TODOS os caracteres reservados do MarkdownV2 do Telegram."""
    return _ESPECIAIS_MDV2.sub(r"\\\1", texto or "")


def escapar_url(url: str) -> str:
    """Dentro de (...) de um link só ')' e '\\' precisam de escape."""
    return url.replace("\\", "\\\\").replace(")", "\\)")


def barra_score(score: int) -> str:
    cheios = round(score / 10)
    return "▰" * cheios + "▱" * (10 - cheios)


def citar(texto: str) -> str:
    """Blockquote do MarkdownV2: cada linha começa com '>'."""
    linhas = [ln for ln in texto.strip().splitlines()] or [""]
    return "\n".join(f">{escapar_md(ln)}" for ln in linhas)


def _cortar(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def formatar_card(vaga: VagaDB, limite_pitch: int = 1800) -> str:
    score = vaga.score or 0
    modalidade = ROTULO_MODALIDADE.get(vaga.modalidade_detectada or "", vaga.modalidade or "Não informado")
    local = f" · {vaga.localizacao}" if vaga.localizacao else ""
    fortes = ", ".join(vaga.tecnologias_match) or "—"
    gaps = ", ".join(vaga.lacunas) or "Nenhum gap relevante"
    pitch = vaga.pitch_final or ""
    origem_pitch = "Claude Sonnet" if vaga.carta_apresentacao else "Claude"

    partes = [
        f"🎯 *{escapar_md(_cortar(vaga.titulo, 200))}*",
        f"🏢 {escapar_md(vaga.empresa or 'Empresa não informada')}",
        f"📍 {escapar_md(modalidade + local)}",
        f"📊 *Score de match:* {score}/100 {barra_score(score)}",
        "",
        f"✅ *Pontos fortes:* {escapar_md(_cortar(fortes, 400))}",
        f"⚠️ *Gaps:* {escapar_md(_cortar(gaps, 400))}",
    ]
    if vaga.projeto_portfolio_destacar:
        partes.append(f"🧩 *Projeto para destacar:* {escapar_md(_cortar(vaga.projeto_portfolio_destacar, 300))}")
    if vaga.justificativa:
        partes.append(f"💡 _{escapar_md(_cortar(vaga.justificativa, 600))}_")
    if pitch:
        partes += ["", f"✉️ *Pitch sugerido \\({escapar_md(origem_pitch)}\\):*", citar(_cortar(pitch, limite_pitch))]
    partes += [
        "",
        f"🔗 [Abrir vaga]({escapar_url(vaga.url)}) · _fonte: {escapar_md(vaga.fonte)}_",
    ]
    texto = "\n".join(partes)

    if len(texto) > LIMITE_TELEGRAM - MARGEM and limite_pitch > 200:
        return formatar_card(vaga, limite_pitch=limite_pitch // 2)
    return texto


def texto_simples(vaga: VagaDB) -> str:
    """Fallback sem formatação, caso o Telegram rejeite o MarkdownV2."""
    return _cortar(
        f"{vaga.titulo}\n{vaga.empresa or ''}\nScore: {vaga.score}/100\n"
        f"Pontos fortes: {', '.join(vaga.tecnologias_match)}\nGaps: {', '.join(vaga.lacunas)}\n\n"
        f"{vaga.pitch_final or ''}\n\n{vaga.url}",
        LIMITE_TELEGRAM - MARGEM,
    )


@dataclass
class ResumoExecucao:
    encontradas: int
    novas: int
    analisadas: int
    aprovadas: int
    notificadas: int
    erros: int
    custo_usd: float
    economia_usd: float
    duracao_s: float


def formatar_resumo(r: ResumoExecucao, min_score: int) -> str:
    linhas = [
        "🤖 *Robô de Vagas — resumo da execução*",
        f"🔎 Encontradas: {r.encontradas} · 🆕 Novas: {r.novas}",
        f"🧠 Analisadas pelo Claude: {r.analisadas}",
        f"⭐ Aprovadas \\(score ≥ {min_score}\\): {r.aprovadas} · 📨 Notificadas: {r.notificadas}",
        f"💰 Custo estimado: US$ {escapar_md(f'{r.custo_usd:.4f}')} · "
        f"💾 economia com cache: US$ {escapar_md(f'{r.economia_usd:.4f}')}",
        f"⏱️ {escapar_md(f'{r.duracao_s:.0f}')}s" + (f" · ❗ erros: {r.erros}" if r.erros else ""),
    ]
    return "\n".join(linhas)


class TelegramNotifier:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not settings.telegram_configurado:
            raise ValueError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID precisam estar configurados.")
        self.chat_id = settings.telegram_chat_id
        self.url = API_TELEGRAM.format(token=settings.telegram_bot_token)
        self._client = httpx.Client(timeout=20.0, transport=transport)
        self._sleep = sleep
        self._ultimo_envio = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TelegramNotifier":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, payload: dict) -> httpx.Response:
        # Telegram: ~1 mensagem/segundo por chat.
        decorrido = time.monotonic() - self._ultimo_envio
        if decorrido < 1.1:
            self._sleep(1.1 - decorrido)
        self._ultimo_envio = time.monotonic()
        return self._client.post(self.url, json=payload)

    def enviar(self, texto: str, *, markdown: bool = True, fallback: str | None = None) -> bool:
        payload: dict = {"chat_id": self.chat_id, "text": texto, "link_preview_options": {"is_disabled": True}}
        if markdown:
            payload["parse_mode"] = "MarkdownV2"

        for tentativa in range(1, 5):
            try:
                resp = self._post(payload)
            except httpx.TransportError as exc:
                log.warning("Falha de rede no Telegram (%s), tentativa %d/4", exc.__class__.__name__, tentativa)
                self._sleep(2**tentativa)
                continue

            if resp.status_code == 200:
                return True

            try:
                corpo = resp.json()
            except ValueError:
                corpo = {}
            descricao = str(corpo.get("description", resp.text))[:300]

            if resp.status_code == 429:
                espera = int((corpo.get("parameters") or {}).get("retry_after", 5))
                log.warning("Telegram rate limit; aguardando %ds", espera)
                self._sleep(espera + 1)
                continue
            if resp.status_code == 400 and "parse" in descricao.lower() and markdown:
                log.warning("Telegram rejeitou o MarkdownV2 (%s). Reenviando como texto simples.", descricao)
                return self.enviar(fallback or re.sub(r"\\(.)", r"\1", texto), markdown=False)
            if resp.status_code >= 500:
                self._sleep(2**tentativa)
                continue

            log.error("Erro do Telegram HTTP %d: %s", resp.status_code, descricao)
            return False

        log.error("Desisti de enviar a mensagem ao Telegram após 4 tentativas.")
        return False

    def notificar_vaga(self, vaga: VagaDB) -> bool:
        return self.enviar(formatar_card(vaga), fallback=texto_simples(vaga))

    def enviar_resumo(self, resumo: ResumoExecucao, min_score: int) -> bool:
        return self.enviar(formatar_resumo(resumo, min_score))
