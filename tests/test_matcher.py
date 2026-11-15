import anthropic
import pytest
from anthropic.types import Message

from src.claude_matcher import NOME_TOOL, TOOL_ANALISE, AnaliseError, ClaudeMatcher, calcular_uso

from .conftest import nova_vaga

ANALISE_OK = {
    "elegivel_estagio": True, "nivel_vaga": "junior", "motivo_inelegibilidade": None, "modalidade_detectada": "remoto",
    "tecnologias_match": ["React", "Node.js", "PostgreSQL"], "lacunas": [],
    "justificativa": "Atende aos requisitos.", "score": 93,
    "projeto_portfolio_destacar": "AgendaPro — SaaS em produção", "pitch_candidatura": "Olá! Sou estudante de ADS...",
}


def _msg(content, stop_reason="tool_use", cache_read=0, cache_write=0):
    return Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 400, "output_tokens": 300,
                  "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write},
    })


class FakeMessages:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, respostas):
        self.messages = FakeMessages(respostas)
        self.beta = type("Beta", (), {})()
        self.beta.messages = self.messages  # mesma fila: os testes só olham as chamadas


def _matcher(settings, perfil, respostas):
    return ClaudeMatcher(settings, perfil=perfil, client=FakeClient(respostas))


def test_request_usa_cache_e_tool_forcada(settings, perfil):
    m = _matcher(settings, perfil, [_msg([{"type": "tool_use", "id": "t1", "name": NOME_TOOL, "input": ANALISE_OK}],
                                         cache_read=5000)])
    res = m.analisar(nova_vaga())
    assert res.resultado.score == 93 and res.resultado.nivel_vaga == "junior"
    req = m.client.messages.chamadas[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["tool_choice"] == {"type": "tool", "name": NOME_TOOL}
    assert req["output_config"] == {"effort": "medium"}
    assert "fallbacks" not in req  # Haiku 5.5 não aceita fallback
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "<perfil_candidato>" in req["system"][-1]["text"]
    assert req["tools"][0]["strict"] is True
    assert "temperature" not in req
    assert res.uso.cache_read_tokens == 5000 and res.uso.economia_usd > 0


def test_prefixo_cacheado_e_identico_entre_chamadas(settings, perfil):
    tool = {"type": "tool_use", "id": "t1", "name": NOME_TOOL, "input": ANALISE_OK}
    m = _matcher(settings, perfil, [_msg([tool]), _msg([tool])])
    m.analisar(nova_vaga())
    m.analisar(nova_vaga(titulo="Outra vaga"))
    a, b = m.client.messages.chamadas
    assert a["system"] == b["system"] and a["tools"] == b["tools"]


def test_modelo_sem_tool_choice_forcado_usa_auto():
    assert ClaudeMatcher.tool_choice("claude-opus-5-5") == {"type": "auto"}
    assert ClaudeMatcher.tool_choice("claude-sonnet-5-5") == {"type": "auto"}
    assert ClaudeMatcher.tool_choice("claude-haiku-5-5")["type"] == "tool"


def test_schema_estrito_bem_formado():
    schema = TOOL_ANALISE["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_inelegivel_zera_score(settings, perfil):
    entrada = {**ANALISE_OK, "elegivel_estagio": False, "motivo_inelegibilidade": "Nível Júnior", "score": 70}
    m = _matcher(settings, perfil, [_msg([{"type": "tool_use", "id": "t1", "name": NOME_TOOL, "input": entrada}])])
    assert m.analisar(nova_vaga()).resultado.score == 0


def test_sem_tool_use_vira_erro(settings, perfil):
    m = _matcher(settings, perfil, [_msg([{"type": "text", "text": "oi"}], stop_reason="end_turn")])
    with pytest.raises(AnaliseError):
        m.analisar(nova_vaga())


def test_refusal_vira_erro(settings, perfil):
    m = _matcher(settings, perfil, [_msg([], stop_reason="refusal")])
    with pytest.raises(AnaliseError, match="recusou"):
        m.analisar(nova_vaga())


def test_erro_de_conexao_vira_analise_error(settings, perfil):
    import httpx2
    erro = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    m = _matcher(settings, perfil, [erro])
    with pytest.raises(AnaliseError, match="conexão"):
        m.analisar(nova_vaga())


def test_pitch(settings, perfil):
    from src.models import MatchResult
    resposta = _msg([{"type": "thinking", "thinking": "", "signature": "sig"},
                     {"type": "text", "text": "Carta personalizada"}], stop_reason="end_turn")
    m = _matcher(settings, perfil, [resposta])
    texto, uso, modelo = m.gerar_pitch(nova_vaga(), MatchResult.model_validate(ANALISE_OK))
    assert texto == "Carta personalizada" and modelo == "claude-sonnet-5-5"
    req = m.client.messages.chamadas[0]
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"] == {"effort": "medium"}
    assert "thinking" not in req and "temperature" not in req


def test_pitch_modelo_antigo_sem_fallback_nem_effort(settings, perfil):
    import dataclasses
    from src.models import MatchResult
    s = dataclasses.replace(settings, model_pitch="claude-sonnet-4-6")
    m = ClaudeMatcher(s, perfil=perfil, client=FakeClient([_msg([{"type": "text", "text": "ok"}], stop_reason="end_turn")]))
    m.gerar_pitch(nova_vaga(), MatchResult.model_validate(ANALISE_OK))
    req = m.client.messages.chamadas[0]
    assert "fallbacks" not in req and "output_config" in req


def test_calculo_de_custo():
    class U:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 1_000_000
    uso = calcular_uso("claude-haiku-5-5", U())
    assert uso.custo_usd == pytest.approx(0.10 + 0.01)
    assert uso.economia_usd == pytest.approx(0.09)
    uso = calcular_uso("claude-sonnet-5-5", U())
    assert uso.custo_usd == pytest.approx(2.0 + 0.10)


def test_falta_de_credito_e_fatal(settings, perfil):
    import httpx2
    from src.claude_matcher import CreditoInsuficienteError
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(400, request=req, json={"type": "error", "error": {"type": "invalid_request_error",
                           "message": "Your credit balance is too low to access the Anthropic API."}})
    erro = anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.", response=resp, body=None)
    m = _matcher(settings, perfil, [erro])
    with pytest.raises(CreditoInsuficienteError):
        m.analisar(nova_vaga())
