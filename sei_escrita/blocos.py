# -*- coding: utf-8 -*-
"""
O responsavel pelos blocos de assinatura.
"""

import re

from .configuracao import REDIRECIONAMENTOS
from .erros import ErroInclusaoDocumento
from .transporte import TransporteEscrita

class GerenciadorBlocos:
    """Bloco de assinatura: escolher, marcar o checkbox e confirmar."""

    # ------------------------------------------------- bloco de assinatura
    # Reproduz o fluxo "Incluir em Bloco de Assinatura" do SEI (capturado no
    # Network): GET acao=bloco_escolher, que traz o <select id=selBloco> e a
    # tabela de documentos do processo; e POST no MESMO acao=bloco_escolher com
    # o bloco escolhido, o botao e o checkbox do documento.
    #
    # Duas peculiaridades do SEI 5.x que o codigo respeita:
    #  * o <select id=selBloco> tem onchange que dispara this.form.submit():
    #    escolher o bloco faz um POST sozinho, que devolve a tela com um
    #    infra_hash NOVO. O POST que confirma a inclusao usa esse hash novo - o
    #    que estava na action do primeiro GET nao serve. Por isso ha dois POSTs
    #    aqui, como no browser (o primeiro so redesenha a tela; se nao fosse
    #    necessario, o segundo continuaria funcionando).
    #  * o checkbox do documento se chama chkDocumentosItem<N>, onde N e a
    #    POSICAO dele na tabela e muda a cada inclusao. O id_documento esta no
    #    value; e de la que o nome do campo e lido (nada e fixado).
    BOTAO_INCLUIR = "sbmIncluir"
    BOTAO_INCLUIR_DISPONIBILIZAR = "sbmIncluirDisponibilizar"

    @staticmethod
    def _link_bloco_escolher(resposta, id_procedimento, id_documento):
        """
        href da tela "Incluir em Bloco de Assinatura" para o documento alvo.

        O no do documento na arvore traz acao=bloco_escolher com id_procedimento,
        id_documento e um infra_hash fresco. So e aceito href cujo id_documento
        e o alvo: um link de outro documento buscaria a tela errada.
        """
        texto = resposta.text or ""
        alvo = f"id_documento={str(id_documento).strip()}"
        alvo_proc = f"id_procedimento={str(id_procedimento).strip()}"
        for padrao in (
            r"""href=["']([^"']*acao=bloco_escolher[^"']+)["']""",
            r"""["']([^"']*acao=bloco_escolher[^"']+)["']""",
        ):
            for href in re.findall(padrao, texto, flags=re.I):
                if alvo in href and alvo_proc in href:
                    return href
        return ""

    @staticmethod
    def _blocos_disponiveis(resposta):
        """{id_bloco: rotulo} lido do <select id=selBloco> da tela."""
        sopa = TransporteEscrita._sopa(resposta)
        select = sopa.find("select", id="selBloco")
        if select is None:
            return {}
        blocos = {}
        for opcao in select.find_all("option"):
            valor = (opcao.get("value") or "").strip()
            rotulo = opcao.get_text(" ", strip=True) or valor
            if valor and valor.lower() != "null":
                blocos[valor] = rotulo
        return blocos

    @staticmethod
    def _checkbox_documento(resposta, id_documento):
        """
        nome do checkbox (chkDocumentosItem<N>) do documento alvo.

        O N e a posicao na tabela (muda a cada inclusao), mas o value carrega o
        id_documento - por isso procuramos pelo value e devolvemos o name.
        """
        sopa = TransporteEscrita._sopa(resposta)
        alvo = str(id_documento).strip()
        for entrada in sopa.find_all("input", attrs={"type": "checkbox"}):
            if (entrada.get("value") or "").strip() == alvo:
                nome = (entrada.get("name") or "").strip()
                if nome:
                    return nome
        return ""

    @staticmethod
    def _avisos_do_sei(resposta):
        """
        Textos de alerta que o SEI devolveu na tela (div.alert / INFRA_VALIDACAO).

        O SEI recusa a inclusao em bloco com HTTP 200 e a mensagem em um
        div.alert - por exemplo "Bloco X nao possui unidades configuradas para
        disponibilizacao". Sem ler isso, um 200 seria tomado por sucesso.
        """
        sopa = TransporteEscrita._sopa(resposta)
        avisos = [
            (el.get_text(" ", strip=True) or "").strip()
            for el in sopa.select("div.alert, div.infraMsgErro, div.infraValidacao")
        ]
        avisos = [a for a in avisos if a]
        texto = sopa.get_text(" ", strip=True) or ""
        for achado in re.findall(r"INFRA_VALIDACAO[^\n]{0,300}", texto):
            avisos.append(achado.strip())
        return avisos

    def incluir_em_bloco_assinatura(
        self, id_procedimento, id_documento, numero_bloco, disponibilizar=True,
        numero="",
    ):
        """
        Inclui o documento no bloco de assinatura informado e o disponibiliza.

        Reproduz o fluxo real do SEI (regra de ouro: todo infra_hash e todo
        campo dinamico vem da tela, nunca e fixado nem previsto):
          1. arvore do processo -> arvore com o documento selecionado;
          2. link acao=bloco_escolher do documento alvo (hash fresco do no);
          3. GET dessa tela -> select selBloco e a tabela de documentos;
          4. confere que o numero_bloco existe na lista (aborta com a lista
             real se nao existir - evita colocar o documento no bloco errado);
          5. POST 1 com o bloco, imitando o onchange do select: o SEI devolve a
             tela com um infra_hash novo;
          6. POST 2 no form recem-lido, com o botao e o checkbox do documento.

        numero_bloco e o ID interno do bloco (o value da opcao, ex.: 7712033),
        nao o numero de exibicao.

        Retorna True quando o SEI confirma; falhas viram ErroInclusaoDocumento.
        """
        id_procedimento = str(id_procedimento).strip()
        id_documento = str(id_documento).strip()
        numero_bloco = str(numero_bloco or "").strip()
        if not id_procedimento or not id_documento or not numero_bloco:
            raise ErroInclusaoDocumento(
                "id_procedimento, id_documento e numero_bloco sao obrigatorios "
                "para incluir em bloco de assinatura.",
                etapa="7_bloco_escolher",
            )
        ids = {id_procedimento}
        acao_final = "disponibilizado" if disponibilizar else "incluido"
        self._registrar_auditoria_escrita(
            f"[BLOCO] documento {id_documento} do processo {id_procedimento} "
            f"-> bloco {numero_bloco} ({acao_final})."
        )

        # 1) Arvore do processo e arvore com o documento selecionado.
        link_proc = self._link_trabalhar_do_processo(id_procedimento, numero)
        if not link_proc:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado (nem na caixa, nem pela pesquisa rapida).",
                etapa="7_bloco_escolher",
            )
        resp_proc, _ = self._ir("get", link_proc, ids_liberados=ids, timeout=30)
        iframe = self._sopa(resp_proc).select_one("iframe#ifrArvore")
        src_arvore = (iframe.get("src") if iframe is not None else "") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore.",
                etapa="7_bloco_escolher",
            )
        resp_arvore, _ = self._ir("get", src_arvore, ids_liberados=ids, timeout=30)

        # 2) Link da tela "Incluir em Bloco de Assinatura".
        href_bloco = self._link_bloco_escolher(
            resp_arvore, id_procedimento, id_documento
        )
        if not href_bloco:
            raise ErroInclusaoDocumento(
                "Link Incluir em Bloco de Assinatura nao encontrado para o "
                f"documento {id_documento} na arvore do processo.",
                etapa="7_bloco_escolher",
            )
        resp_tela, _ = self._ir("get", href_bloco, ids_liberados=ids, timeout=30)
        self._salvar_tela("7_bloco_escolher", resp_tela)

        form, payload = self._form_e_payload(
            self._sopa(resp_tela), ids=("frmBlocoEscolher",), campos=("selBloco",)
        )
        if form is None:
            raise ErroInclusaoDocumento(
                "Tela do bloco nao expoe o formulario frmBlocoEscolher.",
                etapa="7_bloco_escolher",
            )

        # 3) O bloco digitado existe? Confere antes de qualquer POST: se o
        # numero estiver errado, abortamos com a lista real em vez de mandar o
        # documento para o bloco errado.
        blocos = self._blocos_disponiveis(resp_tela)
        if numero_bloco not in blocos:
            lista = "; ".join(f"{v} ({k})" for k, v in blocos.items()) or "(nenhum)"
            raise ErroInclusaoDocumento(
                f"bloco {numero_bloco} nao existe neste processo. "
                f"Blocos disponiveis: {lista}",
                etapa="7_bloco_escolher",
            )
        print(f"  Bloco escolhido: {blocos[numero_bloco]}")

        # O checkbox do documento: nome dinamico, lido da propria tela.
        nome_checkbox = self._checkbox_documento(resp_tela, id_documento)
        if not nome_checkbox:
            raise ErroInclusaoDocumento(
                f"documento {id_documento} nao aparece na lista de documentos "
                "da tela de bloco de assinatura.",
                etapa="7_bloco_escolher",
            )

        botao = (
            self.BOTAO_INCLUIR_DISPONIBILIZAR
            if disponibilizar
            else self.BOTAO_INCLUIR
        )

        # 4) POST 1: imita o onchange do select. O SEI devolve a tela com um
        # infra_hash NOVO, que e o que o POST de confirmacao usa.
        payload1 = dict(payload)
        payload1["selBloco"] = numero_bloco
        url1 = self._action(form, base_url=href_bloco)
        resp1, _ = self._ir("post", url1, ids_liberados=ids, data=payload1, timeout=30)
        self._salvar_tela("8a_bloco_pos_selecionar", resp1)
        avisos1 = self._avisos_do_sei(resp1)
        if avisos1:
            raise ErroInclusaoDocumento(
                f"o SEI recusou o bloco {numero_bloco}: {avisos1[0][:300]}",
                etapa="7_bloco_escolher",
            )

        # 5) POST 2: o form recem-lido (hash novo) com o botao e o checkbox.
        form2, payload2 = self._form_e_payload(
            self._sopa(resp1), ids=("frmBlocoEscolher",), campos=("selBloco",)
        )
        if form2 is None:
            raise ErroInclusaoDocumento(
                "Apos escolher o bloco, o SEI nao devolveu o formulario de "
                "confirmacao do bloco.",
                etapa="8_bloco_incluir",
            )
        payload2["selBloco"] = numero_bloco
        payload2[nome_checkbox] = id_documento
        payload2["hdnDocumentosItensSelecionados"] = id_documento
        payload2[botao] = "Incluir e Disponibilizar" if disponibilizar else "Incluir"
        url2 = self._action(form2, base_url=url1)
        resp2, _ = self._ir("post", url2, ids_liberados=ids, data=payload2, timeout=30)
        # Guarda a tela da confirmacao: e o unico jeito de ver o que o SEI
        # respondeu quando a inclusao nao aparece no bloco.
        self._salvar_tela("8_bloco_resultado", resp2, forcar=True)

        avisos2 = self._avisos_do_sei(resp2)
        if avisos2:
            self._registrar_auditoria_escrita(
                f"[RECUSA-BLOCO] documento {id_documento} no bloco "
                f"{numero_bloco}: {avisos2[0][:200]}"
            )
            raise ErroInclusaoDocumento(
                f"o SEI recusou a inclusao no bloco {numero_bloco}: "
                f"{avisos2[0][:300]}",
                etapa="8_bloco_incluir",
            )
        if resp2.status_code in REDIRECIONAMENTOS or resp2.status_code == 200:
            self._registrar_auditoria_escrita(
                f"[SUCESSO-BLOCO] documento {id_documento} no bloco "
                f"{numero_bloco} ({acao_final})."
            )
            return True
        raise ErroInclusaoDocumento(
            f"inclusao no bloco falhou (HTTP {resp2.status_code})."
            f"{self._mensagens_erro(resp2)}",
            etapa="8_bloco_incluir",
        )


