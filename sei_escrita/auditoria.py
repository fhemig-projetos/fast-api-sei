# -*- coding: utf-8 -*-
"""
O caderno de anotacoes: log de auditoria e copias das telas.
"""

import os
import time

from .configuracao import PASTA_TELAS

class AuditoriaEscrita:
    """Log de auditoria e copias das telas (ruas_inclusao/)."""

    ARQUIVO_AUDITORIA_ESCRITA = "log_auditoria_escrita.txt"

    @classmethod
    def _registrar_auditoria_escrita(cls, linha):
        try:
            with open(cls.ARQUIVO_AUDITORIA_ESCRITA, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {linha}\n")
        except OSError:
            pass

    def _salvar_tela(self, nome_etapa, resposta, forcar=False):
        """
        Grava o HTML da etapa em ruas_inclusao/ (ver --salvar-telas). O dump de erro
        usa forcar=True para sair sempre, como pede o diagnostico do README.
        """
        if not (self.salvar_telas or forcar):
            return
        try:
            os.makedirs(PASTA_TELAS, exist_ok=True)
            caminho = os.path.join(PASTA_TELAS, f"{nome_etapa}_{int(time.time())}.html")
            with open(caminho, "wb") as fh:
                fh.write(getattr(resposta, "content", str(resposta).encode("utf-8")))
            print(f"[telas] {nome_etapa} -> {caminho}")
        except OSError as exc:
            print(f"[telas] aviso: não foi possível salvar {nome_etapa}: {exc}")

