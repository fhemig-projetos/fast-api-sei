# API SEI — Automação da Caixa de Processos e Inclusão de Documentos (SEI-MG)

Conjunto de scripts em Python que automatizam o acesso ao **SEI-MG (GOVMG)**:

- **Leitura** — login, troca de unidade e raspagem da caixa de **"Processos
  Recebidos" / Controle de Processos**, exportando o resultado para `.xlsx`.
- **Escrita** — **inclusão de documento interno** (Memorando, Relatório, etc.)
  dentro de um processo existente, via automação do fluxo nativo do SEI.
- **API HTTP** — exposição da raspagem como tarefas em segundo plano (FastAPI).

O projeto foi construído sobre duas regras invioláveis:

1. **Nunca acionar prazo de processo não lido.** O servidor do SEI registra a
   leitura e inicia contagem de prazos no instante em que a URL principal do
   processo é acessada. Por isso, todo processo marcado como **"Não
   Visualizado" é abortado** antes de qualquer abertura.
2. **Nunca inventar, prever ou fixar `infra_hash`.** Todo POST usa o hash lido
   do formulário da tela anterior (GET → extrair `hidden` → POST).

---

## Estrutura do projeto

| Caminho | Papel |
|---|---|
| `scripts_read/app.py` | Login mínimo (didático). |
| `scripts_read/app2.py` | Login + troca de unidade + raspagem com hash fixo (versão antiga). |
| `scripts_read/app3.py` | **Núcleo de LEITURA**: classe `APIExtratorSEI`, raspagem da caixa, geração do `.xlsx` e travas de segurança. |
| `app4_inclusao.py` | **Núcleo de ESCRITA**: inclui documento interno no processo (herda login/sessão do `app3`). |
| `teste_payload.py` | Diagnóstico: mostra o payload montado no `--dry-run` (não grava nada). |
| `api/api.py` | **FastAPI**: expõe a raspagem como tarefas em segundo plano + download do Excel. |
| `scripts_write/fix_app4_indent.py` | Ferramenta pontual: repara a indentação de `_extrair_hidden_inputs` se o arquivo for corrompido. |
| `scripts_write/valida_modelo_offline.py` | Teste **offline** do fluxo "Documento Modelo": monta o payload a partir do HTML salvo em `ruas_inclusao/` — **não autentica nem toca no SEI**. |
| `scripts_write/testa_mailmerge_offline.py` | Teste **offline** do Mail Merge: valida parsers, regras de mesclagem e contagens contra a fixture `4_editor_montar_FIXTURE.html` — **não toca no SEI**. |
| `teste_leitura_planilha.py` | Prova de conceito isolada: lê o Excel e substitui marcadores numa string HTML fictícia (sem SEI). |
| `.env` | Credenciais `SEI_USUARIO` / `SEI_SENHA` — **nunca versionado**. |
| `requirements.txt` | Dependências com versões fixadas (ambiente testado). |
| `outputs/` | Planilhas e HTML das execuções — **não versionado**. |
| `ruas_inclusao/` | Telas HTML de cada etapa do fluxo de escrita (`--salvar-telas`) — **não versionado**. |
| `log_auditoria.txt` | Auditoria das requisições de leitura. |
| `log_auditoria_escrita.txt` | Auditoria das requisições de escrita. |

---

## Requisitos e instalação

Python **3.13**. Ambiente de referência: `venv` do Windows.

```bash
python -m venv venv
venv\Scripts\activate                  # Windows
# source venv/bin/activate             # Linux/macOS

pip install -r requirements.txt
```

| Biblioteca | Versão | Uso |
|---|---|---|
| `requests` | 2.34.2 | Sessão HTTP, login, GET/POST |
| `beautifulsoup4` | 4.15.0 | Parsing do HTML das telas do SEI |
| `pandas` | 3.0.5 | DataFrame e filtros |
| `numpy` | 2.5.2 | Base do pandas |
| `openpyxl` | 3.1.5 | Geração dos `.xlsx` |
| `fastapi` / `uvicorn` / `pydantic` | 0.141.1 / 0.52.4 / 2.13.5 | API HTTP |
| `python-dotenv` / `python-multipart` | 1.2.3 / 0.0.32 | Apoio (multipart/Forms) |

