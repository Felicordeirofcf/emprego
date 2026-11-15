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
        {"number": 5, "title": "[Remoto] Desenvolvedor Back-end @ Beta", "html_url": "https://github.com/backend-br/vagas/issues/5",
         "body": "Node", "created_at": _recente(), "labels": [{"name": "Júnior"}, {"name": "Remoto"}]},
    ]
    chamadas = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        return httpx.Response(200, json=issues if "backend-br" in str(req.url) else [])

    scraper = GitHubVagasScraper(settings, http=_http(handler))
    res = scraper.executar()
    assert res.erros == []
    assert [v.id_externo for v in res.vagas] == ["1", "4", "5"]
    v1, v4, _ = res.vagas
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
             "state": "Rio de Janeiro", "url": "https://d.gupy.io/jobs/13", "publishedDate": _recente()},
            {"id": 14, "name": "Estágio em TI", "careerPageName": "Confidencial", "description": "",
             "type": "vacancy_type_internship", "workplaceType": "remote", "url": "https://e.gupy.io/jobs/14",
             "publishedDate": _recente()},
        ],
        "pagination": {"offset": 0, "limit": 50, "total": 4},
    }
    termos_vistos = []

    def handler(req):
        assert req.url.host == "portal.gupy.io" and req.url.path == "/api/job-search/jobs"
        assert req.url.params["type"] == "vacancy_type_internship" and req.url.params["limit"] == "100"
        termos_vistos.append(req.url.params["jobName"])
        return httpx.Response(200, json=pagina)

    s = __import__("dataclasses").replace(settings, incluir_junior=False)
    res = GupyScraper(s, http=_http(handler)).executar()
    assert res.erros == []
    assert {v.id_externo for v in res.vagas} == {"10", "13", "14"}  # deduplicado entre termos
    assert next(v for v in res.vagas if v.id_externo == "14").empresa is None  # confidencial
    d = next(v for v in res.vagas if v.id_externo == "13")
    assert d.modalidade == "hibrido" and d.localizacao == "Rio de Janeiro - Rio de Janeiro"
    assert len(termos_vistos) == len(settings.gupy_termos)


def test_gupy_erro_http_registrado(settings):
    res = GupyScraper(settings, http=_http(lambda req: httpx.Response(403, text="forbidden"))).executar()
    assert res.vagas == [] and len(res.erros) == len(settings.gupy_termos) + len(settings.gupy_termos_junior)


def test_gupy_sem_filtro_type_quando_servidor_recusa(settings):
    chamadas = []

    def handler(req):
        chamadas.append(dict(req.url.params))
        if "type" in req.url.params:
            return httpx.Response(400, json={"message": "invalid type"})
        return httpx.Response(200, json={"data": [
            {"id": 1, "name": "Estágio Dev", "careerPageName": "A", "type": "vacancy_type_internship",
             "workplaceType": "remote", "url": "https://a.gupy.io/jobs/1", "publishedDate": _recente()},
            {"id": 2, "name": "Dev Pleno", "careerPageName": "B", "type": "vacancy_type_effective",
             "workplaceType": "remote", "url": "https://b.gupy.io/jobs/2", "publishedDate": _recente()},
        ], "pagination": {"offset": 0, "limit": 100, "total": 2}})

    s = __import__("dataclasses").replace(settings, gupy_termos=["estágio"])
    res = GupyScraper(s, http=_http(handler)).executar()
    assert [v.id_externo for v in res.vagas] == ["1"] and res.erros == []
    assert "type" in chamadas[0] and "type" not in chamadas[1]


def test_github_sem_junior_quando_desligado(settings):
    issues = [{"number": 5, "title": "Dev Back-end", "html_url": "https://github.com/x/vagas/issues/5", "body": "",
               "created_at": _recente(), "labels": [{"name": "Júnior"}]}]
    s = __import__("dataclasses").replace(settings, incluir_junior=False, github_repos=["x/vagas"])
    res = GitHubVagasScraper(s, http=_http(lambda req: httpx.Response(200, json=issues))).executar()
    assert res.vagas == []


def test_gupy_busca_junior_sem_filtro_de_tipo(settings):
    def handler(req):
        assert "type" not in req.url.params  # busca de júnior não filtra por tipo no servidor
        return httpx.Response(200, json={"data": [
            {"id": 21, "name": "Desenvolvedor Júnior Node.js", "careerPageName": "A", "type": "vacancy_type_effective",
             "workplaceType": "remote", "url": "https://a.gupy.io/jobs/21", "publishedDate": _recente()},
            {"id": 22, "name": "Desenvolvedor Pleno Node.js", "careerPageName": "B", "type": "vacancy_type_effective",
             "workplaceType": "remote", "url": "https://b.gupy.io/jobs/22", "publishedDate": _recente()},
            {"id": 23, "name": "Jovem Aprendiz Jr", "careerPageName": "C", "type": "vacancy_type_apprentice",
             "workplaceType": "remote", "url": "https://c.gupy.io/jobs/23", "publishedDate": _recente()},
        ], "pagination": {"offset": 0, "limit": 100, "total": 3}})

    s = __import__("dataclasses").replace(settings, gupy_termos=[], gupy_termos_junior=["desenvolvedor júnior"])
    res = GupyScraper(s, http=_http(handler)).executar()
    assert [v.id_externo for v in res.vagas] == ["21"]
