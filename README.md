# 🤖 Robô de Vagas de Estágio em Tecnologia (Claude API)

📊 **Dashboard:** https://felicordeirofcf.github.io/emprego/ · ⚙️ **Execuções:** [aba Actions](https://github.com/Felicordeirofcf/emprego/actions)

Robô que **coleta vagas de estágio** (GitHub e Gupy), **compara cada vaga com o seu currículo e portfólio** usando a API do Claude, calcula um **score de compatibilidade de 0 a 100** e mostra as melhores no bloco **🔔 Alertas** de um **dashboard de acompanhamento** das suas candidaturas. O aviso pelo Telegram é opcional.

- **Só estágio:** vagas Júnior/Pleno/Sênior, que exigem diploma ou que são presenciais/híbridas fora do RJ ficam com `elegivel_estagio: false` e `score: 0`.
- **Portfólio como diferencial:** a rubrica do Claude dá peso alto (até 25 pts) a projetos reais em produção.
- **Custo baixo:** triagem com o **Claude Haiku 5.5** (US$ 0,10 por milhão de tokens de entrada). Um pré-filtro local descarta o óbvio sem gastar tokens. O perfil vai em **prompt caching**, então da 2ª vaga em diante ele é cobrado a 10% do preço.
- **Resposta 100% tipada:** **Tool Use** com JSON Schema estrito (`strict: true`) e `tool_choice` forçado, validado de novo com Pydantic.

```
 GitHub Issues ─┐                                   ┌─► SQLite (data/vagas.db)
 Gupy API ──────┼─► normaliza ─► deduplica ─► pré-filtro ─► Claude Haiku 5.5 (triagem, tool use + cache)
 (+ seus        │   (Vaga)      (url_hash +    (0 tokens)        │
  scrapers)  ───┘               fingerprint)                     ├─► score ≥ 85: Claude Sonnet 5.5 (pitch)
                                                                 ├─► score ≥ 75: 🔔 Alertas no dashboard (Telegram opcional)
                                                                 └─► docs/index.html (dashboard)
```

---

## 📁 Estrutura

```
robo-vagas-estagio/
├── .github/workflows/monitor.yml   # roda a cada 6h no GitHub Actions e commita o .db + dashboard
├── data/
│   ├── perfil.json                 # SEU currículo/portfólio (preencha!)
│   └── vagas.db                    # criado na 1ª execução
├── docs/index.html                 # dashboard gerado (GitHub Pages)
├── src/
│   ├── config.py                   # variáveis de ambiente e tabela de preços
│   ├── models.py                   # Vaga, MatchResult (Pydantic), UsoTokens
│   ├── database.py                 # SQLAlchemy + SQLite: tabelas vagas/execucoes, duplicidade
│   ├── prefiltro.py                # descarte determinístico (nível, região, data)
│   ├── claude_matcher.py           # Claude: tool use estrito + prompt caching + pitch
│   ├── notifier.py                 # Telegram (opcional) com MarkdownV2 e escape seguro
│   ├── dashboard.py                # gera o dashboard a partir do banco
│   ├── dashboard_template.html     # layout do dashboard (HTML/JS sem dependências)
│   ├── main.py                     # orquestrador
│   └── scrapers/
│       ├── __init__.py             # REGISTRO de fontes
│       ├── base.py                 # JobScraper (classe base) + HttpClient com retry/rate limit
│       ├── github_vagas.py         # backend-br, frontendbr, react-brasil
│       └── gupy.py                 # API pública do portal da Gupy
├── tests/                          # 36 testes (pytest), sem rede e sem gastar tokens
├── .env.example
├── Dockerfile
├── requirements.txt / requirements-dev.txt
└── README.md
```

---

## 🚀 Instalação

Requisitos: **Python 3.11+** e Git.

```bash
git clone https://github.com/SEU-USUARIO/robo-vagas-estagio.git
```

```bash
cd robo-vagas-estagio
```

```bash
python -m venv .venv && source .venv/bin/activate
```

(No Windows: `.venv\Scripts\activate`.)

```bash
pip install -r requirements-dev.txt
```

```bash
cp .env.example .env
```

Depois preencha o `.env` (passos abaixo) e o **`data/perfil.json`** com os seus dados reais.