---

## Configuração (`.env`)

Crie o arquivo na **raiz do projeto**:

```env
SEI_USUARIO=seu_login
SEI_SENHA=sua_senha
```

- O `scripts_read/app3.py` procura o `.env` em **três lugares**, nesta ordem:
  a pasta do próprio script, a **pasta pai (raiz do projeto)** e o diretório
  atual. Basta existir em um deles.
- Também é possível definir as variáveis no próprio ambiente do sistema.
- O `.env` está no `.gitignore` e **nunca** entra no versionamento.

---

## Leitura da caixa de processos (`scripts_read/app3.py`)

Autentica, troca de unidade e **raspa somente as telas de listagem** — sem abrir
nenhum processo.

```bash
venv\Scripts\python scripts_read\app3.py                        # filtro "1080", 5 paginas
venv\Scripts\python scripts_read\app3.py --todos                 # mantem todos os processos
venv\Scripts\python scripts_read\app3.py --offline pagina.html    # teste 100% offline
```

| Flag | Efeito |
|---|---|
| (nenhuma) | Raspa com o prefixo `1080` e gera `caixa_recebidos_fhemig_1080.xlsx` |
| `--todos` | Ignora o filtro e gera `caixa_recebidos_todos.xlsx` |
| `--offline [arquivo.html]` | Processa um HTML já salvo; **não toca no SEI** |

Constantes principais no topo do arquivo: `UNIDADE_ALVO` (padrão `110007141`),
`LIMITE_PAGINAS` (5), `PREFIXO_FILTRO` (`1080`) e `ARQUIVO_SAIDA`.

**Colunas extraídas da listagem:** `Processo`, `IdProtocolo`/`NumeroCheckbox`,
`Tipo`, `Especificacao`, `NaoVisualizado`, `NaoRecebido`, `AtribuidoA`,
`CpfAtribuido`, `Anotacoes`, `Marcadores`, `Sinalizadores` e `Pagina`.

**Travas de segurança (leitura):** só passam URLs que casam com
`PADROES_PERMITIDOS` (`login.php`, `acao=infra_trocar_unidade`,
`inicializar.php`, `acao=principal`, `acao=procedimento_controlar`). Qualquer
requisição que contenha um `TOKENS_PROIBIDOS` (`procedimento_trabalhar`,
`procedimento_visualizar`, `procedimento_abrir`, `documento_visualizar`,
`sair`, ...) é recusada. Tudo fica registrado em `log_auditoria.txt`.

---

## Inclusão de documento interno (`app4_inclusao.py`)

Cria um documento (Memorando, Relatório...) dentro de um processo que já está
acessível na caixa da unidade. O arquivo é **isolado** do núcleo de leitura:
`InclusaoDocumentoSEI` herda apenas `autenticar()` / `trocar_unidade()` e tem
**whitelist/blacklist próprias de escrita**, com auditoria em
`log_auditoria_escrita.txt`.

### Fluxo (4 passos)

1. **Escolher o tipo** — GET em `documento_escolher_tipo`, colhe os `hidden`
   inputs do formulário e faz POST com `hdnIdSerie` = id da série → **302**.
2. **Abrir a tela de geração** — segue o `Location` (`documento_gerar`) e
   localiza o formulário (`frmDocumentoCadastro`).
3. **Preencher e salvar** — monta o payload com **todos** os campos do
   formulário e sobrescreve apenas os de negócio: `rdoNivelAcesso` (1 =
   Restrito), `selHipoteseLegal`, `rdoTextoInicial`, `txtDataElaboracao` e
   `hdnFlagDocumentoCadastro`.
