# -*- coding: utf-8 -*-
"""
Tabelas de referencia do fluxo de escrita (constantes puras).
"""

HOST_SEI = "https://www.sei.mg.gov.br"
PASTA_TELAS = "ruas_inclusao"

REDIRECIONAMENTOS = (301, 302, 303, 307, 308)

# Serie do documento "CESSÃO ESPECIAL ÔNUS CEDENTE" no SEI-MG (id_serie do tipo). Fica
# documentada aqui porque e o caso de uso do fluxo com "Documento Modelo"; continua
# sendo o CLI/API quem informa a serie (--serie 4110) - nada fica fixado no payload.
SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE = "4110"

# Planilha de mesclagem (Mail Merge) da Cessao Especial: nome do arquivo padrao e
# mapeamento marcador -> coluna. O arquivo padrao e procurado na raiz do projeto e,
# como fallback, em ~/Downloads (nunca versionado: *.xlsx esta no .gitignore).
NOME_PLANILHA_PADRAO = "planilha exemplo cessao especial.xlsx"

# Colunas da planilha usadas na mesclagem.
COLUNAS_MERGE = ("Nome Servidor", "MASP", "ADM", "Cargo", "Função", "Nº do termo")

# Os marcadores do Documento Modelo e a coluna correspondente estao descritos
# em REGRAS_MERGE (com regex, porque varios deles dependem do contexto: o mesmo
# "(Nº)" aparece para MASP e para ADM). As entidades do SEI (&ordm; para o
# ordinal, &Ccedil; para o cedilha) aparecem no HTML DESESCAPADO, por isso os
# padroes aceitam as duas formas.

# (descricao, coluna, regex, grupo_substituido, tratamento)
# grupo_substituido = 0 substitui o grupo inteiro; >0 substitui so aquele grupo.
# O HTML que o SEI guarda DENTRO do <textarea> vem escapado: o "&" vira
# "&amp;", o "<" vira "&lt;" e os atributos saem como "&quot;". As tres
# regras de marcador (MASP, ADM e o N do titulo) foram escritas para a forma
# NAO escapada e por isso nunca casaram com o documento real: o contador
# ficava em zero e o servidor era salvo com "(No)" e "XX" originais.
# Estas expressoes aceitam as DUAS formas, porque o CKEditor grava de um jeito
# ou de outro conforme a versao do SEI.
_TAG_ABRE = r"(?:<|&lt;)"
_TAG_FECHA = r"(?:>|&gt;)"
_ATRIBUTOS = r"[^>]*"
_ORDINAL = r"(?:&(?:amp;)?ordm;|º)"
_ESPACO_NAO_QUEBRAVEL = r"(?:&nbsp;|&amp;nbsp;)"
_TAG_SPAN = _TAG_ABRE + r"span" + _ATRIBUTOS + r"\s*" + _TAG_FECHA
_TAG_STRONG_ABRE = _TAG_ABRE + r"strong" + _ATRIBUTOS + r"\s*" + _TAG_FECHA


# Regras que TEM de casar: se uma delas devolver zero, o documento saiu com o
# marcador original e precisa ser barrado. Vale mais que procurar texto no
# corpo, porque o marcador pode ter grafia diferente da esperada.
REGRAS_OBRIGATORIAS = ("MASP", "Nº do termo (titulo)")

# Colunas que sao identificadores numéricos inteiros. O pandas devolve float
# quando a coluna do Excel tem alguma célula vazia: o MASP 1234 chega como
# "1234.0" e ia para dentro do documento com o ".0" no meio do número.
COLUNAS_NUMERICAS = ("MASP", "ADM", "Nº do termo")


