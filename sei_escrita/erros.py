# -*- coding: utf-8 -*-
"""
As excecoes do fluxo de escrita.
"""

class LinhaSemProcesso(ValueError):
    """
    A linha da planilha nao tem numero de processo em 'Processo SEI'.

    Herda de ValueError para nao mudar quem ja tratava o erro antigo, mas e
    uma CATEGORIA propria: nao e falha, e ausencia de trabalho. Nao ha processo
    onde incluir documento para essa pessoa, entao nada e tentado nela.

    Antes, essa linha virava falha comum e ABORTAVA o lote inteiro. Numa
    planilha real (868 linhas, 229 sem processo) isso tornava o lote
    impossivel de rodar. Erros de verdade - processo nao encontrado, processo
    NAO VISUALIZADO - continuam abortando tudo como antes.
    """


class ErroInclusaoDocumento(Exception):
    """Exceção descritiva do fluxo de inclusão de documento no SEI."""

    def __init__(self, mensagem, etapa=None):
        self.etapa = etapa or "geral"
        super().__init__(f"[{self.etapa}] {mensagem}")