4. **Capturar o id** — a resposta é **302** e o `Location` traz
   `...&id_documento=NNNNNNNNN...`, extraído por regex e registrado no log.

Opcionalmente há um **passo 2b**: quando `--protocolo-modelo` é informado, a lupa
`documento_modelo_selecionar` é chamada em modo **leitura** (URL e `infra_hash`
lidos do **JS da própria tela**) para trazer o id interno do modelo — ver a
seção *Documento Modelo* abaixo.

### Uso

```bash
# 1) SIMULACAO - faz todo o fluxo, mostra o payload e NAO grava
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" --unidade 110031457 --serie 12 --dry-run --salvar-telas

# 2) GRAVACAO REAL - somente com --confirmar
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" --unidade 110031457 --serie 12 --hipotese-legal 4 --confirmar

# 3) DOCUMENTO MODELO - Cessao Especial Onus Cedente (serie 4110) tendo o
#    protocolo 148410511 como modelo; a simulacao mostra o payload montado
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" --unidade 110031457 --serie 4110 --protocolo-modelo 148410511 --dry-run --salvar-telas

# 4) Idem, gravando de verdade
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" --unidade 110031457 --serie 4110 --protocolo-modelo 148410511 --confirmar
```

| Flag | Padrão | Função |
|---|---|---|
| `--processo` | — | Número formatado do processo (busca automática na caixa) |
| `--id-procedimento` | — | Id interno do processo (use **um** dos dois) |
| `--unidade` | `110007141` | Id da unidade onde o processo está |
| `--serie` | `63` | Id da série/tipo de documento (`4110` = Cessão Especial Ônus Cedente) |
| `--hipotese-legal` | `4` | Hipótese legal do nível Restrito |
| `--texto-inicial` | `N` | `N` = branco, `T` = texto padrão, `D` = documento modelo (exige `--protocolo-modelo`) |
| `--protocolo-modelo` | — | Protocolo do documento usado como **Documento Modelo** (ex.: `148410511`); força `rdoTextoInicial=D` |
| `--id-documento-modelo` | — | Id interno do modelo (`hdnIdDocumentoTextoBase`), quando já conhecido |
| `--sem-consultar-modelo` | off | Não chama a lupa `documento_modelo_selecionar`; injeta só o protocolo no payload |
| `--dry-run` | off | Prepara e exibe o payload; **não grava** (devolve o payload montado) |
| `--salvar-telas` | off | Salva o HTML de cada etapa em `ruas_inclusao/` |
| `--confirmar` | off | **Obrigatório** para gravar de verdade |
| `--mesmo-assim` | off | Prossegue mesmo em processo **NÃO VISUALIZADO** (evite) |
| `--planilha [caminho]` | — | Excel de **Mail Merge**; sem valor usa `planilha exemplo cessao especial.xlsx` (raiz ou `~/Downloads`) |
| `--linha` | `0` | Índice da linha da planilha a mesclar |
| `--editar-documento ID` | — | Aplica o Mail Merge em um documento **já criado** (pula a criação) |
| `--lote` | off | Cria um documento **por linha** da planilha e mescla em cada um |
| `--a-partir-de N` | — | Com `--lote`: índice inicial (**retomada** após falha) |
| `--ate-linha N` | — | Com `--lote`: índice final do lote |

### Séries usadas na prática

| Documento | `--serie` |
|---|---|
| Relatório | `63` |
| Memorando | `12` |
| Memorando-Circular | `13` |
| Cessão Especial Ônus Cedente | `4110` |

Para descobrir outras séries: rode com `--salvar-telas` e leia
`ruas_inclusao/1_escolher_tipo_*.html` — cada tipo aparece como
`onclick="escolher(<id>)"` / `value="<id>"`.

### Documento Modelo (Cessão Especial Ônus Cedente — `--serie 4110`)

Em vez de nascer com o texto inicial em branco, o documento pode ser gerado a
partir de um **Documento Modelo** já existente no SEI. Basta informar o
protocolo do modelo:

