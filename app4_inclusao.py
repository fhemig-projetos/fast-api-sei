# -*- coding: utf-8 -*-
"""
Nucleo de ESCRITA do SEI-MG: inclusao de documento interno em processo existente.

Complementa o nucleo de leitura (scripts_read/app3.py), do qual herda apenas
autenticar()/trocar_unidade() e a raspagem da caixa. Toda requisicao deste fluxo
passa por _ir(), que aplica whitelist/blacklist proprias de ESCRITA e audita em
log_auditoria_escrita.txt.

Peculiaridades do SEI que o fluxo respeita (detalhes no README.md):
  * todo infra_hash/id de serie vem do formulario da tela anterior - nunca e
    inventado, previsto ou fixado;
  * hdnFlagDocumentoCadastro vai sempre como "2" (o JS do SEI troca o "1" da tela
    por "2" no submit; enviando "1" o servidor so re-renderiza, sem criar nada);
  * rdoFormato vai sempre como "N" (Nato-digital, documento interno sem anexo);
  * "Documento Modelo" e o radio rdoTextoInicial = "D" + o protocolo do modelo em
    txtProtocoloDocumentoTextoBase (o id interno, hdnIdDocumentoTextoBase, so existe
    quando o modelo e escolhido na lupa - por isso ela e consultada em modo leitura,
    sempre com o infra_hash lido do proprio JS da tela);
  * a arvore do processo (iframe#ifrArvore), no SEI 5.x, entrega os nos dentro de
    strings JavaScript (Nos[N].acoes = ...) e invisiveis ao DOM - dai o fallback
    por regex no HTML bruto;
  * o header Location costuma vir com mojibake (bytes utf-8 lidos como latin-1),
    saneado em _limpar_url() antes de qualquer uso.
"""

# --- FACHADA DE COMPATIBILIDADE -------------------------------------
# O codigo mora em sei_escrita/; este arquivo preserva a API e a ordem de
# import originais (env -> scripts_read.app3 -> nucleo de escrita).

import os
import os as _os
import sys

_root_ = _os.path.dirname(_os.path.abspath(__file__))


def _carregar_env_():
    _env_path = _os.path.join(_root_, ".env")
    if _os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as _fh:
            for _linha in _fh:
                _linha = _linha.strip()
                if not _linha or _linha.startswith("#") or "=" not in _linha:
                    continue
                _chave, _, _valor = _linha.partition("=")
                _os.environ[_chave.strip()] = _valor.strip().strip(" ").strip(chr(39)).strip(chr(34))

_carregar_env_()

if _root_ not in sys.path:
    sys.path.insert(0, _root_)


from scripts_read.app3 import (  # noqa: E402
    BASE_SEI,
    SENHA,
    UNIDADE_ALVO,
    USUARIO,
    APIExtratorSEI,
    _limpar,
)


from sei_escrita.configuracao import (HOST_SEI, PASTA_TELAS, REDIRECIONAMENTOS, SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE, NOME_PLANILHA_PADRAO, COLUNAS_MERGE, _TAG_ABRE, _TAG_FECHA, _ATRIBUTOS, _ORDINAL, _ESPACO_NAO_QUEBRAVEL, _TAG_SPAN, _TAG_STRONG_ABRE, REGRAS_OBRIGATORIAS, COLUNAS_NUMERICAS, REGRAS_MERGE, MARCADORES_DO_MODELO, CAMPOS_RESUMO_PROCESSO, APELIDOS_COLUNAS)
from sei_escrita.erros import (LinhaSemProcesso, ErroInclusaoDocumento)
from sei_escrita.utilitarios import (_sem_decimal_de_planilha, _chave_coluna)
from sei_escrita.auditoria import (AuditoriaEscrita)
from sei_escrita.transporte import (TransporteEscrita)
from sei_escrita.payloads import (MontadorPayload)
from sei_escrita.arvore import (ExtratorArvore)
from sei_escrita.mesclagem import (MescladorDocumentos)
from sei_escrita.modelo import (ResolvedorModelo)
from sei_escrita.blocos import (GerenciadorBlocos)
from sei_escrita.inclusao import (InclusaoDocumentoSEI)
from sei_escrita.planilha import (resolver_caminho_planilha, carregar_dados_planilha, _renomear_colunas, carregar_planilha, _processo_marca_nao_visualizado)
from sei_escrita.lote import (_ResolvedorProcessos, _preflight_lote, _selecionar_linhas, executar_lote)
from sei_escrita.cli import (main)

__all__ = ['HOST_SEI', 'PASTA_TELAS', 'REDIRECIONAMENTOS', 'SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE', 'NOME_PLANILHA_PADRAO', 'COLUNAS_MERGE', '_TAG_ABRE', '_TAG_FECHA', '_ATRIBUTOS', '_ORDINAL', '_ESPACO_NAO_QUEBRAVEL', '_TAG_SPAN', '_TAG_STRONG_ABRE', 'REGRAS_OBRIGATORIAS', 'COLUNAS_NUMERICAS', 'REGRAS_MERGE', 'MARCADORES_DO_MODELO', 'CAMPOS_RESUMO_PROCESSO', 'APELIDOS_COLUNAS', 'LinhaSemProcesso', 'ErroInclusaoDocumento', '_sem_decimal_de_planilha', '_chave_coluna', 'AuditoriaEscrita', 'TransporteEscrita', 'MontadorPayload', 'ExtratorArvore', 'MescladorDocumentos', 'ResolvedorModelo', 'GerenciadorBlocos', 'InclusaoDocumentoSEI', 'resolver_caminho_planilha', 'carregar_dados_planilha', '_renomear_colunas', 'carregar_planilha', '_processo_marca_nao_visualizado', '_ResolvedorProcessos', '_preflight_lote', '_selecionar_linhas', 'executar_lote', 'main', 'BASE_SEI', 'SENHA', 'UNIDADE_ALVO', 'USUARIO', 'APIExtratorSEI', '_limpar']

if __name__ == "__main__":
    main()
