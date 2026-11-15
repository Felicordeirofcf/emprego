from src.database import STATUS_ANALISADA, STATUS_DESCARTADA, STATUS_ERRO, MAX_TENTATIVAS
from src.models import MatchResult

from .conftest import nova_vaga


def _resultado(score=90, elegivel=True):
    return MatchResult(
        elegivel_estagio=elegivel, motivo_inelegibilidade=None if elegivel else "Júnior", score=score,
        justificativa="j", modalidade_detectada="remoto", tecnologias_match=["React", "Node.js"],
        lacunas=["Java"], projeto_portfolio_destacar="AgendaPro", pitch_candidatura="pitch",
    )


def test_duplicidade_por_url_e_por_fingerprint(repo):
    v = nova_vaga()
    repo.inserir_vaga(v)
    assert repo.ja_existe(nova_vaga(url="https://empresa.gupy.io/jobs/123/"))  # mesma URL normalizada
    assert repo.ja_existe(nova_vaga(url="https://github.com/x/vagas/issues/9", fonte="github"))  # mesma empresa+título
    assert not repo.ja_existe(nova_vaga(url="https://outra.com/1", titulo="Estágio Dados", empresa="Outra"))


def test_fluxo_analise_e_notificacao(repo):
    a = repo.inserir_vaga(nova_vaga())
    b = repo.inserir_vaga(nova_vaga(url="https://x.com/2", titulo="Estágio Back", empresa="B"))
    repo.inserir_vaga(nova_vaga(url="https://x.com/3", titulo="Dev Pleno", empresa="C"), STATUS_DESCARTADA, "Pleno")
    assert {r.id for r in repo.pendentes_de_analise(10)} == {a.id, b.id}

    repo.salvar_analise(a, _resultado(90), "claude-haiku-5-5")
    repo.salvar_analise(b, _resultado(60), "claude-haiku-5-5")
    assert a.status == STATUS_ANALISADA and a.tecnologias_match == ["React", "Node.js"]

    aprovadas = repo.aprovadas_nao_notificadas(75)
    assert [r.id for r in aprovadas] == [a.id]
    repo.marcar_notificada(a)
    assert repo.aprovadas_nao_notificadas(75) == []


def test_erro_e_retentado_ate_o_limite(repo):
    row = repo.inserir_vaga(nova_vaga())
    for _ in range(MAX_TENTATIVAS - 1):
        repo.registrar_erro(row, "timeout")
    assert row.status == STATUS_ERRO and repo.pendentes_de_analise(10) == [row]
    repo.registrar_erro(row, "timeout")
    assert repo.pendentes_de_analise(10) == []


def test_migracao_adiciona_coluna_nova(tmp_path):
    import sqlite3
    from src.database import criar_engine
    caminho = tmp_path / "antigo.db"
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE vagas (id INTEGER PRIMARY KEY, url_hash VARCHAR(64))")
    con.commit(); con.close()
    criar_engine(caminho)
    colunas = {r[1] for r in sqlite3.connect(caminho).execute("PRAGMA table_info(vagas)")}
    assert "nivel_vaga" in colunas and "pitch_candidatura" in colunas