```bash
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" --unidade 110031457 --serie 4110 --protocolo-modelo 148410511 --dry-run --salvar-telas
```

**O que muda no formulário** (só isso; o resto do payload continua igual):

| Campo | Valor enviado | Por quê |
|---|---|---|
| `rdoTextoInicial` | **`D`** | Opção "Documento Modelo" (`T` = Texto Padrão, `N` = Nenhum) |
| `txtProtocoloDocumentoTextoBase` | protocolo do modelo | É o campo da tela ("Documento Modelo" + protocolo) |
| `hdnIdDocumentoTextoBase` | `value` do radio da lupa **ou vazio** | `infraTableToHiddenText()` grava `box.value` (id do favorito) no hidden e `box.title` (protocolo) no campo visível; sem par `title`+`value` confiável, vai vazio |
| `txtTextoPadrao`, `hdnIdTextoPadrao` | vazio | `configurarTextoInicial()` limpa o "Texto Padrão" ao marcar o modelo |

Sequência executada:

1. `GET documento_gerar...&id_serie=4110` — a tela de cadastro do documento.
2. **Passo 2b** — `GET documento_modelo_selecionar&tipo_selecao=1&id_object=objLupaDocumentoTextoBase...`
   em modo **leitura**, com URL e `infra_hash` **lidos do JS da própria tela**
   (`new infraLupaText('txtProtocoloDocumentoTextoBase','hdnIdDocumentoTextoBase', ...)`).
   Se o HTML devolvido trouxer o radio `chkInfraItem` cujo `title` é o
   protocolo, o `value` dele vai para `hdnIdDocumentoTextoBase`; senão o
   protocolo segue sozinho (o backend do SEI resolve o modelo). Falha nessa
   lupa é **aviso, não erro**.
3. `POST documento_gerar` (hash do próprio formulário), com
   `hdnFlagDocumentoCadastro=2` e `rdoFormato=N`.

> `--sem-consultar-modelo` pula o passo 2b e injeta só o protocolo — útil se, na
> sua sessão, o GET da lupa interferir no hash da tela de geração.

Teste **offline** do payload (não autentica, não toca no SEI):

```bash
venv\Scripts\python scripts_write\valida_modelo_offline.py
```

### Mail Merge — mesclagem de dados da planilha (`--planilha`)

Cria o documento a partir do modelo e **substitui os marcadores de texto** pelos
dados do servidor lidos do Excel. A cadeia reproduz o clique manual no lápis
("Editar Conteúdo") do documento:

1. Árvore do processo (já visualizado na criação).
2. Link do **nó** do documento → árvore "com o documento selecionado" (é nessa
   variante que o SEI renderiza os links do editor).
3. `GET controlador_ajax.php?acao_ajax=documento_verificar_assinatura` (leitura).
4. `GET editor_montar` → **todos** os `textarea txaEditor_XXXXX` (cabeçalho,
   corpo, rodapé...), hidden inputs e a `action` do form.
5. Merge por `REGRAS_MERGE`, com contador por marcador impresso no console.
6. `GET ...acao_ajax=corfirmacao_atualizacao_documento` (confirmação do editor).
7. `POST editor/editor_processar.php?acao=editor_salvar` — sucesso validado pelo
   `OK<versão>` que o próprio editor do SEI espera no iframe.

```bash
# Documento único, mesclando a linha 0
venv\Scripts\python app4_inclusao.py --processo "0000.00.0000000/0000-00" \
  --unidade 110031457 --serie 4110 --protocolo-modelo 148410511 \
  --confirmar --planilha --linha 0

# Documento já existente (pula a criação)
... --confirmar --planilha --editar-documento 900000001

# Lote: um documento por linha da planilha
... --confirmar --planilha --lote

# Retomar após uma falha
... --confirmar --planilha --lote --a-partir-de 7
```

> **`--lote` é fail-fast**: para no primeiro erro e imprime a linha exata para
> retomar com `--a-partir-de`. Cada linha cria **um** documento novo — o fluxo
> não é idempotente, então revise o log antes de repetir.

