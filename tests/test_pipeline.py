"""Teste ponta a ponta do orquestrador com fontes e Claude simulados."""

import src.main as main_mod
from src.claude_matcher import ResultadoAnalise
from src.database import Repositorio, criar_engine, criar_session_factory
from src.models import MatchResult, UsoTokens
from src.scrapers.base import JobScraper

from .conftest import nova_vaga


class FonteFake(JobScraper):
    nome = "fake"

    def coletar(self):
        yield nova_vaga(url="https://a.com/1", titulo="Estágio Full Stack React", empresa="A")
        yield nova_vaga(url="https://a.com/2", titulo="Estágio Java", empresa="B")
        yield nova_vaga(url="https://a.com/3", titulo="Desenvolvedor Sênior", empresa="C")
        yield nova_vaga(url="https://a.com/4", titulo="Estágio Node", empresa="D")


class MatcherFake:
    def __init__(self, settings, perfil=None, client=None):
        self.settings = settings

    def verificar_tamanho_cache(self):
        return 5000

    def analisar(self, vaga):
        score, eleg = {"A": (92, True), "B": (55, True), "D": (0, False)}[vaga.empresa]
        r = MatchResult(
            elegivel_estagio=eleg, motivo_inelegibilidade=None if eleg else "Exige formado", score=score,
            justificativa="j", modalidade_detectada="remoto", tecnologias_match=["React"], lacunas=["Java"],
            projeto_portfolio_destacar="AgendaPro", pitch_candidatura="pitch curto",
        )
        return ResultadoAnalise(r, UsoTokens(input_tokens=100, output_tokens=50, cache_read_tokens=4000,
                                             custo_usd=0.001, economia_usd=0.003, chamadas=1), "claude-haiku-5-5")

    def gerar_pitch(self, vaga, analise):
        return "Carta Sonnet", UsoTokens(chamadas=1, custo_usd=0.01), "claude-sonnet-5-5"


def test_pipeline_completo(settings, monkeypatch):
    class HttpFake:
        def close(self):
            pass

    monkeypatch.setattr(main_mod, "criar_scrapers", lambda s, f=None: [FonteFake(s, http=HttpFake())])
    monkeypatch.setattr(main_mod, "ClaudeMatcher", MatcherFake)

    assert main_mod.main(["--dry-run"]) == 0

    engine = criar_engine(settings.database_path)
    with criar_session_factory(engine)() as s:
        repo = Repositorio(s)
        vagas = {v.empresa: v for v in repo.todas_vagas()}
        assert vagas["C"].status == "descartada_prefiltro" and vagas["C"].score == 0
        assert vagas["A"].score == 92 and vagas["A"].carta_apresentacao == "Carta Sonnet"
        assert vagas["B"].carta_apresentacao is None
        assert vagas["D"].elegivel_estagio is False and vagas["D"].score == 0
        exe = repo.todas_execucoes()[-1]
        assert (exe.encontradas, exe.novas, exe.descartadas_prefiltro, exe.analisadas, exe.aprovadas) == (4, 4, 1, 3, 1)
        assert exe.notificadas == 0  # dry-run

    html = settings.dashboard_path.read_text(encoding="utf-8")
    assert "Estágio Full Stack React" in html and "__DADOS_JSON__" not in html

    # 2ª execução: tudo já conhecido, nada novo
    assert main_mod.main(["--dry-run"]) == 0
    with criar_session_factory(engine)() as s:
        assert Repositorio(s).todas_execucoes()[-1].novas == 0


def test_perfil_exemplo_so_coleta(settings, monkeypatch, tmp_path):
    import json
    from .conftest import RAIZ
    perfil = json.loads((RAIZ / "data" / "perfil.json").read_text(encoding="utf-8"))
    perfil["_exemplo"] = True
    caminho = tmp_path / "perfil.json"
    caminho.write_text(json.dumps(perfil), encoding="utf-8")
    monkeypatch.setenv("PERFIL_PATH", str(caminho))

    class HttpFake:
        def close(self):
            pass

    def nao_chamar(*a, **k):
        raise AssertionError("Claude não deve ser chamado com perfil de exemplo")

    monkeypatch.setattr(main_mod, "criar_scrapers", lambda s, f=None: [FonteFake(s, http=HttpFake())])
    monkeypatch.setattr(main_mod, "ClaudeMatcher", nao_chamar)
    assert main_mod.main([]) == 0
