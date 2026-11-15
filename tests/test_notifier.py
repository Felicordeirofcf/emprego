import json

import httpx

from src.database import VagaDB
from src.notifier import LIMITE_TELEGRAM, TelegramNotifier, escapar_md, formatar_card


def _vaga(**kw):
    base = dict(
        titulo="Estágio Dev (Full-Stack) #1 *urgente*", empresa="Acme_Tech S.A.", url="https://x.com/vaga_(1)",
        fonte="gupy", localizacao="Rio de Janeiro - RJ", modalidade="remoto", modalidade_detectada="hibrido",
        score=91, justificativa="Bom match.", tecnologias_match_json=json.dumps(["React", "Node.js"]),
        lacunas_json=json.dumps(["Java"]), projeto_portfolio_destacar="AgendaPro",
        pitch_candidatura="Olá! Tenho um SaaS em produção.", carta_apresentacao=None,
    )
    base.update(kw)
    return VagaDB(**base)


def test_escape_mdv2():
    assert escapar_md("a_b*c[d](e)~f`g>h#i+j-k=l|m{n}o.p!q\\") == (
        "a\\_b\\*c\\[d\\]\\(e\\)\\~f\\`g\\>h\\#i\\+j\\-k\\=l\\|m\\{n\\}o\\.p\\!q\\\\")


def test_card_tem_campos_e_respeita_limite():
    card = formatar_card(_vaga(carta_apresentacao="x" * 6000))
    assert "Score de match:* 91/100" in card and "Acme\\_Tech S\\.A\\." in card
    assert "Híbrido" in card and "(https://x.com/vaga_(1\\))" in card
    assert len(card) <= LIMITE_TELEGRAM


def _settings(settings):
    import dataclasses
    return dataclasses.replace(settings, telegram_bot_token="123:abc", telegram_chat_id="42")


def test_envio_ok(settings):
    corpo = {}

    def handler(req):
        corpo.update(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    tg = TelegramNotifier(_settings(settings), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert tg.notificar_vaga(_vaga())
    assert corpo["parse_mode"] == "MarkdownV2" and corpo["chat_id"] == "42"


def test_fallback_texto_simples_e_rate_limit(settings):
    respostas = iter([
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}),
        httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"}),
        httpx.Response(200, json={"ok": True}),
    ])
    enviados = []

    def handler(req):
        enviados.append(json.loads(req.content))
        return next(respostas)

    tg = TelegramNotifier(_settings(settings), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert tg.notificar_vaga(_vaga())
    assert "parse_mode" not in enviados[-1]
