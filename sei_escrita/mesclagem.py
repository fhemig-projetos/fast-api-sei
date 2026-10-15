# -*- coding: utf-8 -*-
"""
O preenchedor do modelo (mail merge).
"""

import re
from datetime import datetime

from html import unescape as _unescape_entidades

from .configuracao import COLUNAS_NUMERICAS, REGRAS_MERGE
from .utilitarios import _sem_decimal_de_planilha

class MescladorDocumentos:
    """Aplica as REGRAS_MERGE no HTML do modelo (mail merge)."""

    @staticmethod
    def _mesclar_html(html_documento, dados):
        """
        Aplica as REGRAS_MERGE no HTML do documento e devolve (html, contagem).

        Todas as regras sao lidas de REGRAS_MERGE - nenhum valor do servidor
        fica fixado no codigo. Para o tratamento "texto" a coluna recebe acento
        (MASP, ADM, termo) porque esses marcadores vivem dentro de <strong> onde
        o CKEditor espera o numero puro. O contador por regra permite auditar a
        mesclagem no console (e detectar modelo que mudou).
        """
        contagem = {}
        resultado = html_documento
        for descricao, coluna, padrao, grupo, tratamento in REGRAS_MERGE:
            if tratamento == "data":
                valor = datetime.now().strftime("%d/%m/%Y")
            else:
                bruto = str(dados.get(coluna, "") or "").strip()
                if tratamento == "texto" and coluna in COLUNAS_NUMERICAS:
                    valor = _sem_decimal_de_planilha(bruto)
                else:
                    valor = _unescape_entidades(bruto)
            if not valor:
                contagem[descricao] = 0
                continue

            def _substituir(match, _valor=valor, _grupo=grupo):
                if _grupo <= 0:
                    return _valor
                texto = list(match.group(0))
                inicio, fim = match.span(_grupo)
                return (
                    "".join(texto[: inicio - match.start()])
                    + _valor
                    + "".join(texto[fim - match.start() :])
                )

            resultado, qtde = re.subn(padrao, _substituir, resultado)
            contagem[descricao] = qtde
        return resultado, contagem

