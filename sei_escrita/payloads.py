# -*- coding: utf-8 -*-
"""
O preenchedor de formularios dos POSTs do SEI.
"""

import time

class MontadorPayload:
    """Monta os campos dos POSTs do SEI a partir do HTML servido (hidden inputs, flags, texto inicial)."""

    # Valores do radio "Texto Inicial" da tela de geracao (configurarTextoInicial()):
    # N = nenhum, T = texto padrao, D = documento modelo (protocolo de doc existente).
    TEXTO_INICIAL_MODELO = "D"

    TIPOS_IGNORADOS = ("submit", "button", "image", "checkbox", "radio", "file")

    @staticmethod
    def _extrair_hidden_inputs(form):
        """
        Colhe todos os campos enviaveis de um formulario (hidden, text, textarea e
        select), como um submit padrao faria. E daqui que saem todos os infra_hash
        e IDs fresquinhos da tela (regra de ouro: nunca inventar/prever hash).
        """
        if form is None:
            return {}
        payload = {}
        for tag in form.select("input[name], select[name], textarea[name]"):
            if tag.name == "select":
                opcao = tag.select_one("option[selected]") or tag.select_one("option")
                payload[tag["name"]] = opcao.get("value", "") if opcao is not None else ""
            elif tag.name == "textarea":
                payload[tag["name"]] = tag.get_text() or ""
            elif (tag.get("type") or "text").lower() not in MontadorPayload.TIPOS_IGNORADOS:
                payload[tag["name"]] = tag.get("value") or ""
        return payload

    @staticmethod
    def _form_e_payload(sopa, ids=(), campos=()):
        """
        Devolve (form, payload) do formulario pedido: primeiro pelos ids conhecidos
        e, se nao achar, pelo formulario que contenha um dos campos de negocio.
        """
        form = None
        for ident in ids:
            form = sopa.find("form", id=ident)
            if form is not None:
                break
        if form is None:
            form = next(
                (
                    candidato
                    for candidato in sopa.select("form")
                    if any(candidato.select_one(f'[name="{campo}"]') for campo in campos)
                ),
                None,
            )
        if form is None:
            return None, {}
        return form, MontadorPayload._extrair_hidden_inputs(form)

    @staticmethod
    def _atribuir_campo(payload, candidatos, valor):
        """
        Sobrescreve o campo de negocio na 1a chave presente no payload (a nomenclatura
        varia entre versoes do SEI); se nenhuma existir, insere a 1a candidata.
        """
        chave = next((nome for nome in candidatos if nome in payload), candidatos[0])
        payload[chave] = str(valor)
        return chave

    @classmethod
    def _texto_inicial_efetivo(cls, texto_inicial, protocolo_modelo=None):
        """
        Regra do radio "Texto Inicial": com modelo informado o valor e sempre "D"
        (Documento Modelo); sem modelo vale o que veio do CLI/API (default "N").
        """
        if protocolo_modelo:
            return cls.TEXTO_INICIAL_MODELO
        return str(texto_inicial or "N").strip().upper() or "N"

    def _payload_gerar(
        self,
        sopa,
        id_hipotese_legal,
        texto_inicial,
        dados_adicionais=None,
        protocolo_modelo=None,
        id_documento_modelo=None,
    ):
        """
        Monta o payload do cadastro do documento (frmDocumentoCadastro nas telas atuais,
        frmDocumentoGerar em versoes antigas): todos os campos da tela + sobrescritas de
        negocio (impostas pelo SEI, ver README). Devolve (form, payload).

        protocolo_modelo    : protocolo de um documento existente usado como "Documento
                              Modelo" (ex.: "148410511"). Liga o radio rdoTextoInicial
                              = "D" e preenche txtProtocoloDocumentoTextoBase.
        id_documento_modelo : id interno do modelo (hdnIdDocumentoTextoBase), o mesmo que
                              a lupa "Selecionar nos Favoritos" grava. E opcional: o
                              protocolo informado a mao e resolvido pelo backend do SEI.
        """
        form, payload = self._form_e_payload(
            sopa,
            ids=("frmDocumentoCadastro", "frmDocumentoGerar"),
            campos=("rdoNivelAcesso", "txtDataElaboracao"),
        )
        if not payload:
            return form, {}
        for candidatos, valor in (
            (("hdnFlagDocumentoCadastro",), "2"),
            (("rdoFormato",), "N"),
            (("rdoNivelAcesso", "selNivelAcesso"), "1"),
            (
                ("selHipotesesLegais", "selHipoteseLegal", "hdnHipoteseLegais", "txtHipoteseLegal"),
                id_hipotese_legal,
            ),
            (
                ("rdoTextoInicial", "selTextoInicial"),
                self._texto_inicial_efetivo(texto_inicial, protocolo_modelo),
            ),
            (("txtDataElaboracao",), time.strftime("%d/%m/%Y")),
        ):
            self._atribuir_campo(payload, candidatos, valor)
        if protocolo_modelo:
            self._atribuir_campo(
                payload, ("txtProtocoloDocumentoTextoBase",), protocolo_modelo
            )
            self._atribuir_campo(
                payload, ("hdnIdDocumentoTextoBase",), id_documento_modelo or ""
            )
            # configurarTextoInicial() limpa o "Texto Padrao" ao marcar "Documento
            # Modelo"; replicar isso evita que o servidor entenda que dois textos
            # iniciais foram escolhidos ao mesmo tempo.
            for campo_texto_padrao in ("txtTextoPadrao", "hdnIdTextoPadrao"):
                if campo_texto_padrao in payload:
                    payload[campo_texto_padrao] = ""
        payload.update({chave: str(valor) for chave, valor in (dados_adicionais or {}).items()})
        return form, payload

