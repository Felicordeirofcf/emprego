"""Orquestrador: coleta -> deduplicação -> pré-filtro -> Claude -> Telegram -> dashboard.

Uso:
    python -m src.main                  # pipeline completo
    python -m src.main --dry-run        # analisa mas não envia nada ao Telegram
    python -m src.main --apenas-coleta  # só coleta e salva (sem gastar tokens)
    python -m src.main --fontes gupy --max-vagas 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import anthropic

from . import prefiltro
from .claude_matcher import (
    AnaliseError,
    ClaudeMatcher,
    CreditoInsuficienteError,
    carregar_perfil,
    perfil_eh_exemplo,
)
from .config import Settings, get_settings
from .dashboard import gerar_dashboard
from .database import (
    STATUS_DESCARTADA,
    STATUS_PENDENTE,
    Repositorio,
    VagaDB,
    criar_engine,
    criar_session_factory,
)
from .models import MatchResult, UsoTokens, Vaga
from .notifier import ResumoExecucao, TelegramNotifier
from .scrapers import criar_scrapers

log = logging.getLogger("robo")

SEP = "─" * 64


def anotar_erro(titulo: str, mensagem: str) -> None:
    """Erro no log e, no GitHub Actions, também como anotação no topo da execução."""
    log.error("%s: %s", titulo, mensagem)
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::error title={titulo}::{mensagem}", flush=True)


def configurar_logs(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for ruidoso in ("httpx", "httpcore", "httpx2", "httpcore2", "anthropic"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Robô de monitoramento e match de vagas de estágio")
    p.add_argument("--dry-run", action="store_true", help="Não envia mensagens ao Telegram")
    p.add_argument("--apenas-coleta", action="store_true", help="Coleta e salva, sem chamar o Claude")
    p.add_argument("--fontes", help="Fontes separadas por vírgula (padrão: FONTES_ATIVAS)")
    p.add_argument("--max-vagas", type=int, help="Teto de vagas analisadas nesta execução")
    p.add_argument("--sem-dashboard", action="store_true", help="Não regenera docs/index.html")
    return p.parse_args(argv)


@dataclass
class Contadores:
    encontradas: int = 0
    novas: int = 0
    descartadas_prefiltro: int = 0
    analisadas: int = 0
    elegiveis: int = 0
    aprovadas: int = 0
    notificadas: int = 0
    erros: int = 0


@dataclass
class SaidaWorker:
    vaga_id: int
    resultado: MatchResult | None
    modelo: str | None
    carta: str | None
    modelo_pitch: str | None
    uso: UsoTokens
    erro: str | None


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------


def etapa_coleta(settings: Settings, fontes: list[str] | None) -> tuple[list[Vaga], dict[str, int], list[str]]:
    log.info(SEP)
    log.info("1/4 · COLETA")
    todas: list[Vaga] = []
    por_fonte: dict[str, int] = {}
    erros: list[str] = []
    for scraper in criar_scrapers(settings, fontes):
        inicio = time.monotonic()
        res = scraper.executar()
        por_fonte[res.fonte] = len(res.vagas)
        erros += [f"{res.fonte}: {e}" for e in res.erros]
        todas += res.vagas
        log.info("  %-8s %3d vaga(s) de estágio em %.1fs%s", res.fonte, len(res.vagas),
                 time.monotonic() - inicio, f" ({len(res.erros)} erro(s))" if res.erros else "")
    return todas, por_fonte, erros


def etapa_persistencia(repo: Repositorio, vagas: list[Vaga], settings: Settings, c: Contadores) -> None:
    log.info(SEP)
    log.info("2/4 · DEDUPLICAÇÃO E PRÉ-FILTRO")
    for vaga in vagas:
        if repo.ja_existe(vaga):
            continue
        c.novas += 1
        seguir, motivo = prefiltro.avaliar(vaga, settings)
        if seguir:
            repo.inserir_vaga(vaga, STATUS_PENDENTE)
        else:
            c.descartadas_prefiltro += 1
            repo.inserir_vaga(vaga, STATUS_DESCARTADA, motivo)
            log.debug("  descartada sem IA: %s — %s", vaga.titulo, motivo)
    log.info("  %d nova(s) · %d já conhecida(s) · %d descartada(s) pelo pré-filtro (0 tokens)",
             c.novas, len(vagas) - c.novas, c.descartadas_prefiltro)


def _processar(matcher: ClaudeMatcher, settings: Settings, vaga_id: int, vaga: Vaga) -> SaidaWorker:
    uso = UsoTokens()
    try:
        analise = matcher.analisar(vaga)
        uso.somar(analise.uso)
        carta = modelo_pitch = None
        r = analise.resultado
        if settings.enable_sonnet_pitch and r.elegivel_estagio and r.score >= settings.sonnet_pitch_min_score:
            try:
                carta, uso_pitch, modelo_pitch = matcher.gerar_pitch(vaga, r)
                uso.somar(uso_pitch)
            except AnaliseError as exc:
                log.warning("  pitch não gerado para '%s': %s (mantendo o pitch da triagem)", vaga.titulo, exc)
        return SaidaWorker(vaga_id, r, analise.modelo, carta, modelo_pitch, uso, None)
    except AnaliseError as exc:
        return SaidaWorker(vaga_id, None, None, None, None, uso, str(exc))


def etapa_analise(repo: Repositorio, session, matcher: ClaudeMatcher, settings: Settings,
                  limite: int, c: Contadores, uso_total: UsoTokens) -> None:
    log.info(SEP)
    log.info("3/4 · ANÁLISE COM CLAUDE (%s)", settings.model_triagem)
    pendentes = repo.pendentes_de_analise(limite)
    if not pendentes:
        log.info("  nenhuma vaga pendente de análise")
        return
    log.info("  %d vaga(s) na fila (teto por execução: %d)", len(pendentes), limite)
    matcher.verificar_tamanho_cache()

    por_id: dict[int, VagaDB] = {row.id: row for row in pendentes}
    tarefas = [(row.id, row.to_vaga()) for row in pendentes]

    def aplicar(saida: SaidaWorker) -> None:
        row = por_id[saida.vaga_id]
        uso_total.somar(saida.uso)
        if saida.erro or saida.resultado is None:
            c.erros += 1
            repo.registrar_erro(row, saida.erro or "erro desconhecido")
            log.error("  ✗ %s — %s", _curto(row.titulo), saida.erro)
        else:
            r = saida.resultado
            repo.salvar_analise(row, r, saida.modelo or settings.model_triagem)
            if saida.carta:
                repo.salvar_carta(row, saida.carta, saida.modelo_pitch or settings.model_pitch)
            c.analisadas += 1
            c.elegiveis += int(r.elegivel_estagio)
            aprovada = r.elegivel_estagio and r.score >= settings.min_score
            c.aprovadas += int(aprovada)
            marca = "★" if aprovada else ("·" if r.elegivel_estagio else "✗")
            extra = f" (inelegível: {r.motivo_inelegibilidade})" if not r.elegivel_estagio else ""
            cache = f" cache_read={saida.uso.cache_read_tokens}" if saida.uso.cache_read_tokens else (
                f" cache_write={saida.uso.cache_write_tokens}" if saida.uso.cache_write_tokens else "")
            log.info("  %s %3d  %s%s%s", marca, r.score, _curto(row.titulo), extra, cache)
        session.commit()  # salva progresso a cada vaga (resiliente a interrupção)

    # 1ª chamada sozinha: grava o cache do prefixo (perfil). As demais o leem.
    primeiro_id, primeira_vaga = tarefas[0]
    aplicar(_processar(matcher, settings, primeiro_id, primeira_vaga))

    if len(tarefas) > 1:
        with ThreadPoolExecutor(max_workers=settings.max_concorrencia) as pool:
            futuros = [pool.submit(_processar, matcher, settings, vid, v) for vid, v in tarefas[1:]]
            for fut in as_completed(futuros):
                aplicar(fut.result())


def etapa_notificacao(repo: Repositorio, session, settings: Settings, dry_run: bool, c: Contadores) -> None:
    log.info(SEP)
    log.info("4/4 · ALERTAS (score ≥ %d)", settings.min_score)
    aprovadas = repo.aprovadas_nao_notificadas(settings.min_score)
    if not aprovadas:
        log.info("  nenhuma vaga aprovada nova")
        return
    if dry_run or not settings.telegram_configurado:
        destino = "--dry-run, nada enviado" if dry_run else "veja o bloco 🔔 Alertas no dashboard"
        log.info("  %d vaga(s) aprovada(s) (%s):", len(aprovadas), destino)
        for v in aprovadas:
            log.info("    ★ %3d  %s — %s", v.score, _curto(v.titulo), v.url)
        return
    with TelegramNotifier(settings) as tg:
        for v in aprovadas:
            if tg.notificar_vaga(v):
                repo.marcar_notificada(v)
                session.commit()
                c.notificadas += 1
                log.info("  📨 enviada: %s", _curto(v.titulo))


def _curto(texto: str, n: int = 70) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= n else texto[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configurar_logs(settings.log_level)
    inicio = time.monotonic()

    log.info("🤖 Robô de Vagas de Estágio — iniciando")
    try:
        if not args.apenas_coleta and perfil_eh_exemplo(settings.perfil_path):
            log.warning("data/perfil.json ainda é o MODELO com dados fictícios (\"_exemplo\": true). "
                        "Rodando só a coleta. Preencha seus dados e apague essa linha para ativar o Claude.")
            args.apenas_coleta = True
    except (FileNotFoundError, json.JSONDecodeError):
        pass  # tratado logo abaixo
    codigo_saida = 0
    if not args.apenas_coleta and not settings.anthropic_api_key:
        # Coleta e atualiza o painel mesmo assim, mas a execução termina com erro para chamar atenção.
        anotar_erro(
            "ANTHROPIC_API_KEY não encontrada",
            "Crie um REPOSITORY secret (Settings > Secrets and variables > Actions > New repository secret) "
            "chamado ANTHROPIC_API_KEY. Secrets de environment não chegam a este workflow. "
            "Nesta execução o robô só coletou vagas, sem análise.",
        )
        args.apenas_coleta = True
        codigo_saida = 2
    try:
        perfil = carregar_perfil(settings.perfil_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.error("Erro ao carregar o perfil: %s", exc)
        return 2

    engine = criar_engine(settings.database_path)
    Session = criar_session_factory(engine)
    c = Contadores()
    uso = UsoTokens()
    observacoes: list[str] = ["ANTHROPIC_API_KEY ausente: só coleta"] if codigo_saida == 2 else []

    with Session() as session:
        repo = Repositorio(session)
        exe = repo.iniciar_execucao()
        session.commit()
        try:
            fontes = [f.strip() for f in args.fontes.split(",")] if args.fontes else None
            vagas, por_fonte, erros_coleta = etapa_coleta(settings, fontes)
            c.encontradas = len(vagas)
            observacoes += erros_coleta
            exe.fontes_json = json.dumps(por_fonte, ensure_ascii=False)

            etapa_persistencia(repo, vagas, settings, c)
            session.commit()

            if args.apenas_coleta:
                log.info("--apenas-coleta: análise e notificação puladas")
            else:
                matcher = ClaudeMatcher(settings, perfil=perfil)
                limite = args.max_vagas or settings.max_vagas_por_execucao
                try:
                    etapa_analise(repo, session, matcher, settings, limite, c, uso)
                except CreditoInsuficienteError:
                    anotar_erro("Sem créditos na Anthropic",
                                "A conta da API está sem saldo. Adicione créditos em console.anthropic.com "
                                "(Settings > Billing). As vagas continuam na fila para a próxima execução.")
                    observacoes.append("Conta da Anthropic sem créditos")
                    codigo_saida = 2
                except anthropic.PermissionDeniedError as exc:
                    anotar_erro("Sem permissão na API da Anthropic (403)", str(exc)[:300])
                    observacoes.append("Permissão negada na Anthropic")
                    codigo_saida = 2
                except anthropic.AuthenticationError:
                    anotar_erro("ANTHROPIC_API_KEY inválida (401)",
                                "A Anthropic recusou a chave. Gere uma nova em console.anthropic.com e "
                                "atualize o repository secret ANTHROPIC_API_KEY.")
                    observacoes.append("Falha de autenticação na Anthropic")
                    codigo_saida = 2
                etapa_notificacao(repo, session, settings, args.dry_run, c)
        except KeyboardInterrupt:
            log.warning("Interrompido pelo usuário — salvando o progresso…")
            observacoes.append("Execução interrompida")
            codigo_saida = 130
        finally:
            for campo in ("encontradas", "novas", "descartadas_prefiltro", "analisadas",
                          "elegiveis", "aprovadas", "notificadas", "erros"):
                setattr(exe, campo, getattr(c, campo))
            exe.observacoes = "\n".join(observacoes)[:4000] or None
            repo.finalizar_execucao(exe, uso)
            session.commit()

        duracao = time.monotonic() - inicio
        if (not args.dry_run and not args.apenas_coleta and settings.telegram_configurado
                and settings.telegram_enviar_resumo and c.novas > 0):
            with TelegramNotifier(settings) as tg:
                tg.enviar_resumo(ResumoExecucao(c.encontradas, c.novas, c.analisadas, c.aprovadas,
                                                c.notificadas, c.erros, uso.custo_usd,
                                                uso.economia_usd, duracao), settings.min_score)

        if not args.sem_dashboard:
            try:
                caminho = gerar_dashboard(session, settings)
                log.info("📊 Dashboard atualizado: %s", caminho)
            except Exception as exc:  # noqa: BLE001 - dashboard nunca derruba o robô
                log.warning("Falha ao gerar o dashboard: %s", exc)

    _imprimir_resumo(c, uso, time.monotonic() - inicio, observacoes)
    engine.dispose()
    return codigo_saida


def _imprimir_resumo(c: Contadores, uso: UsoTokens, duracao: float, observacoes: list[str]) -> None:
    log.info(SEP)
    log.info("RESUMO DA EXECUÇÃO")
    log.info("  Vagas encontradas ............ %d", c.encontradas)
    log.info("  Novas ........................ %d", c.novas)
    log.info("  Descartadas no pré-filtro .... %d", c.descartadas_prefiltro)
    log.info("  Analisadas pelo Claude ....... %d", c.analisadas)
    log.info("  Elegíveis (estágio) .......... %d", c.elegiveis)
    log.info("  Aprovadas (score mínimo) ..... %d", c.aprovadas)
    if c.notificadas:
        log.info("  Notificadas no Telegram ...... %d", c.notificadas)
    log.info("  Erros de análise ............. %d", c.erros)
    log.info("  Chamadas à API ............... %d", uso.chamadas)
    log.info("  Tokens: entrada %d · saída %d · cache escrito %d · cache lido %d",
             uso.input_tokens, uso.output_tokens, uso.cache_write_tokens, uso.cache_read_tokens)
    log.info("  Custo estimado ............... US$ %.4f", uso.custo_usd)
    log.info("  Economia com prompt caching .. US$ %.4f", uso.economia_usd)
    log.info("  Duração ...................... %.1fs", duracao)
    for obs in observacoes:
        log.warning("  ⚠ %s", obs)
    log.info(SEP)


if __name__ == "__main__":
    sys.exit(main())
