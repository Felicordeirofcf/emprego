"""Gera o dashboard de acompanhamento (HTML estático e autocontido) a partir do SQLite.

O arquivo gerado (docs/index.html) não depende de servidor nem de CDN: pode ser aberto
direto no navegador ou publicado no GitHub Pages (Settings > Pages > branch main, pasta /docs).

Uso:
    python -m src.dashboard            # gera docs/index.html
    python -m src.dashboard --serve    # gera e serve em http://localhost:8000
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import socketserver
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .claude_matcher import perfil_eh_exemplo
from .config import Settings, get_settings
from .database import Repositorio, criar_engine, criar_session_factory

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def coletar_dados(session: Session, settings: Settings) -> dict[str, Any]:
    repo = Repositorio(session)
    vagas = []
    for v in repo.todas_vagas():
        vagas.append(
            {
                "id": v.url_hash[:16],
                "titulo": v.titulo,
                "empresa": v.empresa,
                "fonte": v.fonte.split(":")[0],
                "fonte_detalhe": v.fonte,
                "url": v.url,
                "localizacao": v.localizacao,
                "modalidade": v.modalidade_detectada or v.modalidade or "nao_informado",
                "publicada_em": _iso(v.publicada_em),
                "coletada_em": _iso(v.coletada_em),
                "status": v.status,
                "elegivel": v.elegivel_estagio,
                "motivo": v.motivo_inelegibilidade,
                "score": v.score,
                "justificativa": v.justificativa,
                "match": v.tecnologias_match,
                "lacunas": v.lacunas,
                "projeto": v.projeto_portfolio_destacar,
                "pitch": v.pitch_final,
                "pitch_modelo": v.modelo_pitch if v.carta_apresentacao else v.modelo,
                "notificado_em": _iso(v.notificado_em),
                "erro": v.ultimo_erro,
            }
        )
    execucoes = [
        {
            "id": e.id,
            "iniciada_em": _iso(e.iniciada_em),
            "finalizada_em": _iso(e.finalizada_em),
            "encontradas": e.encontradas,
            "novas": e.novas,
            "descartadas": e.descartadas_prefiltro,
            "analisadas": e.analisadas,
            "elegiveis": e.elegiveis,
            "aprovadas": e.aprovadas,
            "notificadas": e.notificadas,
            "erros": e.erros,
            "custo_usd": e.custo_usd,
            "economia_usd": e.economia_usd,
            "cache_read": e.cache_read_tokens,
            "cache_write": e.cache_write_tokens,
            "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens,
            "fontes": json.loads(e.fontes_json or "{}"),
        }
        for e in repo.todas_execucoes()
    ]
    aviso = None
    try:
        if perfil_eh_exemplo(settings.perfil_path):
            na_fila = sum(1 for v in vagas if v["status"] == "pendente")
            aviso = (
                "⚠️ O data/perfil.json ainda é o modelo com dados fictícios, então o Claude está desligado: "
                "o robô só coleta vagas e nenhuma recebe score ou alerta. "
                f"{na_fila} vaga(s) aguardando análise. Preencha o perfil e apague a linha \"_exemplo\": true para ativar."
            )
    except (OSError, ValueError):
        pass
    return {
        "aviso": aviso,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "min_score": settings.min_score,
        "modelo_triagem": settings.model_triagem,
        "modelo_pitch": settings.model_pitch,
        "vagas": vagas,
        "execucoes": execucoes,
    }


def renderizar(dados: dict[str, Any]) -> str:
    # "</" escapado para que nenhum texto vindo da internet feche a tag <script>.
    payload = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DADOS_JSON__", payload)


def gerar_dashboard(session: Session, settings: Settings) -> Path:
    destino = settings.dashboard_path
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(renderizar(coletar_dados(session, settings)), encoding="utf-8")
    return destino


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o dashboard de acompanhamento")
    parser.add_argument("--serve", action="store_true", help="Serve o dashboard localmente")
    parser.add_argument("--porta", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    engine = criar_engine(settings.database_path)
    with criar_session_factory(engine)() as session:
        caminho = gerar_dashboard(session, settings)
    log.info("Dashboard gerado em %s", caminho)

    if args.serve:
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(caminho.parent))
        with socketserver.TCPServer(("127.0.0.1", args.porta), handler) as httpd:
            log.info("Abrindo em http://localhost:%d/%s (Ctrl+C para sair)", args.porta, caminho.name)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
