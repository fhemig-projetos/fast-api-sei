# -*- coding: utf-8 -*-
"""
O porteiro: unico ponto de requisicao de ESCRITA.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scripts_read.app3 import BASE_SEI

from .configuracao import HOST_SEI
from .erros import ErroInclusaoDocumento

class TransporteEscrita:
    """Unico ponto de requisicao de ESCRITA: whitelist/blacklist, Location saneado, resposta em BeautifulSoup (_sopa)."""

    PADROES_ESCRITA = (
        "login.php",
        "inicializar.php",
        "acao=principal",
        "acao=infra_trocar_unidade",
        "acao=procedimento_controlar",
        "acao=documento_escolher_tipo",
        "acao=documento_gerar",
        "acao=documento_modelo_selecionar",
        # --- Editor de conteudo (mail merge) -------------------------------
        # GET editor_montar. O POST de salvamento vai para editor_processar.php
        # e traz acao_origem=editor_montar, que NAO casa "acao=editor_montar" -
        # por isso o padrao do arquivo vem separado.
        "acao=editor_montar",
        "editor_processar.php",
        # Arvore "com o documento selecionado" (apos clicar no no do documento):
        # e a tela onde o SEI renderiza os links do editor.
        "acao=arvore_visualizar",
        # Inclusao em bloco de assinatura (GET da tela e POST de confirmacao):
        "acao=bloco_escolher",
        # Endpoints ajax do editor (verificar_assinatura, confirmacao, etc.).
        "controlador_ajax.php",
        # Pesquisa rapida do topo: localiza o processo pelo numero, sem
        # depender da paginacao da caixa de "Recebidos".
        "acao=protocolo_pesquisa_rapida",
        "acao=procedimento_trabalhar",
        "acao=procedimento_visualizar",
    )

    TOKENS_PROIBIDOS_ESCRITA = (
        "procedimento_trabalhar",
        "procedimento_visualizar",
        "procedimento_abrir",
        "procedimento_receber",
        "procedimento_enviar",
        "procedimento_concluir",
        "procedimento_atribuicao",
        "documento_visualizar",
        "documento_incluir_externo",
        "documento_editar_conteudo",
        "anotacao_registrar",
        "andamento_",
        "infra_upload",
        "peticionamento",
        "bloco_incluir",
        "sair",
    )

    TOKENS_ABERTURA = ("procedimento_trabalhar", "procedimento_visualizar")

    @staticmethod
    def _limpar_url(url):
        """
        Saneia o mojibake clássico do SEI-MG (bytes 'utf-8' lidos como latin-1,
        ex.: 'Â§' no lugar de 'ç'), que derruba o requests em UnicodeDecodeError.
        O re-encode latin-1 -> utf-8 só é tentado quando o conteúdo é de fato
        mojibake (re-encode válido).
        """
        if url is None:
            return None
        if isinstance(url, bytes):
            url = url.decode("iso-8859-1")
        url = url.strip().strip('"').strip("'")
        if not url:
            return url
        try:
            return url.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return url

    @staticmethod
    def _normalizar_url(url):
        """Converte action/Location relativo em URL absoluta (com o saneamento do mojibake)."""
        url = TransporteEscrita._limpar_url(url) or ""
        if url.startswith(("http://", "https://")):
            return url
        return HOST_SEI + url if url.startswith("/") else urljoin(BASE_SEI, url)

    @staticmethod
    def _sopa(resposta):
        """
        Constrói BeautifulSoup a partir dos BYTES da resposta, deixando o bs4
        respeitar o <meta charset> da página (evita mojibake do .text do requests).
        """
        return BeautifulSoup(getattr(resposta, "content", resposta), "html.parser")

    def _action(self, form, base_url=""):
        """action do formulario (normalizada) ou base_url quando ela nao existe."""
        action = self._limpar_url(form.get("action")) if form is not None else ""
        return self._normalizar_url(action) if action else base_url

    @staticmethod
    def _location(resposta):
        """
        Location ja saneado do mojibake (o header vem em caixa mista entre versoes).
        """
        for chave in ("Location", "location", "LOCATION"):
            try:
                if chave in resposta.headers:
                    return TransporteEscrita._limpar_url(resposta.headers[chave])
            except UnicodeDecodeError:
                continue
        return ""

    def _autorizar(self, url, ids_liberados=()):
        """
        Whitelist + blacklist de ESCRITA. A abertura de processo (trabalhar/visualizar)
        so passa para os ids informados em ids_liberados, ou seja, processos ja
        VISUALIZADOS - a trava de prazo nunca e afrouxada aqui.
        """
        url_low = url.lower()
        ids = set(ids_liberados or ())
        # O SEI usa "id_procedimento" na caixa e "id_protocolo" no
        # redirecionamento da pesquisa rapida. A guarda procurava so o
        # primeiro: na URL vinda da busca o id nao era reconhecido,
        # id_url ficava vazio e a propria URL que o SEI devolveu como
        # resultado da pesquisa era bloqueada como se fosse abertura nao
        # autorizada. E a MESMA divergencia de nome ja corrigida na busca -
        # aqui ela simplesmente travava o lote.
        m_id = re.search(r"[?&]id_(?:protocolo|procedimento)=(\d+)", url)
        id_url = m_id.group(1) if m_id else ""
        bloqueios = [
            token
            for token in self.TOKENS_PROIBIDOS_ESCRITA
            if token in url_low and not (token in self.TOKENS_ABERTURA and id_url in ids)
        ]
        if bloqueios:
            raise ErroInclusaoDocumento(
                f"URL bloqueada na escrita (contém '{bloqueios[0]}'): {url}", etapa="guardas"
            )
        if not any(padrao in url for padrao in self.PADROES_ESCRITA):
            raise ErroInclusaoDocumento(
                f"URL fora da whitelist de escrita: {url}", etapa="guardas"
            )

    def _ir(self, metodo, url, ids_liberados=None, **kwargs):
        """
        Unico ponto de saida HTTP do fluxo de escrita: normaliza a URL, aplica as
        guardas, audita e devolve (resposta, location ja saneado). Assim os passos do
        fluxo nunca repetem a leitura/limpeza do header Location.
        """
        url = self._normalizar_url(url)
        self._autorizar(url, ids_liberados or ())
        self._registrar_auditoria_escrita(f"{metodo.upper()} {url}")
        resposta = getattr(self.session, metodo.lower())(url, **kwargs)
        location = self._location(resposta)
        destino = f"Location: {location}" if location else f"url_final: {resposta.url}"
        self._registrar_auditoria_escrita(f"  <- status {resposta.status_code} | {destino}")
        return resposta, location

    @staticmethod
    def _mensagens_erro(resposta):
        """
        Colhe as mensagens de validacao do SEI (barra de progresso, alert() etc.) para
        explicar por que a criacao nao redirecionou.
        """
        msgs = [
            tag.get_text(strip=True)
            for tag in TransporteEscrita._sopa(resposta).select(
                ".infraBarraProgresso, .infraMensagemErro, #divInfraMsg, fieldset legend"
            )
        ]
        msgs += [
            m.group(1)
            for m in re.finditer(r"""alert\(['"]([^'"]{5,200})['"]\)""", resposta.text)
        ]
        unicas = list(dict.fromkeys(m for m in msgs if m))
        return (" | Mensagens: " + " ; ".join(unicas[:5])) if unicas else ""