Marcadores do modelo e a coluna da planilha (`REGRAS_MERGE`):

| Marcador no modelo | Coluna |
|---|---|
| `(NOME DO(A) SERVIDOR(A) CEDIDO(A))`, `(NOME DO SERVIDOR CEDIDO)`, `(NOME)`, `[NOME DO SERVIDOR]` | `Nome Servidor` |
| `(Nº)` após `MASP` | `MASP` |
| `(Nº)` após `ADM` | `ADM` |
| `(SIGLA DO CARGO)` | `Cargo` |
| `(FUNÇÃO)` | `Função` |
| `XX` (título do termo) | `Nº do termo` |
| `[DATA DO DIA DO PREENCHIMENTO]` | data de hoje |

> `MASP` e `ADM` usam o **mesmo** marcador `(Nº)` no modelo — por isso a regra é
> contextual (regex que ancora na palavra). Trocar o texto no modelo exige
> atualizar `REGRAS_MERGE`; o contador de cada regra avisa quando um marcador
> deixa de ser encontrado.

Teste **offline** do merge (não autentica, não toca no SEI):

```bash
venv\Scripts\python scripts_write\testa_mailmerge_offline.py
```

### Detalhes técnicos que o código já resolve

Estes pontos foram descobertos na engenharia reversa e são o motivo de o fluxo
funcionar — valem como documentação de manutenção:

- **`hdnFlagDocumentoCadastro` deve ir como `2`.** O HTML traz `value="1"`, mas
  o JavaScript do SEI (`submeter()`, disparado pelo botão Salvar) troca o valor
  para `2` imediatamente antes do submit. Enviando `1`, o servidor apenas
  **re-renderiza a tela (HTTP 200) sem criar o documento**.
- **A árvore do processo fica em `iframe#ifrArvore`** e, no SEI 5.x, os nós vêm
  dentro de strings JavaScript (`Nos[N].acoes = '<a href="...">'`), invisíveis
  ao parser DOM — por isso há um fallback por regex no HTML bruto.
- **Os `chkInfraItem*` da tela de escolha são removidos do payload**, senão o
  SEI interpreta como seleção múltipla.
- **A série viaja em `hdnIdSerie`** (o `selSerie` chega vazio).
- **O `Location` costuma vir com mojibake** (`ç` lido como `Ã§`); existe um
  saneamento `latin-1 → utf-8` aplicado só quando o conteúdo é realmente
  mojibake, evitando `UnicodeDecodeError` no `requests`.
- **O "Documento Modelo" viaja em `rdoTextoInicial=D`** +
  `txtProtocoloDocumentoTextoBase`. O `hdnIdDocumentoTextoBase` segue o framework
  (botão *Transportar* → `infraTransportarSelecao()` → `infraTableToHiddenText()`):
  **`txt = box.title`** (o protocolo) e **`hdn = box.value`** (o id do favorito na
  lista). O `id_documento=` dos links da coluna de ação é de visualizar e é
  **ignorado**; sem par `title`+`value` confiável o hidden vai **vazio**.
- **A lupa não recebe hash de outro lugar**: a URL de `documento_modelo_selecionar`
  é extraída do `<script>` da tela de geração, com o `infra_hash` **dela** — que é
  diferente do hash do formulário. Nada é previsto, fixado ou reaproveitado.

---


## API HTTP (`api/api.py`)

Expõe a raspagem do `app3.py` como **tarefas em segundo plano**: a raspagem roda
em uma thread e o cliente consulta o status e baixa o Excel.

```bash
venv\Scripts\python api\api.py
# documentacao interativa: http://127.0.0.1:8000/docs
```

| Método | Rota | Função |
|---|---|---|
| `GET` | `/api/health` | Status do serviço, tarefas ativas e unidade alvo |
| `GET` | `/api/tarefas` | Resumo das tarefas |
| `POST` | `/api/raspar` | Cria a tarefa (responde HTTP 202) |
| `GET` | `/api/tarefas/{id}` | Situação e dados da tarefa |
| `GET` | `/api/tarefas/{id}/excel` | Download do `.xlsx` (HTTP 409 se ainda não pronto) |

