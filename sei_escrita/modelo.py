# -*- coding: utf-8 -*-
"""
O caçador do Documento Modelo (lupa do SEI).
"""

import re

import requests

from .erros import ErroInclusaoDocumento

class ResolvedorModelo:
    """Resolve o Documento Modelo consultando a lupa do SEI (protocolo -> id_documento)."""

    @staticmethod
    def _url_lupa_documento_modelo(sopa):
        """
        URL da lupa "Documento Modelo" (objLupaDocumentoTextoBase), lida no proprio JS da
        tela de geracao - inclusive o infra_hash daquela action, que so existe ali (regra
        de ouro: hash nunca e previsto nem fixado). Devolve "" quando a tela nao expoe o
        objeto (tipo de documento sem "Documento Modelo" habilitado).
        """
        html = str(sopa)
        m = re.search(
            r"new\s+infraLupaText\(\s*['\"]txtProtocoloDocumentoTextoBase['\"]\s*,\s*"
            r"['\"]hdnIdDocumentoTextoBase['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            html,
            flags=re.I,
        )
        if m and "documento_modelo_selecionar" in m.group(1).lower():
            return m.group(1).strip()
        # Fallback: qualquer URL de documento_modelo_selecionar citada na tela (ex.:
        # onclick de lupa), desde que venha do HTML - nunca de constante do script.
        m = re.search(
            r"['\"]([^'\"]*acao=documento_modelo_selecionar[^'\"]*)['\"]", html, flags=re.I
        )
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extrair_id_modelo_na_lupa(html_texto, protocolo_modelo):
        """
        Devolve o valor que a lupa REAL gravaria em hdnIdDocumentoTextoBase para o
        protocolo pedido ("" quando nao ha par confiavel).

        Fonte da verdade: o botao "Transportar" chama infraTransportarSelecao() ->
        infraTableToHiddenText() (infra_js/InfraUtil.js do proprio SEI):

            objText.value   = box.title;   // txtProtocolo...Base = protocolo
            objHidden.value = box.value;   // hdnId...TextoBase   = value do radio

        Ou seja: acha o radio chkInfraItem cujo title e o protocolo e devolve o value
        dele. O id_documento=NNN dos links de visualizar (coluna de acao da linha) e de
        outro fim - e IGNORADO, pois pegaria um id errado.
        """
        protocolo = re.sub(r"\D", "", str(protocolo_modelo or ""))
        if not protocolo:
            return ""
        # 1) Padrao real da lista SEI: title = protocolo -> value = id do registro.
        for etiqueta in re.findall(r"<input[^>]*>", html_texto, flags=re.I):
            if not re.search(r"""name=["']chkInfraItem["']""", etiqueta, flags=re.I):
                continue
            m_titulo = re.search(r"""title=["']([^"']*)["']""", etiqueta, flags=re.I)
            m_valor = re.search(r"""value=["'](\d{1,12})["']""", etiqueta, flags=re.I)
            if m_titulo and m_valor and re.sub(r"\D", "", m_titulo.group(1)) == protocolo:
                return m_valor.group(1)
        # 2) Fallback: outras versoes de lupa que chamem selecionarItem(id, 'protocolo').
        m = re.search(
            r"""selecionarItem\(\s*['"]?(\d{1,12})['"]?\s*,\s*['"]?\s*""" + re.escape(protocolo),
            html_texto,
            flags=re.I,
        )
        return m.group(1) if m else ""


    def _resolver_documento_modelo(
        self,
        sopa_gerar,
        protocolo_modelo,
        id_documento_modelo="",
        ids_liberados=(),
        consultar_lupa=True,
    ):
        """
        Resolve o "Documento Modelo" (radio rdoTextoInicial = "D") da tela de geracao.

        O protocolo sozinho ja basta para o POST, mas quando o modelo e escolhido na lupa
        ("Selecionar nos Favoritos") o browser tambem envia hdnIdDocumentoTextoBase. Para
        replicar isso de verdade fazemos o GET de leitura na lupa
        documento_modelo_selecionar - URL e infra_hash lidos do JS da propria tela - e
        tentamos casar id+protocolo no HTML devolvido. Qualquer falha aqui e apenas
        informativa: o fluxo continua com o protocolo, que e o campo de negocio.

        Devolve {"protocolo": str, "id_documento": str} (id_documento = "" quando nao foi
        possivel isolar o id com seguranca).
        """
        protocolo = re.sub(r"\D", "", str(protocolo_modelo or "")) or str(
            protocolo_modelo or ""
        ).strip()
        if id_documento_modelo:
            print(f"  Documento Modelo: id_documento informado no CLI ({id_documento_modelo}).")
            return {"protocolo": protocolo, "id_documento": str(id_documento_modelo).strip()}
        if not consultar_lupa:
            print("  Documento Modelo: consulta da lupa desativada; enviando apenas o protocolo.")
            return {"protocolo": protocolo, "id_documento": ""}

        url_lupa = self._url_lupa_documento_modelo(sopa_gerar)
        if not url_lupa:
            print(
                "  Aviso: lupa de Documento Modelo nao exposta na tela; "
                "enviando apenas o protocolo."
            )
            return {"protocolo": protocolo, "id_documento": ""}
        print(f"  Consultando a lupa de Documento Modelo (protocolo {protocolo})...")
        try:
            resp_lupa, _ = self._ir("get", url_lupa, ids_liberados=ids_liberados, timeout=30)
        except (ErroInclusaoDocumento, requests.exceptions.RequestException) as exc:
            print(
                f"  Aviso: lupa de Documento Modelo falhou ({exc}); "
                "enviando apenas o protocolo."
            )
            return {"protocolo": protocolo, "id_documento": ""}
        self._salvar_tela("2b_lupa_documento_modelo", resp_lupa)
        if resp_lupa.status_code != 200:
            print(
                f"  Aviso: lupa de Documento Modelo respondeu HTTP {resp_lupa.status_code}; "
                "enviando apenas o protocolo."
            )
            return {"protocolo": protocolo, "id_documento": ""}

        html_lupa = str(self._sopa(resp_lupa))
        id_modelo = self._extrair_id_modelo_na_lupa(html_lupa, protocolo)
        if id_modelo:
            print(f"  Documento Modelo resolvido na lupa: hdnIdDocumentoTextoBase = {id_modelo}.")
        elif protocolo and protocolo in re.sub(r"\D", "", html_lupa):
            print(
                "  Lupa listou o protocolo, mas o par id+protocolo nao foi isolado com "
                "seguranca; enviando apenas o protocolo."
            )
        else:
            print("  Aviso: lupa nao devolveu o id do modelo; enviando apenas o protocolo.")
        return {"protocolo": protocolo, "id_documento": id_modelo}

