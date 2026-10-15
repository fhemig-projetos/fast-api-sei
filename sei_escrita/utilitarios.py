# -*- coding: utf-8 -*-
"""
Funcoes pequenas e puras de texto e de titulo de coluna.
"""

import re
import unicodedata

def _sem_decimal_de_planilha(valor):
    """
    "1234.0" -> "1234". Preserva os zeros à esquerda ("00000000" continua
    "00000000"), que int() perderia.

    Só remove a casa decimal quando ela é toda zero. Um valor com parte
    fracionária de verdade ("1234.5") é devolvido intacto em vez de ser
    arredondado em silêncio: número de MASP não tem fração, e arredondar
    dado de servidor seria pior do que mostrar o que veio.
    """
    texto = str(valor or "").strip()
    if not texto:
        return ""
    inteiro = re.fullmatch(r"(\d+)[.,]0+", texto)
    return inteiro.group(1) if inteiro else texto


def _chave_coluna(nome):
    """
    Reduz o titulo de uma coluna a uma forma comparavel.

    "Nº do termo", "N° do termo", "No do termo" e "n_o_termo" viram a mesma
    chave. E o que permite ler a planilha de outra unidade sem renomear nada:
    o grafismo do titulo depende do teclado de quem montou a planilha, e antes
    bastava um acento a menos para o programa recusar o arquivo inteiro.
    """
    texto = str(nome or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("\u00ba", " ").replace("\u00b0", " ")
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