Corpo do `POST /api/raspar`:

```json
{ "filtro": "1080", "limite_paginas": 5, "atraso": 0.6 }
```

- `filtro`: prefixo do número a manter (`todos` ou vazio = tudo)
- `limite_paginas`: 1 a 20
- `atraso`: intervalo em segundos entre páginas (0 a 5)

> As travas de segurança do `app3.py` continuam ativas: a API **não abre
> processo nenhum**.

---

## Segurança, LGPD e o que nunca vai para o Git

O `.gitignore` foi blindado. Ficam **fora do versionamento**:

| Ignorado | Por quê |
|---|---|
| `.env` | Credenciais do SEI |
| `log_auditoria*.txt` | URLs com `infra_hash` e IDs de processos |
| `ruas_inclusao/`, `drawable/` e HTML capturados | Telas do SEI com hash de sessão |
| `outputs/`, `*.xlsx` | Planilhas com dados de processos e de pessoas |
| `payload_gerar.json` | Dump do formulário do SEI |

Práticas embutidas no código:

- `--confirmar` é obrigatório para qualquer gravação real; `--dry-run` nunca grava.
- Processo **Não Visualizado** aborta o fluxo (proteção de prazo).
- Toda requisição de escrita é auditada em `log_auditoria_escrita.txt`.
- Nenhum `infra_hash` é escrito no código: ele sempre vem da tela anterior.

---

## Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `RuntimeError: Credenciais do SEI não encontradas` | `.env` ausente | crie o `.env` na raiz com `SEI_USUARIO` / `SEI_SENHA` |
| `Falha no login` | senha ou rede | valide o acesso pelo navegador |
| `Processo ... nao encontrado na caixa da unidade` | unidade errada ou processo fora da caixa | confira o `--unidade` (SEI → Trocar Unidade) |
| `ABORTADO: processo NAO VISUALIZADO` | proteção de prazo | use `--mesmo-assim` apenas com certeza absoluta |
| `documento_gerar nao redirecionou apos criar (HTTP 200)` | validação do SEI | confira `hdnFlagDocumentoCadastro=2` no `--dry-run` e veja `ruas_inclusao/3_erro_gerar_*.html` |
| `Aviso: lupa nao devolveu o id do modelo` | layout do popup mudou ou o modelo não está na lista | não é erro: o protocolo continua indo no POST; `--sem-consultar-modelo` silencia o aviso |
| `--protocolo-modelo` + `--texto-inicial`| regra do radio `D` | remova `--texto-inicial` (o modelo sempre força `D`) |
| `Tela de escolha de tipo nao expoe o formulario` | sem permissão no processo | verifique a permissão do usuário na unidade |
| `Operação de ESCRITA cancelada` | faltou `--confirmar` | use `--confirmar` (ou `--dry-run`) |
| `IndentationError` em `app4_inclusao.py` | arquivo corrompido em edição | `venv\Scripts\python scripts_write\fix_app4_indent.py` |

Dica: rode sempre **da raiz do projeto** — os caminhos de log e de saída são
relativos.

---

## Observações

- `app.py` e `app2.py` são versões didáticas/históricas; a referência atual de
  leitura é o `app3.py`.
- O `app2.py` usa `infra_hash` **fixo** (só para estudo) — o `app3.py` passou a
  usar hash dinâmico justamente por isso.
- Rodar `app4_inclusao.py --confirmar` duas vezes cria **um novo documento a
  cada execução** (o fluxo não é idempotente).
- Documentos criados saem como **Nato-digital** (`rdoFormato=N`), com o nível de
  acesso conforme a hipótese legal informada e sem texto (`--texto-inicial N`) —
  ou a partir de um **Documento Modelo**, quando `--protocolo-modelo` é usado.

