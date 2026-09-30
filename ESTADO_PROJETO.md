# ESTADO DO PROJETO — onde paramos

Data: 28/09 — ultimo commit no GitHub: `72d340e`
Arquivo com mudancas NAO commitadas: `interface_app.py`

## O que o programa faz

Automatiza a criacao de documentos no SEI-MG (Cessao Especial Onus Cedente)
a partir de uma planilha Excel: cria o documento, faz o mail merge e inclui
nos blocos de assinatura.

## Como rodar

```bash
venv/Scripts/python.exe interface_app.py     # testar em Python
build_exe.bat                                 # gerar dist/AutomacaoSEI.exe
```

## O que precisa preencher na tela

| Campo | Valor |
|---|---|
| Usuario do SEI | o login do operador |
| Senha do SEI | a senha (mascarada, pedida a cada uso) |
| Unidade | **preenche sozinha** ao sair do campo senha |
| Tipo | `4110` (numero do tipo de documento) |
| Modelo | `148410511` (protocolo do Documento Modelo) |
| Planilha | arquivo .xlsx |
| Bloco 1 / Bloco 2 | IDs dos blocos, ou vazio para nao incluir |

Botoes de bloco (escolher UM):
- "Incluir documento" -> entra no bloco, nao exige assinante
- "Incluir e disponibilizar" -> envia para assinatura (exige unidades no bloco)
- "Nao incluir em bloco" -> cria e mescla, sem entrar em bloco

## Decisoes tomadas (nao mexer sem o usuario pedir)

- **Senha pedida a cada uso**, nada gravado no PC. O `.exe` nao leva usuario
  nem senha (verificado nos bytes do binario).
- **Nao grava status na planilha**: o programa so LE a planilha.
- **Execucao sequencial**, nunca assincrona: o SEI nao aceita sessao concorrente.
- **Logs em `%APPDATA%\AutomacaoSEI\logs`**, fora do projeto e do `.exe`.
- `SIMULAR = False` (grava de verdade). Com `True` o pre-voo roda sem gravar,
  mas nao exercita o bloco.
- **O `.exe` NAO entra no Git** (37 MB). Se distribui por fora.

## Regras do SEI que o codigo respeita (nao quebrar)

- Todo `infra_hash` vem da tela. Nunca fixar, nunca prever.
- `hdnFlagDocumentoCadastro` sempre "2"; `rdoFormato` sempre "N".
- O checkbox do documento e `chkDocumentosItem<N>`, onde N e a POSICAO na
  tabela e muda a cada inclusao. O id_documento vem no `value`.
- A tela de bloco (`acao=bloco_escolher`) tem dois POSTs: o primeiro imita o
  `onchange` do `<select>`, o segundo confirma.
- O SEI responde **HTTP 200 mesmo quando recusa**: o aviso vem em `div.alert`.
  Nunca tratar 200 como sucesso sem ler os alertas.
- O `.exe` embute pandas/customtkinter/openpyxl; `scripts_write/` (testes)
  NAO e empacotado.

## Feito hoje

1. Login e senha na tela, LGPD
2. Inclusao em **dois blocos** de assinatura
3. Botao "Nao incluir em bloco"
4. Unidade detectada no login + Tipo e Modelo (vazios, obrigatorios)
5. Troca de unidade agora e **verificada** (antes assumia sucesso)
6. Validacao so ao clicar Iniciar
7. Tela com **rolagem**, botao Iniciar fixo no rodape
8. Bug: o bloco de assinatura cobria o botao de escolher a planilha

## Busca do processo por numero (novo)

Cada linha da planilha traz um numero de processo; o programa precisa achar esse
processo para abrir e alterar os documentos dele. A localizacao usa **duas
fontes, nesta ordem**:

1. **Pesquisa rapida** do topo do SEI (`acao=protocolo_pesquisa_rapida`) — acha
   pelo numero, sem limite de paginas.
2. **Caixa de "Recebidos"** — varre ate 20 paginas (comportamento antigo,
   mantido como reserva).

Isso resolve um limite real: a caixa mostra so a **pagina 1** na hora de abrir o
processo, entao um lote com processos antigos era localizado e mesmo assim
falhava ao abrir. Agora, quando a caixa nao traz o link, a pesquisa rapida
entrega um link com `infra_hash` fresco.

**Divisao de papel (importante para a seguranca):** a pesquisa *descobre*, a
caixa *confirma*. So aceitamos um resultado da pesquisa quando ele traz a marca
`processoVisualizado` / `processoNaoVisualizado`. Sem marca, caimos na caixa —
porque `bool(None)` e `False`, e "nao sei" jamais pode virar "pode abrir"
(regra de ouro nº 1).

Teste offline: `venv/Scripts/python scripts_write/testa_busca_offline.py`
(24 verificacoes, sem rede).

## Testes offline (rodar depois de mexer)

```bash
venv/Scripts/python scripts_write/testa_interface_gui.py
venv/Scripts/python scripts_write/valida_modelo_offline.py
venv/Scripts/python scripts_write/testa_mailmerge_offline.py
venv/Scripts/python scripts_write/testa_busca_offline.py
```

## Pendencias

1. **`.exe` desatualizado** (28/09 23:21): nao tem a rolagem nem o rodape fixo.
   Gerar com `build_exe.bat` DEPOIS de testar no Python.
2. **`interface_app.py` sem commit** (rolagem + rodape fixo).
3. **Janela pode passar da tela**: pede ~1055px, monitor tem 864px. Se a barra
   de titulo sumir, e isso. A janela e mexivel, entao da para arrastar.
4. `HIPOTESE_LEGAL = "4"` (Restrito) continua fixo — se outra unidade usar outro
   nivel, ajustar.
5. Refatoracao SOLID pendente (`planilha.py`, `mailmerge.py`,
   `lote_controller.py`). Ficou para outro dia. As tres suites offline passam,
   entao ha rede de seguranca.
6. `credenciais_sei.py` ficou sem uso pela interface (a senha passou a ser pedida
   a cada uso). Nao removido.

## Como o build deve ser conferido

- usuario e senha ausentes do binario
- backend e bibliotecas presentes
- `dist/` fora do Git
