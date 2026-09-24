# -*- coding: utf-8 -*-
"""
Prova de conceito de MAIL MERGE offline (sem tocar no SEI).

Objetivo desta iteracao: validar localmente a logica de mesclagem que, no
proximo passo, sera aplicada ao documento "CESSAO ESPECIAL ONUS CEDENTE"
criado pelo app4_inclusao.py. Aqui o script:

  1. le a planilha `planilha exemplo cessao especial.xlsx` com pandas;
  2. seleciona APENAS a primeira linha de dados (indice 0);
  3. substitui marcadores de texto ([NOME_SERVIDOR], [MASP_SERVIDOR],
     [CARGO_SERVIDOR], [FUNCAO_SERVIDOR]) numa string HTML ficticia que
     simula o conteudo do editor de texto do SEI;
  4. imprime o HTML final e verifica se sobrou algum marcador por substituir.

REGRAS DE OURO (README.md): este script NAO importa requests/app3/app4, NAO
faz requisicao alguma e NAO lida com infra_hash - as travas de seguranca do
fluxo de escrita ficam intactas por construcao. Totalmente offline.

Uso:
    venv/Scripts/python teste_leitura_planilha.py [caminho_da_planilha.xlsx]
"""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# Consoles Windows podem nao suportar acentos; garante UTF-8 na saida.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # pragma: no cover

import pandas as pd  # noqa: E402

NOME_PLANILHA = "planilha exemplo cessao especial.xlsx"
# Fallback: enquanto o arquivo nao for copiado para a raiz do projeto.
DOWNLOADS_PLANILHA = os.path.join(
    os.path.expanduser("~"), "Downloads", NOME_PLANILHA
)

# Marcador (como aparece no HTML do SEI) -> coluna da planilha.
MAPEAMENTO = {
    "[NOME_SERVIDOR]": "Nome Servidor",
    "[MASP_SERVIDOR]": "MASP",
    "[CARGO_SERVIDOR]": "Cargo",
    "[FUNCAO_SERVIDOR]": "Função",
}

# HTML ficticio no estilo do editor de texto do SEI, com os marcadores
# dentro de <span> (exatamente como ficariam no modelo real).
HTML_FICTICIO = (
    "<p>O servidor <span style=\"color:red\">[NOME_SERVIDOR]</span>, "
    "MASP <span style=\"color:red\">[MASP_SERVIDOR]</span>, "
    "ocupante do cargo de <span style=\"color:red\">[CARGO_SERVIDOR]</span>, "
    "na condicao de <span style=\"color:red\">[FUNCAO_SERVIDOR]</span>, "
    "fica cedida os direitos e vantagens do termo, "
    "nos termos da legislacao aplicavel.</p>"
)


def resolver_caminho_planilha(argv):
    """Resolve o caminho da planilha: argv[1] -> raiz do projeto -> Downloads."""
    candidatos = []
    if len(argv) > 1:
        candidatos.append(argv[1])
    candidatos.append(os.path.join(RAIZ, NOME_PLANILHA))
    candidatos.append(DOWNLOADS_PLANILHA)

    for caminho in candidatos:
        if os.path.isfile(caminho):
            return caminho
    sys.exit(
        "Planilha nao encontrada. Busquei, nesta ordem:\n"
        + "\n".join(f"  - {c}" for c in candidatos)
        + "\nInforme o caminho: venv/Scripts/python teste_leitura_planilha.py <caminho.xlsx>"
    )


def texto(valor):
    """Converte um valor de celula em texto limpo (NaN -> vazio)."""
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def carregar_primeira_linha(caminho):
    """Le a planilha e devolve a linha de indice 0 como dict {coluna: texto}."""
    df = pd.read_excel(caminho)
    print(f"Planilha lida: {caminho}")
    print(f"Colunas encontradas: {list(df.columns)}")
    print(f"Total de linhas de dados: {len(df)}")

    faltantes = [c for c in MAPEAMENTO.values() if c not in df.columns]
    if faltantes:
        sys.exit(f"ABORTADO: colunas obrigatorias ausentes na planilha: {faltantes}")
    if df.empty:
        sys.exit("ABORTADO: a planilha nao contem linhas de dados.")

    primeira = df.iloc[0]
    return {coluna: texto(primeira[coluna]) for coluna in df.columns}


def mesclar(html, dados):
    """Substitui cada marcador do dicionario MAPEAMENTO pelo valor correspondente."""
    resultado = html
    for marcador, coluna in MAPEAMENTO.items():
        resultado = resultado.replace(marcador, dados.get(coluna, ""))
    return resultado


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    caminho = resolver_caminho_planilha(argv)
    dados = carregar_primeira_linha(caminho)

    print("\n--- DADOS DA PRIMEIRA LINHA (indice 0) ---")
    for coluna, valor in dados.items():
        print(f"  {coluna} = {valor}")

    print("\n--- MAPPING APLICADO ---")
    for marcador, coluna in MAPEAMENTO.items():
        print(f"  {marcador} -> {coluna} = {dados.get(coluna, '')}")

    html_final = mesclar(HTML_FICTICIO, dados)

    print("\n--- HTML RESULTANTE ---")
    print(html_final)

    # Validacao automatica: nenhum marcador [MAIUSCULO_...] deve sobrar.
    sobrando = re.findall(r"\[[A-Z_]+\]", html_final)
    print("\n--- VALIDACAO ---")
    if sobrando:
        print(f"FALHA: marcadores nao substituidos: {sorted(set(sobrando))}")
        sys.exit(1)
    print("SUCESSO: todos os marcadores foram substituidos.")


if __name__ == "__main__":
    main()