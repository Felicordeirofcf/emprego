from datetime import datetime, timedelta, timezone

import httpx

from src.scrapers.base import HttpClient
from src.scrapers.github_vagas import GitHubVagasScraper, detectar_localizacao, extrair_empresa
from src.scrapers.gupy import GupyScraper


def _http(handler) -> HttpClient:
    return HttpClient(transport=httpx.MockTransport(handler), intervalo_minimo=0, sleep=lambda s: None)


def _recente(dias=1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_github_filtra_estagio_e_ignora_prs(settings):
    issues = [
        {"number": 1, "title": "[Remoto] Estagiário Back-end @ Acme", "html_url": "https://github.com/backend-br/vagas/issues/1",
         "body": "Node e SQL", "created_at": _recente(), "labels": [{"name": "Estágio"}, {"name": "Remoto"}]},
        {"number": 2, "title": "[Rio de Janeiro] Dev Pleno", "html_url": "https://github.com/backend-br/vagas/issues/2",
         "body": "", "created_at": _recente(), "labels": [{"name": "Pleno"}]},
        {"number": 3, "title": "Estágio front", "html_url": "https://github.com/backend-br/vagas/pull/3",
         "body": "", "created_at": _recente(), "labels": [], "pull_request": {}},
        {"number": 4, "title": "Vaga de estagio em dados", "html_url": "https://github.com/backend-br/vagas/issues/4",
         "body": "Python", "created_at": _recente(), "labels": [{"name": "Híbrido"}, {"name": "RJ"}]},
    ]
    chamadas = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        return httpx.Response(200, json=issues if "backend-br" in str(req.url) else [])

    scraper = GitHubVagasScraper(settings, http=_http(handler))
    res = scraper.executar()
    assert res.erros == []
    assert [v.id_externo for v in res.vagas] == ["1", "4"]
    v1, v4 = res.vagas
    assert v1.modalidade == "remoto" and v1.empresa == "Acme"
    assert v4.modalidade == "hibrido" and v4.localizacao == "Rio de Janeiro - RJ"
    assert len(chamadas) == 3  # um request por repositório
    assert chamadas[0].url.params["state"] == "open"


def test_github_repo_inexistente_vira_erro_sem_derrubar(settings):
    scraper = GitHubVagasScraper(settings, http=_http(lambda req: httpx.Response(404, json={"message": "Not Found"})))
    res = scraper.executar()
    assert res.vagas == [] and len(res.erros) == 3


def test_http_retenta_em_429(settings):
    respostas = iter([httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"data": []})])
    http = _http(lambda req: next(respostas))
    assert http.get("https://x.test").status_code == 200


def test_helpers_github():
    assert extrair_empresa("[Remoto] Estágio Full Stack na Empresa Legal") == "Empresa Legal"
    assert detectar_localizacao(["Presencial"], "Estágio em Go") is None
    assert detectar_localizacao(["SP"], "Estágio") == "SP"


def test_gupy_filtra_tipo_e_normaliza(settings):
    pagina = {
        "data": [
            {"id": 10, "name": "Estágio em Desenvolvimento", "careerPageName": "Empresa A", "description": "React",
             "type": "vacancy_type_internship", "workplaceType": "remote", "city": "", "state": "",
             "jobUrl": "https://a.gupy.io/jobs/10", "publishedDate": _recente()},
            {"id": 11, "name": "Analista de Sistemas", "careerPageName": "Empresa B", "description": "",
             "type": "vacancy_type_effective", "workplaceType": "on-site", "jobUrl": "https://b.gupy.io/jobs/11",
             "publishedDate": _recente()},
            {"id": 12, "name": "Jovem Aprendiz", "careerPageName": "C", "type": "vacancy_type_apprentice",
             "jobUrl": "https://c.gupy.io/jobs/12"},
            {"id": 13, "name": "Estágio Dev Python", "careerPageName": "Empresa D", "description": "Python",
             "type": "vacancy_type_internship", "workplaceType": "hybrid", "city": "Rio de Janeiro",
             "state": "Rio de Janeiro", "jobUrl": "https://d.gupy.io/jobs/13", "publishedDate": _recente()},
        ],
        "pagination": {"offset": 0, "limit": 50, "total": 4},
    }
    termos_vistos = []

    def handler(req):
        termos_vistos.append(req.url.params["jobName"])
        return httpx.Response(200, json=pagina)

    res = GupyScraper(settings, http=_http(handler)).executar()
    assert res.erros == []
    assert {v.id_externo for v in res.vagas} == {"10", "13"}  # deduplicado entre termos
    d = next(v for v in res.vagas if v.id_externo == "13")
    assert d.modalidade == "hibrido" and d.localizacao == "Rio de Janeiro - Rio de Janeiro"
    assert len(termos_vistos) == len(settings.gupy_termos)


def test_gupy_erro_http_registrado(settings):
    res = GupyScraper(settings, http=_http(lambda req: httpx.Response(403, text="forbidden"))).executar()
    assert res.vagas == [] and len(res.erros) == len(settings.gupy_termos)