### 1. Obter a `ANTHROPIC_API_KEY`

1. Crie uma conta em <https://console.anthropic.com>.
2. Em **Settings → Billing**, adicione créditos (US$ 5 rendem meses de uso deste robô).
3. Em **Settings → API Keys**, clique em **Create Key**, dê um nome (ex.: `robo-vagas`) e copie a chave `sk-ant-...`.
4. Cole no `.env`: `ANTHROPIC_API_KEY=sk-ant-...`.

> Nunca commite o `.env`. Ele já está no `.gitignore`.

### 2. (Opcional) Criar o bot do Telegram

Pule esta etapa se quiser os alertas só no dashboard, que é o padrão do workflow.

1. No Telegram, abra uma conversa com **@BotFather** e envie `/newbot`.
2. Escolha um nome e um usuário terminado em `bot` (ex.: `minhas_vagas_estagio_bot`).
3. O BotFather devolve um token do tipo `123456789:AAH...`. Coloque em `TELEGRAM_BOT_TOKEN`.
4. **Abra a conversa com o seu bot e envie qualquer mensagem** (ex.: `oi`). Sem isso o bot não consegue falar com você.

#### Como descobrir o `TELEGRAM_CHAT_ID`

Com o token no `.env`, acesse no navegador (troque `<TOKEN>`):

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Procure `"chat":{"id":123456789,...}`. Esse número é o seu `TELEGRAM_CHAT_ID`. Para receber num grupo, adicione o bot ao grupo, mande uma mensagem e use o id do grupo (é negativo, ex.: `-100...`).

### 3. Preencher o `data/perfil.json`

O arquivo já vem com um exemplo realista de stack full stack. **Enquanto a linha `"_exemplo": true` existir, o robô só coleta vagas**: não chama o Claude nem envia nada, para nunca gerar pitch com projetos fictícios. Troque tudo pelos seus dados, apague essa linha e capriche principalmente em:

- `formacao.periodo_atual` e `formacao.previsao_formatura` (o Claude usa para checar requisitos de período);
- `projetos_portfolio`: problema resolvido, stack, responsabilidades e **métricas reais** (usuários, clientes, tempo economizado). É daqui que saem o score de "evidência em produção" e o pitch.

> O perfil inteiro vai para o prompt. Não coloque CPF, endereço completo ou telefone.
>
> O Haiku 5.5 faz cache a partir de **512 tokens**, então qualquer perfil razoável já se beneficia. O robô confere o tamanho a cada execução e mostra no log.

---

## ▶️ Como rodar

```bash
python -m src.main
```

Opções úteis:

```bash
python -m src.main --dry-run            # analisa, mas não envia nada ao Telegram
```

```bash
python -m src.main --apenas-coleta      # só coleta e salva (0 tokens), bom para testar os scrapers
```

```bash
python -m src.main --fontes gupy --max-vagas 5
```

Exemplo de log:

```
08:17:02 | INFO    | 🤖 Robô de Vagas de Estágio — iniciando
08:17:02 | INFO    | 1/4 · COLETA
08:17:05 | INFO    |   github    11 vaga(s) de estágio em 2.9s
08:17:14 | INFO    |   gupy      37 vaga(s) de estágio em 8.7s
08:17:14 | INFO    | 2/4 · DEDUPLICAÇÃO E PRÉ-FILTRO
08:17:14 | INFO    |   9 nova(s) · 39 já conhecida(s) · 2 descartada(s) pelo pré-filtro (0 tokens)
08:17:14 | INFO    | 3/4 · ANÁLISE COM CLAUDE (claude-haiku-5-5)
08:17:15 | INFO    | Prefixo cacheável: ~7800 tokens (mínimo do claude-haiku-5-5: 512) ✓
08:17:18 | INFO    |   ★  91  Estágio em Desenvolvimento Full Stack cache_write=6012
08:17:20 | INFO    |   ·  58  Estágio Back-end Java cache_read=6012
08:17:21 | INFO    |   ✗   0  Estágio TI (inelegível: Presencial em São Paulo/SP) cache_read=6012
...
08:17:40 | INFO    | RESUMO DA EXECUÇÃO
08:17:40 | INFO    |   Analisadas pelo Claude ....... 7
08:17:40 | INFO    |   Aprovadas (score mínimo) ..... 2
08:17:40 | INFO    |   Custo estimado ............... US$ 0.0461
08:17:40 | INFO    |   Economia com prompt caching .. US$ 0.0068
```

