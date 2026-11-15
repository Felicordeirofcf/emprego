from datetime import datetime, timedelta, timezone

from src import prefiltro
from src.models import MatchResult, hash_url

from .conftest import nova_vaga


def _match(**kw):
    base = dict(
        elegivel_estagio=True, motivo_inelegibilidade=None, score=88, justificativa="ok",
        modalidade_detectada="remoto", tecnologias_match=["React"], lacunas=[],
        projeto_portfolio_destacar="AgendaPro", pitch_candidatura="Olá",
    )
    base.update(kw)
    return MatchResult.model_validate(base)


def test_inelegivel_sempre_score_zero():
    r = _match(elegivel_estagio=False, score=90, motivo_inelegibilidade=None)
    assert r.score == 0
    assert r.motivo_inelegibilidade


def test_score_e_limitado_0_100():
    assert _match(score=140).score == 100
    assert _match(score=-3).score == 0


def test_hash_ignora_query_e_barra_final():
    assert hash_url("https://a.com/vaga/1?utm=x") == hash_url("https://A.com/vaga/1/")


def test_prefiltro_aceita_estagio_remoto(settings):
    assert prefiltro.avaliar(nova_vaga(), settings) == (True, None)


def test_prefiltro_descarta_senior(settings):
    ok, motivo = prefiltro.avaliar(nova_vaga(titulo="Desenvolvedor Sênior Node"), settings)
    assert not ok and "senior" in motivo


def test_prefiltro_mantem_estagio_ou_junior(settings):
    ok, _ = prefiltro.avaliar(nova_vaga(titulo="Estágio ou Júnior Front-end"), settings)
    assert ok


def test_prefiltro_descarta_presencial_fora_do_rj(settings):
    ok, motivo = prefiltro.avaliar(
        nova_vaga(modalidade="presencial", localizacao="São Paulo - São Paulo"), settings)
    assert not ok and "fora do RJ" in motivo


def test_prefiltro_aceita_hibrido_no_rj(settings):
    ok, _ = prefiltro.avaliar(nova_vaga(modalidade="hibrido", localizacao="Rio de Janeiro - RJ"), settings)
    assert ok


def test_prefiltro_descarta_vaga_antiga(settings):
    antiga = datetime.now(timezone.utc) - timedelta(days=90)
    ok, _ = prefiltro.avaliar(nova_vaga(publicada_em=antiga), settings)
    assert not ok


def test_prefiltro_descarta_aprendiz(settings):
    ok, _ = prefiltro.avaliar(nova_vaga(titulo="Jovem Aprendiz TI"), settings)
    assert not ok