REGRAS_MERGE = (
    (
        "MASP",
        "MASP",
        rf"(MASP\s*{_ESPACO_NAO_QUEBRAVEL}?\s*(?:{_TAG_SPAN}\s*)?{_TAG_STRONG_ABRE}\s*)"
        rf"(\(\s*N\s*{_ORDINAL}\s*\))",
        2,
        "texto",
    ),
    (
        "ADM",
        "ADM",
        rf"(ADM\s*{_ESPACO_NAO_QUEBRAVEL}?\s*(?:{_TAG_SPAN}\s*)?{_TAG_STRONG_ABRE}\s*)"
        rf"(\(\s*N\s*{_ORDINAL}\s*\))",
        2,
        "texto",
    ),
    (
        "Nº do termo (titulo)",
        "Nº do termo",
        rf"(N\s*{_ORDINAL}\s*{_TAG_SPAN}\s*)(XX)(\s*{_TAG_ABRE}/span\s*{_TAG_FECHA})",
        2,
        "texto",
    ),
    (
        "Nº do termo (titulo sem span)",
        "Nº do termo",
        rf"(N\s*{_ORDINAL}\s+)(XX)(?=\s*(?:&nbsp;|&amp;nbsp;|/|&amp;))",
        2,
        "texto",
    ),
    (
        "Nome do servidor (cedente)",
        "Nome Servidor",
        r"\(NOME DO\(A\) SERVIDOR\(A\) CEDIDO\(A\)\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (cedido)",
        "Nome Servidor",
        r"\(NOME DO SERVIDOR CEDIDO\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (clausula)",
        "Nome Servidor",
        r"\(NOME\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (assinatura)",
        "Nome Servidor",
        r"\[NOME DO SERVIDOR\]",
        0,
        "texto",
    ),
    (
        "Cargo (sigla)",
        "Cargo",
        r"\(SIGLA DO CARGO\)",
        0,
        "texto",
    ),
    (
        "Funcao do servidor",
        "Função",
        r"\(FUN\s*(?:ÇÃO|&Ccedil;&Atilde;O)\)",
        0,
        "texto",
    ),
    (
        "Data do preenchimento",
        None,
        r"\[DATA DO DIA DO PREENCHIMENTO\]",
        0,
        "data",
    ),
)

# Marcadores que o modelo usa hoje; qualquer um que ainda exista no HTML final
# significa merge incompleto (checa de forma barata, sem regex).
MARCADORES_DO_MODELO = (
    "(NOME DO(A) SERVIDOR(A) CEDIDO(A))",
    "(NOME DO SERVIDOR CEDIDO)",
    "(SIGLA DO CARGO)",
    "[NOME DO SERVIDOR]",
    "[DATA DO DIA DO PREENCHIMENTO]",
    # Estes tres faltavam. Sem eles na trava, uma regra de merge que nao casasse
    # passava BATIDA: o contador virava zero, nenhum aviso era dado e o
    # documento era GRAVADO com "XX" e "(No)" no lugar dos numeros. E o que
    # apareceu nos primeiros documentos da unidade 110031718.
    # Forma real devolvida pelo CKEditor no modelo 110031718: "(N&ordm;)",
    # sem espaco. Com "(N &ordm;)" a trava nao via a occurrences.
    "(N&ordm;)",
    "(N &ordm;)",
    "(Nº)",
)

CAMPOS_RESUMO_PROCESSO = (
    "Processo",
    "IdProtocolo",
    "Tipo",
    "Especificacao",
    "NaoVisualizado",
    "NaoRecebido",
    "AtribuidoA",
    "Interessados",
)


# Aceita varias grafias por coluna canonica. A primeira que existir na planilha
# ganha; as demais sao apenas apelidos tambem aceitos. "Processo SEI" nao entra
# no merge - serve so para localizar o processo de cada linha.
APELIDOS_COLUNAS = {
    "Nome Servidor": ("nome servidor", "nome", "servidor", "nome do servidor",
                      "servico", "cedente"),
    "MASP": ("masp", "matricula", "siap", "siape"),
    "ADM": ("adm", "codigo adm"),
    "Cargo": ("cargo", "sigla cargo"),
    "Função": ("funcao", "funcao servidor", "descricao cargo"),
    "Nº do termo": ("n do termo", "n termo", "numero do termo", "n_termo",
                    # "n_o_termo"/"n o termo" sao o que o Excel costuma deixar
                    # quando o cabecalho foi digitado com o "º" quebrado em
                    # "o" + separador. A docstring de _chave_coluna ja prometia
                    # essa equivalencia, mas a chave gerada ("n_o_termo")
                    # nao batia com nenhuma das anteriores ("n_do_termo",
                    # "n_termo"): a planilha era recusada sem motivo aparente.
                    "n_o_termo", "n o termo", "nº termo", "nº do termo",
                    # Planilha de outra unidade (CONTROLE DAS MOVIMENTACOES
                    # HRJP) traz o cabecalho so como "Nº".
                    "nº", "no",
                    "termo"),
    "Processo SEI": ("processo sei", "processo", "n processo", "numero processo",
                     "sei"),
}