### Com Docker

```bash
docker build -t robo-vagas .
```

```bash
docker run --rm --env-file .env -v "$PWD/data:/app/data" -v "$PWD/docs:/app/docs" robo-vagas
```

---

## 📊 Dashboard de acompanhamento

A cada execução o robô gera `docs/index.html`, um arquivo único, sem dependências externas, com tema claro e escuro:

- **🔔 Alertas (topo da página):** vagas elegíveis com score ≥ `MIN_SCORE` que você ainda não avaliou. As que chegaram desde a sua última visita levam o selo **NOVA**, e a aba do navegador mostra a contagem, por exemplo "(3) Robô de Vagas". Cada alerta tem os botões *Ver pitch*, *Tenho interesse*, *Candidatei* e *Descartar*; ao escolher uma ação, a vaga sai dos alertas e segue na tabela. *Marcar todas como vistas* zera o selo NOVA.
- **Indicadores:** vagas coletadas, analisadas, aprovadas, suas candidaturas, custo da IA e economia com cache.
- **Funil:** Coletadas → Analisadas → Elegíveis → Aprovadas → Candidatei → Entrevista → Proposta.
- **Distribuição de score** com a linha do score mínimo.
- **Vagas por execução** e **custo por execução**.
- **Tecnologias com mais match** (seus pontos fortes) e **lacunas mais frequentes** (o que estudar).
- **Tabela de vagas** com filtros por período, fonte, modalidade, etapa, score e busca. Ao clicar numa linha você vê a análise do Claude, os pontos fortes, os gaps e o pitch, com botão **Copiar**.
- **"Minha etapa"** (Nova, Tenho interesse, Candidatei, Entrevista, Proposta...): fica salva no `localStorage` do navegador, com botões para exportar e importar em JSON.

Abrir localmente:

```bash
python -m src.dashboard --serve
```

Depois acesse <http://localhost:8000/index.html>. Também dá para abrir o arquivo direto no navegador.

**Publicar no GitHub Pages:** em *Settings → Pages*, escolha *Deploy from a branch*, branch `main` e pasta `/docs`. O workflow já commita o dashboard atualizado a cada 6h.

> ⚠️ Num repositório **público**, o `vagas.db` e o dashboard (com seus pitches) ficam públicos. Recomendo repositório **privado**. GitHub Pages em repositório privado exige plano pago; sem ele, abra o `docs/index.html` baixado do repositório.

---

## ⏰ Agendamento

### GitHub Actions (grátis, na nuvem) — recomendado

1. Suba o projeto para um repositório seu no GitHub (de preferência privado).
2. Em **Settings → Secrets and variables → Actions → New repository secret**, crie:
   - `ANTHROPIC_API_KEY`
   - (opcional, para receber também no Telegram) `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`, descomentando as duas linhas correspondentes em `.github/workflows/monitor.yml`
3. (Opcional) Na aba **Variables**, ajuste `MIN_SCORE`, `CLAUDE_MODEL_TRIAGEM`, `CLAUDE_MODEL_PITCH`, `ENABLE_SONNET_PITCH` e `MAX_VAGAS_POR_EXECUCAO`.
4. Em **Settings → Actions → General → Workflow permissions**, marque **Read and write permissions**.
5. Na aba **Actions**, abra *Monitor de vagas de estágio* e clique em **Run workflow** para testar (há a opção `dry_run`).

O workflow (`.github/workflows/monitor.yml`) roda a cada 6 horas (`17 */6 * * *`, horário UTC), usa o `GITHUB_TOKEN` automático para a API do GitHub (5.000 req/h), impede execuções simultâneas e commita `data/vagas.db` + `docs/index.html` no fim. O histórico de vagas fica no próprio repositório.

> O GitHub desativa workflows agendados de repositórios sem atividade há 60 dias. Os commits automáticos do robô contam como atividade.

### Cron (Linux/macOS, máquina ou VPS própria)

