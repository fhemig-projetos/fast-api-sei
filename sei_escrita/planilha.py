# -*- coding: utf-8 -*-
"""
A leitora da planilha.
"""

import os
import sys

import pandas as pd

_root_ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from .configuracao import APELIDOS_COLUNAS, COLUNAS_MERGE, COLUNAS_NUMERICAS, NOME_PLANILHA_PADRAO
from .utilitarios import _chave_coluna, _sem_decimal_de_planilha

def resolver_caminho_planilha(valor):
    """
    Resolve o caminho do Excel de mesclagem: valor informado em --planilha
    (relativo a raiz do projeto) -> nome padrao na raiz -> ~/Downloads (fallback).
    """
    if valor:
        caminho = valor if os.path.isabs(valor) else os.path.join(_root_, valor)
        if os.path.isfile(caminho):
            return caminho
        if os.path.basename(valor) != NOME_PLANILHA_PADRAO:
            sys.exit(f"Planilha nao encontrada: {caminho}")
    for candidato in (
        os.path.join(_root_, NOME_PLANILHA_PADRAO),
        os.path.join(os.path.expanduser("~"), "Downloads", NOME_PLANILHA_PADRAO),
    ):
        if os.path.isfile(candidato):
            return candidato
    sys.exit(
        f"Planilha '{NOME_PLANILHA_PADRAO}' nao encontrada na raiz do projeto nem em "
        "~/Downloads (ou informe outro caminho em --planilha)."
    )


def carregar_dados_planilha(caminho, indice=0):
    """
    Le o Excel e devolve {coluna: valor} da linha pedida (ver COLUNAS_MERGE).
    Aborta com sys.exit em coluna ausente, planilha vazia ou indice fora do
    intervalo.

    Passa pela MESMA normalizacao de nomes do fluxo em lote (_renomear_colunas).
    Antes esta funcao exigia o titulo exato ("Nº do termo") enquanto a outra
    aceitava apelidos: dois modos de ler a mesma planilha no mesmo arquivo, e o
    caminho de processo unico recusava arquivos que o lote aceitava.
    """
    bruta = pd.read_excel(caminho)
    df = _renomear_colunas(bruta)
    faltantes = sorted({c for c in COLUNAS_MERGE if c not in df.columns})
    if faltantes:
        sys.exit(
            f"Colunas obrigatorias ausentes em {os.path.basename(caminho)}: {faltantes} "
            f"(colunas lidas: {list(bruta.columns)}).\n"
            "Aceitas, por coluna:\n  "
            + "\n  ".join(f"{c} -> {list(APELIDOS_COLUNAS[c])}" for c in faltantes)
        )
    if df.empty:
        sys.exit(f"Planilha sem linhas de dados: {caminho}")
    if not isinstance(indice, int) or indice < 0 or indice >= len(df):
        sys.exit(f"--linha {indice} fora do intervalo valido (0..{len(df) - 1}).")
    linha = df.iloc[indice]
    dados = {
        coluna: ("" if pd.isna(linha[coluna]) else str(linha[coluna]).strip())
        for coluna in COLUNAS_MERGE
    }
    for coluna in COLUNAS_NUMERICAS:
        if coluna in dados:
            dados[coluna] = _sem_decimal_de_planilha(dados[coluna])
    return dados


def _renomear_colunas(df):
    """Devolve uma copia do DataFrame com as colunas no nome canonico."""
    disponiveis = {}
    for coluna in df.columns:
        disponiveis.setdefault(_chave_coluna(coluna), coluna)

    renomear = {}
    for canonica, apelidos in APELIDOS_COLUNAS.items():
        for apelido in (canonica,) + apelidos:
            achada = disponiveis.get(_chave_coluna(apelido))
            if achada is not None:
                renomear[achada] = canonica
                break
    return df.rename(columns=renomear)


def carregar_planilha(caminho):
    """
    Le a planilha inteira e devolve a lista de linhas (uma por servidor).

    O nome das colunas nao e mais obrigatorio ser escrito de uma unica forma.
    Cada coluna canonica (COLUNAS_MERGE) e procurada por um conjunto de
    apelidos, e a busca ignora maiusculas, acentos, "_" e espacos. Assim a
    planilha de outra unidade abre com "NOME DO SERVIDOR", "Funcao" ou
    "n_o_termo" sem ninguem precisar editar o codigo.
    """
    bruta = pd.read_excel(caminho)
    df = _renomear_colunas(bruta)
    faltantes = sorted({c for c in COLUNAS_MERGE if c not in df.columns})
    if faltantes:
        sys.exit(
            f"Colunas obrigatorias ausentes em {os.path.basename(caminho)}: {faltantes} "
            f"(colunas lidas: {list(bruta.columns)}).\n"
            "Aceitas, por coluna:\n  "
            + "\n  ".join(f"{c} -> {list(APELIDOS_COLUNAS[c])}" for c in faltantes)
        )
    if df.empty:
        sys.exit(f"Planilha sem linhas de dados: {caminho}")
    linhas = []
    for indice, linha in df.iterrows():
        dados = {
            coluna: ("" if pd.isna(linha[coluna]) else str(linha[coluna]).strip())
            for coluna in COLUNAS_MERGE
        }
        for coluna in COLUNAS_NUMERICAS:
            if coluna in dados:
                dados[coluna] = _sem_decimal_de_planilha(dados[coluna])
        processo = ""
        if "Processo SEI" in df.columns and not pd.isna(linha["Processo SEI"]):
            processo = str(linha["Processo SEI"]).strip()
        linhas.append({"indice": int(indice), "dados": dados, "processo": processo})
    return linhas


def _processo_marca_nao_visualizado(achado):
    """
    Regra de ouro nº 1 (unica fonte da verdade): o item da caixa esta marcado
    como "Nao Visualizado"? Abrir a URL principal de um processo assim inicia a
    contagem de prazo no servidor - por isso abortamos ANTES de abrir.
    """
    if not achado:
        return False
    valor = achado.get("NaoVisualizado")
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "sim", "verdadeiro", "s")
    return bool(valor)


