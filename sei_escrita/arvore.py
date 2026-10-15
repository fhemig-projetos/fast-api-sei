# -*- coding: utf-8 -*-
"""
O leitor da arvore de documentos do SEI.
"""

import re

from bs4 import BeautifulSoup

from html import unescape as _unescape_entidades

from .transporte import TransporteEscrita

class ExtratorArvore:
    """Le a arvore de documentos e localiza links/botoes (fallback por regex no SEI 5.x)."""

    def _link_trabalhar_na_caixa(self, id_procedimento):
        """href de procedimento_trabalhar do processo, lido na propria caixa (hash fresco)."""
        html = self.navegar_para_controle()
        if not html:
            return ""
        alvo = f"id_procedimento={id_procedimento}"
        return next(
            (
                a["href"]
                for a in BeautifulSoup(html, "html.parser").select("a[href]")
                if "procedimento_trabalhar" in a["href"] and alvo in a["href"]
            ),
            "",
        )

    def _link_trabalhar_do_processo(self, id_procedimento, numero="",
                                    mesmo_assim=False):
        """
        Link de ABERTURA do processo, com duas fontes e nesta ordem.

        A caixa vem primeiro: e o caminho historico, ja testado, e traz o hash
        fresco da propria tela. Mas a caixa so mostra a pagina 1 - num lote com
        processos antigos o link nao estava la e a edicao falhava, mesmo com o
        processo corretamente localizado pela busca.

        Quando a caixa nao traz o link, refazemos a PESQUISA RAPIDA e usamos o
        href que ela devolve.

        Refazer AGORA, e nao reaproveitar o link guardado no pre-voo, e
        proposital: num lote grande o pre-voo resolve todos os processos antes
        de gravar qualquer um, e a gravacao comeca depois. Um hash guardado
        nesse intervalo pode ja ter vencido - o link usado aqui e fresco de
        verdade.
        """
        link = self._link_trabalhar_na_caixa(id_procedimento)
        if link:
            return link
        if not numero:
            return ""
        # Aqui o processo JA foi validado no pre-voo (inclusive a trava de
        # prazo), entao nao ha marca a relebr: o que precisamos e do link.
        item = self._buscar_processo_pela_pesquisa(numero, mesmo_assim=True)
        # So aceitamos o link se for mesmo o processo pedido.
        if item and str(item.get("IdProtocolo") or "") == str(id_procedimento).strip():
            return item.get("Link") or ""
        return ""

    @staticmethod
    def _link_tipo_na_arvore(resposta):
        """
        href de documento_escolher_tipo ("Incluir Documento") na arvore do processo.

        No SEI 5.x os nos vem dentro de strings JavaScript (Nos[N].acoes = '<a href=...>')
        e sao invisiveis ao parser DOM - dai o fallback por regex no HTML bruto. O href
        ja traz o infra_hash fresco do proprio no (regra de ouro: nunca inventar hash).
        """
        link = TransporteEscrita._sopa(resposta).select_one(
            "a[href*='documento_escolher_tipo']"
        )
        if link is not None and link.get("href"):
            return link["href"]
        m = re.search(
            r"""href=["'](controlador\.php\?acao=documento_escolher_tipo[^"']+)["']""",
            resposta.text,
            flags=re.I,
        )
        return m.group(1) if m else ""

    @staticmethod
    def _link_editor_na_arvore(resposta, id_documento, acoes=("editor_montar", "documento_alterar")):
        """
        href para abrir o editor do documento alvo, lido na propria tela (hash fresco).

        No SEI 5.x os nos vem em strings JavaScript (Nos[N].acoes = ...) e sao
        invisiveis ao DOM - dai a busca por regex no HTML bruto, como em
        _link_tipo_na_arvore. Prioridade: editor_montar direto; na pratica a arvore
        so expoe documento_alterar ("Consultar/Alterar", acao_origem=arvore_visualizar)
        e e a partir dele que o SEI monta o editor. O href ja traz id_procedimento,
        id_documento e infra_hash (regra de ouro: nada e montado nem previsto aqui).
        So e aceito href com id_documento igual ao alvo: um link de outro documento
        montaria o editor - e o salvamento - no lugar errado.
        """
        texto = resposta.text or ""
        alvo = f"id_documento={str(id_documento).strip()}"
        for acao in acoes:
            encontrados = []
            for padrao in (
                rf"""href=["']([^"']*acao={acao}[^"']+)["']""",
                rf"""["']([^"']*acao={acao}[^"']+)["']""",
            ):
                for href in re.findall(padrao, texto, flags=re.I):
                    if href not in encontrados:
                        encontrados.append(href)
            for href in encontrados:
                if alvo in href:
                    return href
        return ""

    @staticmethod
    def _todos_editores(resposta):
        """
        Devolve ({nome_campo: html}, form) com TODOS os textareas txaEditor_XXXXX.

        O SEI abre um editor por parte do documento (cabecalho, corpo, rodape...),
        cada um com o sufixo dinamico txaEditor_<numero>. Buscar apenas o primeiro
        realaria o cabecalho (somente leitura) e perderia o corpo do texto. Todos
        sao lidos do HTML - nenhum nome e fixado no codigo (regra de ouro).
        """
        sopa = TransporteEscrita._sopa(resposta)
        partes = {}
        for tag in sopa.find_all("textarea", attrs={"name": re.compile(r"^txaEditor_")}):
            nome = (tag.get("name") or "").strip()
            if nome:
                partes[nome] = tag.get_text()
        if not partes:
            return {}, None
        primeiro = sopa.find("textarea", attrs={"name": next(iter(partes))})
        return partes, (primeiro.find_parent("form") if primeiro is not None else None)

    @staticmethod
    def _link_ajax_editor(resposta, acao_ajax, id_documento=None):
        """
        Extrai a URL de um endpoint ajax do editor (controlador_ajax.php?acao_ajax=...).

        Mesma politica do _link_tipo_na_arvore: a URL e lida do HTML servido (o
        hash que o SEI renderiza naquele ponto) e nunca montada no codigo. Quando
        id_documento vem informado, exige que a URL aponte para o documento-alvo,
        para nao disparar ajax de outro documento.
        """
        alvo = f"id_documento={str(id_documento).strip()}" if id_documento else ""
        for achado in re.findall(
            rf"""["']([^"']*controlador_ajax\.php\?acao_ajax={re.escape(acao_ajax)}[^"']*)["']""",
            resposta.text or "",
            flags=re.I,
        ):
            if not alvo or alvo in achado:
                # Em atributo o SEI pode gravar &amp; no lugar de &; a URL que o
                # browser usaria tem o & simples.
                return _unescape_entidades(achado)
        return ""