```bash
crontab -e
```

Adicione a linha (ajuste o caminho):

```
17 */6 * * * cd /home/voce/robo-vagas-estagio && .venv/bin/python -m src.main >> data/robo.log 2>&1
```

No Windows, use o **Agendador de Tarefas** apontando para `.venv\Scripts\python.exe -m src.main`, com "Iniciar em" na pasta do projeto.

---

## ⚙️ Configuração (`.env`)

| Variável | Padrão | Para que serve |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chave da API do Claude |
| `CLAUDE_MODEL_TRIAGEM` | `claude-haiku-5-5` | Modelo da análise estruturada |
| `CLAUDE_MODEL_PITCH` | `claude-sonnet-5-5` | Modelo da carta/pitch personalizado |
| `CLAUDE_EFFORT_TRIAGEM` / `CLAUDE_EFFORT_PITCH` | `medium` | Esforço de raciocínio: `low`, `medium`, `high`, `xhigh`, `max` |
| `ENABLE_SONNET_PITCH` | `true` | Liga/desliga o pitch com o modelo maior |
| `SONNET_PITCH_MIN_SCORE` | `85` | Score a partir do qual o pitch é gerado |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Opcional: bot e chat de destino (sem eles, alertas só no dashboard) |
| `TELEGRAM_ENVIAR_RESUMO` | `true` | Resumo da execução (só quando há vagas novas) |
| `MIN_SCORE` | `75` | Score mínimo para notificar |
| `MAX_VAGAS_POR_EXECUCAO` | `60` | Teto de vagas analisadas por rodada (controle de custo) |
| `MAX_CONCORRENCIA` | `3` | Análises em paralelo depois da 1ª (que aquece o cache) |
| `MAX_DESCRICAO_CHARS` | `15000` | Corta descrições gigantes (com aviso no log) |
| `DIAS_MAXIMOS_VAGA` | `30` | Ignora vagas mais antigas |
| `UF_PRESENCIAL` / `ESTADO_PRESENCIAL` | `RJ` / `Rio de Janeiro` | Região aceita para presencial/híbrido |
| `FONTES_ATIVAS` | `github,gupy` | Fontes habilitadas |
| `GITHUB_REPOS` | 3 repositórios | Repositórios de vagas no GitHub |
| `GUPY_TERMOS` | 14 termos de tecnologia | Buscas na Gupy, separadas por `;` (o filtro de estágio é automático) |

---

## 🧠 Como o Claude decide

`src/claude_matcher.py` envia para cada vaga:

```python
client.messages.create(
    model="claude-haiku-5-5",
    system=[
        {"type": "text", "text": INSTRUCOES_TRIAGEM},             # rubrica + regras de elegibilidade
        {"type": "text", "text": "<perfil_candidato>...</perfil_candidato>",
         "cache_control": {"type": "ephemeral"}},                # ← prompt caching
    ],
    tools=[TOOL_ANALISE],                                         # JSON Schema estrito
    tool_choice={"type": "tool", "name": "registrar_analise_vaga"},  # resposta sempre via tool
    output_config={"effort": "medium"},
    messages=[{"role": "user", "content": "<vaga>...</vaga>"}],
)
```

O pitch usa o **Claude Sonnet 5.5** com raciocínio adaptativo e **fallback automático**: se o modelo recusar a solicitação, a própria API refaz a chamada num modelo de fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`). O Haiku 5.5 não tem fallback do lado do servidor; uma recusa na triagem vira `erro` e a vaga é tentada de novo depois.

- **Elegibilidade (eliminatória):** não é estágio, exige diploma, curso incompatível, presencial/híbrido fora do RJ, período de formatura incompatível ou fora de TI → `score = 0`. A regra é aplicada **de novo no código** (`MatchResult`), mesmo que o modelo erre.
- **Score (0–100):** stack (40) + evidência em produção (25) + área (15) + requisitos acadêmicos e logísticos (10) + qualidade da vaga (10), com faixas de calibração e exemplos no prompt.
- **Campos:** `elegivel_estagio`, `motivo_inelegibilidade`, `score`, `tecnologias_match`, `lacunas`, `projeto_portfolio_destacar`, `pitch_candidatura`, além de `justificativa` e `modalidade_detectada`.
- **Proteção contra prompt injection:** o texto da vaga vai dentro de `<vaga>` e o modelo é instruído a ignorar instruções contidas nele.
- **Erros:** recusa, corte por `max_tokens`, saída fora do schema, rate limit e falhas de rede marcam a vaga como `erro`. Ela é tentada de novo nas próximas execuções, até 3 vezes. O SDK já faz até 4 retries com backoff em 429/5xx.

### Custos estimados (preços da Anthropic)

| Etapa | Custo aproximado |
|---|---|
| Triagem por vaga (Haiku 5.5, perfil lido do cache) | ~US$ 0,0005–0,001 |
| 1ª vaga da rodada (grava o cache) | +~US$ 0,001 |
| Pitch Sonnet 5.5 (só score ≥ 85) | ~US$ 0,02–0,04 |
| Rodada típica (5–15 vagas novas, 1–2 pitches) | ~US$ 0,01–0,08 |

O teto por rodada é controlado por `MAX_VAGAS_POR_EXECUCAO`. O custo real de cada execução aparece no log e no dashboard.

> Quase todo o custo vem do pitch com o Sonnet. Para gastar menos, suba `SONNET_PITCH_MIN_SCORE` ou use `CLAUDE_EFFORT_PITCH=low`. Os modelos são configuráveis (`CLAUDE_MODEL_TRIAGEM` / `CLAUDE_MODEL_PITCH`) e os preços ficam em `src/config.py`. Para modelos que não aceitam `tool_choice` forçado, o código usa `auto` automaticamente, mantendo o schema estrito.

---

## 🧩 Adicionando uma nova fonte (Programathor, CIEE, Nube...)

1. Crie `src/scrapers/programathor.py`:

```python
from typing import Iterable

from ..models import Vaga
from .base import HttpClient, JobScraper, ScraperError


class ProgramathorScraper(JobScraper):
    nome = "programathor"

    def criar_http(self) -> HttpClient:
        return HttpClient(intervalo_minimo=1.5)  # educação com o servidor

    def coletar(self) -> Iterable[Vaga]:
        resp = self.http.get("https://...")  # retry, Retry-After e backoff já inclusos
        if resp.status_code >= 400:
            raise ScraperError(f"HTTP {resp.status_code}")
        for item in ...:  # parse do JSON/HTML
            yield Vaga(fonte=self.nome, url=..., titulo=..., empresa=..., descricao=...,
                       modalidade="remoto", localizacao="Rio de Janeiro - RJ")
```

2. Registre em `src/scrapers/__init__.py`: `REGISTRO["programathor"] = ProgramathorScraper`.
3. Ative em `FONTES_ATIVAS=github,gupy,programathor`.

Deduplicação, pré-filtro, análise, notificação e dashboard funcionam sem nenhuma outra mudança. Respeite os Termos de Uso e o `robots.txt` de cada site.

---

## 🧪 Testes

```bash
pytest
```

São 36 testes: scrapers com HTTP simulado, deduplicação, pré-filtro, request do Claude (cache, tool forçada, schema), regra de `score=0`, escape do MarkdownV2, fallback e rate limit do Telegram, e um teste ponta a ponta do orquestrador com geração do dashboard. Nenhum teste acessa a rede ou gasta tokens.

---

## 🛠️ Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| `ANTHROPIC_API_KEY não definida` | Preencha o `.env` (local) ou o secret (Actions) |
| `Rate limit da API do GitHub esgotado` | Defina `GITHUB_TOKEN` (no Actions já é automático) |
| Log avisa "prefixo abaixo do mínimo" | Perfil curto demais para o cache: detalhe mais os projetos |
| `cache_read` sempre 0 | O perfil mudou entre chamadas ou passaram mais de 5 min entre elas |
| Telegram `chat not found` | Você não mandou mensagem para o bot, ou o `TELEGRAM_CHAT_ID` está errado |
| Gupy retorna 403/429 | Bloqueio temporário: o robô registra o erro e segue com as outras fontes |
| Muitas vagas com `erro` | Veja a coluna "Último erro" no dashboard (clique na vaga) |
| Workflow não commita | Ative *Read and write permissions* em Settings → Actions |
