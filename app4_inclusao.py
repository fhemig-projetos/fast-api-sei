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

import re  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from html import unescape as _unescape_entidades  # noqa: E402
from urllib.parse import urljoin  # noqa: E402

import pandas as pd  # noqa: E402
import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from scripts_read.app3 import (  # noqa: E402
    BASE_SEI,
    SENHA,
    UNIDADE_ALVO,
    USUARIO,
    APIExtratorSEI,
    _limpar,
)

HOST_SEI = "https://www.sei.mg.gov.br"
PASTA_TELAS = "ruas_inclusao"

REDIRECIONAMENTOS = (301, 302, 303, 307, 308)

# Serie do documento "CESSÃO ESPECIAL ÔNUS CEDENTE" no SEI-MG (id_serie do tipo). Fica
# documentada aqui porque e o caso de uso do fluxo com "Documento Modelo"; continua
# sendo o CLI/API quem informa a serie (--serie 4110) - nada fica fixado no payload.
SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE = "4110"

# Planilha de mesclagem (Mail Merge) da Cessao Especial: nome do arquivo padrao e
# mapeamento marcador -> coluna. O arquivo padrao e procurado na raiz do projeto e,
# como fallback, em ~/Downloads (nunca versionado: *.xlsx esta no .gitignore).
NOME_PLANILHA_PADRAO = "planilha exemplo cessao especial.xlsx"

# Colunas da planilha usadas na mesclagem.
COLUNAS_MERGE = ("Nome Servidor", "MASP", "ADM", "Cargo", "Função", "Nº do termo")

# Os marcadores do Documento Modelo e a coluna correspondente estao descritos
# em REGRAS_MERGE (com regex, porque varios deles dependem do contexto: o mesmo
# "(Nº)" aparece para MASP e para ADM). As entidades do SEI (&ordm; para o
# ordinal, &Ccedil; para o cedilha) aparecem no HTML DESESCAPADO, por isso os
# padroes aceitam as duas formas.

# (descricao, coluna, regex, grupo_substituido, tratamento)
# grupo_substituido = 0 substitui o grupo inteiro; >0 substitui so aquele grupo.
REGRAS_MERGE = (
    (
        "MASP",
        "MASP",
        r"(MASP\s*(?:&nbsp;)?\s*<span[^>]*>\s*(?:<strong[^>]*>\s*)?)(\(N(?:\s*&ordm;|º)\))",
        2,
        "texto",
    ),
    (
        "ADM",
        "ADM",
        r"(ADM\s*(?:&nbsp;)?\s*<span[^>]*>\s*(?:<strong[^>]*>\s*)?)(\(N(?:\s*&ordm;|º)\))",
        2,
        "texto",
    ),
    (
        "Nº do termo (titulo)",
        "Nº do termo",
        r"(N\s*&ordm;\s*<span[^>]*>\s*)(XX)(\s*</span\s*>)",
        2,
        "texto",
    ),
    (
        "Nome do servidor (cedente)",
        "Nome Servidor",
        r"\(NOME DO\(A\) SERVIDOR\(A\) CEDIDO\(A\)\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (cedido)",
        "Nome Servidor",
        r"\(NOME DO SERVIDOR CEDIDO\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (clausula)",
        "Nome Servidor",
        r"\(NOME\)",
        0,
        "texto",
    ),
    (
        "Nome do servidor (assinatura)",
        "Nome Servidor",
        r"\[NOME DO SERVIDOR\]",
        0,
        "texto",
    ),
    (
        "Cargo (sigla)",
        "Cargo",
        r"\(SIGLA DO CARGO\)",
        0,
        "texto",
    ),
    (
        "Funcao do servidor",
        "Função",
        r"\(FUN\s*(?:ÇÃO|&Ccedil;&Atilde;O)\)",
        0,
        "texto",
    ),
    (
        "Data do preenchimento",
        None,
        r"\[DATA DO DIA DO PREENCHIMENTO\]",
        0,
        "data",
    ),
)

# Marcadores que o modelo usa hoje; qualquer um que ainda exista no HTML final
# significa merge incompleto (checa de forma barata, sem regex).
MARCADORES_DO_MODELO = (
    "(NOME DO(A) SERVIDOR(A) CEDIDO(A))",
    "(NOME DO SERVIDOR CEDIDO)",
    "(SIGLA DO CARGO)",
    "[NOME DO SERVIDOR]",
    "[DATA DO DIA DO PREENCHIMENTO]",
)

CAMPOS_RESUMO_PROCESSO = (
    "Processo",
    "IdProtocolo",
    "Tipo",
    "Especificacao",
    "NaoVisualizado",
    "NaoRecebido",
    "AtribuidoA",
    "Interessados",
)


class ErroInclusaoDocumento(Exception):
    """Exceção descritiva do fluxo de inclusão de documento no SEI."""

    def __init__(self, mensagem, etapa=None):
        self.etapa = etapa or "geral"
        super().__init__(f"[{self.etapa}] {mensagem}")


class InclusaoDocumentoSEI(APIExtratorSEI):
    """
    Escrita de documentos no SEI-MG.

    Herda de APIExtratorSEI apenas autenticar()/trocar_unidade() e a raspagem da
    caixa (navegar_para_controle). Todas as requisições deste fluxo passam por
    _ir(), que possui whitelist/blacklist próprias de ESCRITA.
    """

    # Valores do radio "Texto Inicial" da tela de geracao (configurarTextoInicial()):
    # N = nenhum, T = texto padrao, D = documento modelo (protocolo de doc existente).
    TEXTO_INICIAL_MODELO = "D"

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
        # Endpoints ajax do editor (verificar_assinatura, confirmacao, etc.).
        "controlador_ajax.php",
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

    TIPOS_IGNORADOS = ("submit", "button", "image", "checkbox", "radio", "file")

    ARQUIVO_AUDITORIA_ESCRITA = "log_auditoria_escrita.txt"

    def __init__(self, usuario, senha, unidade_alvo=None):
        super().__init__(usuario, senha)
        self.unidade_alvo = unidade_alvo or UNIDADE_ALVO
        self.salvar_telas = False

    @classmethod
    def _registrar_auditoria_escrita(cls, linha):
        try:
            with open(cls.ARQUIVO_AUDITORIA_ESCRITA, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {linha}\n")
        except OSError:
            pass

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
        url = InclusaoDocumentoSEI._limpar_url(url) or ""
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
            elif (tag.get("type") or "text").lower() not in InclusaoDocumentoSEI.TIPOS_IGNORADOS:
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
        return form, InclusaoDocumentoSEI._extrair_hidden_inputs(form)

    @staticmethod
    def _atribuir_campo(payload, candidatos, valor):
        """
        Sobrescreve o campo de negocio na 1a chave presente no payload (a nomenclatura
        varia entre versoes do SEI); se nenhuma existir, insere a 1a candidata.
        """
        chave = next((nome for nome in candidatos if nome in payload), candidatos[0])
        payload[chave] = str(valor)
        return chave

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
                    return InclusaoDocumentoSEI._limpar_url(resposta.headers[chave])
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
        m_id = re.search(r"[?&]id_procedimento=(\d+)", url)
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

    def trocar_unidade(self):
        """Igual ao app3, mas com a unidade alvo configuravel (--unidade)."""
        if self.unidade_atual == self.unidade_alvo:
            return True
        url = self._link_trocar_unidade(self.html_home)
        if not url:
            # Sem o link na tela nao ha infra_hash legitimo: em vez de chutar um
            # valor (regra de ouro), procuramos o hash de qualquer link da propria
            # pagina. Sem hash, o proprio SEI recusa - e o operador ve o motivo.
            m_hash = re.search(r"infra_hash=([0-9a-f]{32,})", self.html_home or "")
            if not m_hash:
                print(
                    "  Nao encontrei o link de troca nem um infra_hash na tela inicial; "
                    "a troca de unidade nao pode ser feita com seguranca."
                )
                return False
            print("  Link de troca ausente; montando a URL com o hash da tela inicial.")
            url = (
                "controlador.php?acao=infra_trocar_unidade&"
                "acao_origem=infra_trocar_unidade&infra_sistema=100000100&"
                f"infra_unidade_atual={self.unidade_alvo}&"
                f"infra_hash={m_hash.group(1)}"
            )
        if not url.startswith("http"):
            url = BASE_SEI + url
        print(f"Trocando para a unidade {self.unidade_alvo}...")
        try:
            pagina_troca = self._requisicao_segura("get", url, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"  GET da tela de troca falhou: {exc}")
            return False
        action, payload = self._montar_payload_troca(pagina_troca.text, self.unidade_alvo)
        if not action:
            print("  Formulario de troca de unidade nao encontrado na resposta.")
            return False
        try:
            self._requisicao_segura("post", action, data=payload, allow_redirects=False, timeout=30)
        except UnicodeDecodeError:
            pass
        except requests.exceptions.RequestException as exc:
            print(f"  POST da troca falhou: {exc}")
            return False
        self.unidade_atual = self.unidade_alvo
        print("  Comando de troca de unidade enviado.")
        return True

    def _localizar_processo_na_caixa(self, numero, limite_paginas=20):
        """Varre a caixa da unidade atual (somente leitura) e devolve (item, encontrado)."""
        from scripts_read.app3 import raspar_pagina_recebidos

        html = self.navegar_para_controle()
        if not html or not any(
            t in html for t in ("tblProcessosDetalhado", "tblProcessosRecebidos", "tblProcessos")
        ):
            raise ErroInclusaoDocumento(
                "Nao consegui carregar a caixa de processos da unidade atual.", etapa="localizar"
            )

        alvo = _limpar(numero or "").replace(" ", "")
        for pagina in range(1, limite_paginas + 1):
            itens, tem_proxima, action_url, form_data, _ = raspar_pagina_recebidos(html, pagina)
            for item in itens:
                if _limpar(item.get("Processo") or "").replace(" ", "") == alvo:
                    return item, True
            if not tem_proxima:
                print(f"  Fim da caixa na pagina {pagina}; processo nao encontrado.")
                break
            time.sleep(0.4)
            html = self._ir("post", action_url, data=form_data, timeout=30)[0].text
        return None, False

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

    @staticmethod
    def _link_tipo_na_arvore(resposta):
        """
        href de documento_escolher_tipo ("Incluir Documento") na arvore do processo.

        No SEI 5.x os nos vem dentro de strings JavaScript (Nos[N].acoes = '<a href=...>')
        e sao invisiveis ao parser DOM - dai o fallback por regex no HTML bruto. O href
        ja traz o infra_hash fresco do proprio no (regra de ouro: nunca inventar hash).
        """
        link = InclusaoDocumentoSEI._sopa(resposta).select_one(
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
        sopa = InclusaoDocumentoSEI._sopa(resposta)
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
                if tratamento == "texto" and bruto.isdigit() and coluna in ("MASP", "ADM", "Nº do termo"):
                    valor = bruto
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

    @staticmethod
    def _mensagens_erro(resposta):
        """
        Colhe as mensagens de validacao do SEI (barra de progresso, alert() etc.) para
        explicar por que a criacao nao redirecionou.
        """
        msgs = [
            tag.get_text(strip=True)
            for tag in InclusaoDocumentoSEI._sopa(resposta).select(
                ".infraBarraProgresso, .infraMensagemErro, #divInfraMsg, fieldset legend"
            )
        ]
        msgs += [
            m.group(1)
            for m in re.finditer(r"""alert\(['"]([^'"]{5,200})['"]\)""", resposta.text)
        ]
        unicas = list(dict.fromkeys(m for m in msgs if m))
        return (" | Mensagens: " + " ; ".join(unicas[:5])) if unicas else ""

    def incluir_documento_interno(
        self,
        id_procedimento,
        id_serie,
        id_hipotese_legal="4",
        texto_inicial="N",
        dados_adicionais=None,
        confirmar=False,
        dry_run=False,
        salvar_telas=False,
        protocolo_modelo=None,
        id_documento_modelo=None,
        consultar_lupa_modelo=True,
    ):
        """
        Cria um documento interno no processo informado.

        Parâmetros
        ----------
        id_procedimento   : id interno do processo (ex.: "000000000")
        id_serie          : id da série/tipo de documento (ex.: "63" = Relatório;
                            "4110" = Cessão Especial Ônus Cedente)
        id_hipotese_legal : id da hipótese legal (ex.: "4") do nível Restrito
        texto_inicial     : "N" = branco | "T" = texto padrão (ignorado p/ "D")
        dados_adicionais  : dict extra p/ sobrescrever/inserir campos de negócio
        confirmar         : obrigatório True para qualquer gravação real
        dry_run           : prepara o payload final e NÃO envia o POST de criação
        salvar_telas      : grava o HTML de cada etapa em .\\ruas_inclusao\\
        protocolo_modelo  : protocolo do documento usado como "Documento Modelo"
                            (ex.: "148410511"). Força rdoTextoInicial = "D", preenche
                            txtProtocoloDocumentoTextoBase e consulta a lupa
                            documento_modelo_selecionar (GET de leitura) atrás do id
                            interno do modelo.
        id_documento_modelo : id interno do modelo (hdnIdDocumentoTextoBase). Opcional:
                            quando vazio, é descoberto na lupa; quando a lupa não
                            devolve o par id+protocolo, o POST segue só com o protocolo.
        consultar_lupa_modelo : False injeta só o protocolo no payload, sem o GET de
                            leitura na lupa documento_modelo_selecionar (válvula de
                            escape caso essa consulta interfira na sessão/hash).

        Retorno
        -------
        str  — id_documento criado (ex.: "000000000"), quando grava de verdade.
        dict — payload montado, quando dry_run=True (nada é gravado).

        Erros
        -----
        ErroInclusaoDocumento com a etapa descritiva em qualquer falha.
        """
        if not confirmar and not dry_run:
            raise ErroInclusaoDocumento(
                "Operação de ESCRITA cancelada. Passe confirmar=True (ou use --confirmar).",
                etapa="confirmacao",
            )

        id_procedimento, id_serie = str(id_procedimento).strip(), str(id_serie).strip()
        self.salvar_telas = bool(salvar_telas)
        modo = "DRY-RUN (simulacao)" if dry_run else "GRAVACAO"
        detalhe_modelo = f", documento modelo {protocolo_modelo}" if protocolo_modelo else ""
        self._registrar_auditoria_escrita(
            f"[{modo}] serie {id_serie} no processo {id_procedimento} "
            f"(hipotese legal {id_hipotese_legal}, "
            f"texto inicial {self._texto_inicial_efetivo(texto_inicial, protocolo_modelo)}"
            f"{detalhe_modelo})."
        )

        ids = {id_procedimento}

        link_proc = self._link_trabalhar_na_caixa(id_procedimento)
        if not link_proc:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado na caixa da unidade.",
                etapa="0_abrir_processo",
            )
        resp_proc, _ = self._ir("get", link_proc, ids_liberados=ids, timeout=30)
        self._salvar_tela("0_processo_aberto", resp_proc)

        iframe = self._sopa(resp_proc).select_one("iframe#ifrArvore")
        src_arvore = (iframe.get("src") if iframe is not None else "") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore (permissao ou layout diferente).",
                etapa="0_abrir_processo",
            )
        resp_arvore, _ = self._ir("get", src_arvore, ids_liberados=ids, timeout=30)
        self._salvar_tela("1_arvore", resp_arvore)
        href_tipo = self._link_tipo_na_arvore(resp_arvore)
        if not href_tipo:
            raise ErroInclusaoDocumento(
                "Botao 'Incluir Documento' (documento_escolher_tipo) nao encontrado na arvore. "
                "Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )

        url_tipo = self._normalizar_url(href_tipo)
        resp_tipo, _ = self._ir("get", url_tipo, ids_liberados=ids, timeout=30)
        form_tipo, campos_tipo = self._form_e_payload(
            self._sopa(resp_tipo),
            ids=("frmDocumentoEscolherTipo", "frmInfraSelecaoSerie"),
            campos=("id_serie", "selSerie", "txtSerie"),
        )
        if form_tipo is None:
            raise ErroInclusaoDocumento(
                "Tela de escolha de tipo nao expoe o formulario de series "
                "(id_serie/selSerie). Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )
        self._salvar_tela("1_escolher_tipo", resp_tipo)
        action_escolher = self._action(form_tipo, base_url=url_tipo)

        payload = {c: v for c, v in campos_tipo.items() if not c.startswith("chkInfraItem")}
        self._atribuir_campo(payload, ("id_serie", "selSerie", "hdnIdSerie"), id_serie)
        resp_escolha, loc_gerar = self._ir(
            "post", action_escolher, data=payload, ids_liberados=ids,
            allow_redirects=False, timeout=30,
        )

        if resp_escolha.status_code == 200:
            resp_gerar = resp_escolha
        elif resp_escolha.status_code in REDIRECIONAMENTOS:
            if not loc_gerar:
                raise ErroInclusaoDocumento(
                    "escolher_tipo respondeu sem header Location.", etapa="1_escolher_tipo"
                )
            url_gerar = self._normalizar_url(loc_gerar)
            if "documento_gerar" not in url_gerar.lower():
                raise ErroInclusaoDocumento(
                    f"Location inesperado (nao aponta para documento_gerar): {url_gerar}",
                    etapa="2_tela_gerar",
                )
            resp_gerar, _ = self._ir("get", url_gerar, ids_liberados=ids, timeout=30)
        else:
            raise ErroInclusaoDocumento(
                f"Escolha do tipo nao redirecionou para documento_gerar "
                f"(HTTP {resp_escolha.status_code}).",
                etapa="1_escolher_tipo",
            )
        self._salvar_tela("2_documento_gerar", resp_gerar)

        sopa_gerar = self._sopa(resp_gerar)
        modelo = {"protocolo": "", "id_documento": ""}
        if protocolo_modelo:
            modelo = self._resolver_documento_modelo(
                sopa_gerar,
                protocolo_modelo,
                id_documento_modelo,
                ids,
                consultar_lupa=consultar_lupa_modelo,
            )
        form_gerar, payload = self._payload_gerar(
            sopa_gerar,
            id_hipotese_legal,
            texto_inicial,
            dados_adicionais,
            protocolo_modelo=modelo["protocolo"],
            id_documento_modelo=modelo["id_documento"],
        )
        if not payload:
            raise ErroInclusaoDocumento(
                f"Formulário frmDocumentoGerar não encontrado ou sem campos "
                f"(HTTP {resp_gerar.status_code}) — a tela de geração pode ter carregado "
                "uma mensagem de erro (use --salvar-telas para inspecionar).",
                etapa="3_payload_gerar",
            )
        action_gerar = self._action(form_gerar, base_url=action_escolher)
        if not action_gerar:
            raise ErroInclusaoDocumento(
                "action do frmDocumentoGerar não localizado.", etapa="3_payload_gerar"
            )

        if protocolo_modelo:
            print(
                "  Documento Modelo: rdoTextoInicial = "
                f"{payload.get('rdoTextoInicial', '')}, "
                f"txtProtocoloDocumentoTextoBase = "
                f"{payload.get('txtProtocoloDocumentoTextoBase', '')}, "
                f"hdnIdDocumentoTextoBase = '{payload.get('hdnIdDocumentoTextoBase', '')}'"
            )
        if dry_run:
            print("\n[Dry-run] POST documento_gerar seria enviado para:")
            print("  ", action_gerar)
            print("[Dry-run] Payload (campos relevantes):")
            for chave in sorted(payload):
                valor = str(payload[chave])
                print(f"  {chave} = {valor[:120]}{'...' if len(valor) > 120 else ''}")

            return payload

        resp_final, loc_final = self._ir(
            "post", action_gerar, data=payload, ids_liberados=ids,
            allow_redirects=False, timeout=30,
        )
        if resp_final.status_code not in REDIRECIONAMENTOS:
            self._salvar_tela("3_erro_gerar", resp_final, forcar=True)
            raise ErroInclusaoDocumento(
                f"documento_gerar não redirecionou após criar "
                f"(HTTP {resp_final.status_code}).{self._mensagens_erro(resp_final)}",
                etapa="4_capturar_id",
            )
        if not loc_final:
            raise ErroInclusaoDocumento(
                f"documento_gerar respondeu {resp_final.status_code} sem header Location.",
                etapa="4_capturar_id",
            )
        m = re.search(r"[?&]id_documento=(\d+)", loc_final) or re.search(
            r"id_documento=(\d+)", loc_final, flags=re.I
        )
        if not m:
            raise ErroInclusaoDocumento(
                f"Não foi possível extrair id_documento do Location: {loc_final}",
                etapa="4_capturar_id",
            )
        self._registrar_auditoria_escrita(
            f"[SUCESSO] id_documento criado = {m.group(1)} ({loc_final})"
        )
        return m.group(1)

    def editar_conteudo_documento(self, id_procedimento, id_documento, dados):
        """
        Mescla os dados da planilha no conteudo do documento e SALVA no SEI (mail merge).

        Reproduz a cadeia capturada no Network do clique no lapis (Editar Conteudo),
        sem hardcode de hash nem de campo dinamico (regra de ouro):
          1. arvore do processo (ja visualizado na inclusao);
          2. href do NO do documento -> arvore "com o documento selecionado", onde o
             lapis e as URLs do editor sao renderizados (com hash fresco);
          3. ajax documento_verificar_assinatura (GET de leitura, como o browser);
          4. editor_montar -> TODOS os textarea txaEditor_XXXXX (cabecalho, corpo,
             rodape...), hidden inputs (infra_hash) e a action do form;
          5. merge por REGRAS_MERGE em cada parte, com contador por regra;
          6. ajax corfirmacao_atualizacao_documento (confirmacao antes de salvar);
          7. POST em editor/editor_processar.php?acao=editor_salvar (confirma "OK").

        dados: {coluna da planilha: valor} (ver COLUNAS_MERGE).
        Retorna True quando o SEI confirma o salvamento; falhas viram ErroInclusaoDocumento.
        """
        id_procedimento = str(id_procedimento).strip()
        id_documento = str(id_documento).strip()
        dados = dados or {}
        if not id_procedimento or not id_documento:
            raise ErroInclusaoDocumento(
                "id_procedimento e id_documento sao obrigatorios para editar o conteudo.",
                etapa="5_editor_montar",
            )
        if not dados:
            raise ErroInclusaoDocumento(
                "dados da planilha vazios - nada a mesclar no documento.",
                etapa="5_editor_montar",
            )
        ids = {id_procedimento}
        self._registrar_auditoria_escrita(
            f"[EDITOR] mail merge no documento {id_documento} do processo "
            f"{id_procedimento} com {dados}."
        )

        # 1) Arvore do processo (ja visualizado na inclusao).
        link_proc = self._link_trabalhar_na_caixa(id_procedimento)
        if not link_proc:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado na caixa da unidade.",
                etapa="5_editor_montar",
            )
        resp_proc, _ = self._ir("get", link_proc, ids_liberados=ids, timeout=30)
        iframe = self._sopa(resp_proc).select_one("iframe#ifrArvore")
        src_arvore = (iframe.get("src") if iframe is not None else "") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore (permissao ou layout diferente).",
                etapa="5_editor_montar",
            )
        resp_arvore, _ = self._ir("get", src_arvore, ids_liberados=ids, timeout=30)
        self._salvar_tela("4a_arvore_editor", resp_arvore)

        # 2) Arvore COM o documento selecionado: o no traz
        # acao=arvore_visualizar&...&id_documento=<alvo>&infra_hash=<fresh>, e e
        # nessa variante que o SEI renderiza o link do editor.
        href_no = self._link_editor_na_arvore(
            resp_arvore, id_documento, acoes=("arvore_visualizar",)
        )
        resp_alvo = resp_arvore
        if href_no:
            resp_alvo, _ = self._ir("get", href_no, ids_liberados=ids, timeout=30)
            self._salvar_tela("4b_arvore_documento", resp_alvo)
        else:
            print(
                "  Aviso: link do no do documento nao encontrado na arvore; "
                "seguindo com a tela atual."
            )

        # 3) Verificacao de assinatura (GET de leitura): o browser so abre o editor
        # depois dela; aqui e fidelidade de fluxo (nao grava nada).
        url_assinatura = self._link_ajax_editor(
            resp_alvo, "documento_verificar_assinatura", id_documento
        )
        texto_assinatura = ""
        if url_assinatura:
            resp_assinatura, _ = self._ir("get", url_assinatura, ids_liberados=ids, timeout=30)
            texto_assinatura = (resp_assinatura.text or "").strip()
            print(
                f"  Verificacao de assinatura: HTTP {resp_assinatura.status_code} "
                f"({texto_assinatura[:60]!r})"
            )
        else:
            print("  Aviso: ajax de verificacao de assinatura nao encontrado; seguindo.")

        # 4) editor_montar: primeiro o link renderizado na propria tela; se nao
        # houver, ele pode vir na resposta do ajax acima.
        href_montar = self._link_editor_na_arvore(
            resp_alvo, id_documento, acoes=("editor_montar",)
        )
        if not href_montar and "editor_montar" in texto_assinatura:
            achado = re.search(
                r"""(controlador\.php\?acao=editor_montar[^"'<\s]+)""", texto_assinatura
            )
            href_montar = achado.group(1) if achado else ""
        if not href_montar:
            raise ErroInclusaoDocumento(
                f"editor_montar do documento {id_documento} nao localizado (nem na "
                "arvore selecionada, nem na resposta do ajax) - rode com "
                "--salvar-telas e inspecione 4b_arvore_documento_*.html.",
                etapa="5_editor_montar",
            )
        resp_editor, _ = self._ir("get", href_montar, ids_liberados=ids, timeout=30)
        self._salvar_tela("4_editor_montar", resp_editor)
        partes, form = self._todos_editores(resp_editor)
        if not partes or form is None:
            raise ErroInclusaoDocumento(
                "Textarea txaEditor_* ou form de salvamento ausentes na tela do editor "
                f"(HTTP {resp_editor.status_code}; use --salvar-telas).",
                etapa="5_editor_montar",
            )
        print(f"  Editor: {len(partes)} parte(s) -> {', '.join(partes)}")

        action_editor = self._normalizar_url(self._limpar_url(form.get("action")) or "")
        if "editor_processar.php" not in action_editor:
            m_acao = re.search(
                r"""action=["']([^"']*editor_processar\.php[^"']*)["']""",
                resp_editor.text or "",
                flags=re.I,
            )
            action_editor = self._normalizar_url(m_acao.group(1)) if m_acao else ""
        if "editor_processar.php" not in action_editor:
            raise ErroInclusaoDocumento(
                "action do form do editor (editor/editor_processar.php) nao exposta - "
                "nada foi salvo (use --salvar-telas).",
                etapa="6_editor_salvar",
            )

        # 5) Merge em CADA parte do documento (o corpo do texto e a parte editavel).
        payload = self._extrair_hidden_inputs(form)
        contador = {}
        for nome, html in partes.items():
            html_mesclado, contagem = self._mesclar_html(html, dados)
            for chave, qtde in contagem.items():
                contador[chave] = contador.get(chave, 0) + qtde
            payload[nome] = html_mesclado
        print("  Merge:")
        for chave, _coluna, _padrao, _grupo, _trat in REGRAS_MERGE:
            qtde = contador.get(chave, 0)
            print(f"    {chave}: {qtde} substituicao(oes)")
            if not qtde:
                print("      AVISO: marcador nao encontrado no modelo.")
        texto_final = " ".join(payload[nome] for nome in partes)
        remanescentes = [m for m in MARCADORES_DO_MODELO if m in texto_final]
        if remanescentes:
            raise ErroInclusaoDocumento(
                f"Marcadores do modelo ainda presentes apos o merge: {remanescentes} "
                "- nada foi salvo.",
                etapa="6_editor_salvar",
            )

        # 6) Confirmacao antes de salvar (o editor consulta esse ajax e so submete
        # depois; se ele falhar, o browser tambem aborta).
        url_confirmacao = self._link_ajax_editor(
            resp_editor, "corfirmacao_atualizacao_documento", id_documento
        )
        if url_confirmacao:
            resp_conf, _ = self._ir("get", url_confirmacao, ids_liberados=ids, timeout=30)
            if resp_conf.status_code != 200:
                raise ErroInclusaoDocumento(
                    f"confirmacao de salvamento falhou (HTTP {resp_conf.status_code}) "
                    "- nada foi salvo.",
                    etapa="6_editor_salvar",
                )
            print(f"  Confirmacao: {(resp_conf.text or '').strip()[:120]!r}")
        else:
            print("  Aviso: ajax de confirmacao nao encontrado; seguindo para o POST.")

        # 7) POST editor_salvar (action lida do form; sem auto-redirect p/ auditar).
        resp_salvar, loc_salvar = self._ir(
            "post",
            action_editor,
            data=payload,
            ids_liberados=ids,
            allow_redirects=False,
            timeout=90,
        )
        self._salvar_tela("5_editor_salvar", resp_salvar)
        if resp_salvar.status_code in REDIRECIONAMENTOS:
            self._registrar_auditoria_escrita(
                f"[SUCESSO-EDITOR] conteudo salvo no documento {id_documento} "
                f"(Location: {loc_salvar or '(sem Location)'})"
            )
            return True
        if resp_salvar.status_code == 200:
            resposta = (self._sopa(resp_salvar).get_text(" ", strip=True) or "").strip()
            if resposta.startswith("OK"):
                self._registrar_auditoria_escrita(
                    f"[SUCESSO-EDITOR] conteudo salvo no documento {id_documento} "
                    f"(versao {resposta[2:] or '?'})."
                )
                print(f"  SEI confirmou: {resposta[:20]!r}")
                return True
            if "INFRA_VALIDACAO" in resposta or "COMPARACAO" in resposta:
                raise ErroInclusaoDocumento(
                    f"o SEI recusou o salvamento: {resposta[:300]}",
                    etapa="6_editor_salvar",
                )
            pendencias = self._mensagens_erro(resp_salvar)
            raise ErroInclusaoDocumento(
                f"resposta inesperada do editor_salvar: {resposta[:200] or '(vazia)'}"
                f"{pendencias}",
                etapa="6_editor_salvar",
            )
        raise ErroInclusaoDocumento(
            f"editor_salvar falhou (HTTP {resp_salvar.status_code})."
            f"{self._mensagens_erro(resp_salvar)}",
            etapa="6_editor_salvar",
        )


def resolver_caminho_planilha(valor):
    """
    Resolve o caminho do Excel de mesclagem: valor informado em --planilha
    (relativo a raiz do projeto) -> nome padrao na raiz -> ~/Downloads (fallback).
    """
    if valor:
        caminho = valor if os.path.isabs(valor) else os.path.join(_root_, valor)
        if os.path.isfile(caminho):
            return caminho
        if os.path.basename(valor) != NOME_PLANILHA_PADRAO:
            sys.exit(f"Planilha nao encontrada: {caminho}")
    for candidato in (
        os.path.join(_root_, NOME_PLANILHA_PADRAO),
        os.path.join(os.path.expanduser("~"), "Downloads", NOME_PLANILHA_PADRAO),
    ):
        if os.path.isfile(candidato):
            return candidato
    sys.exit(
        f"Planilha '{NOME_PLANILHA_PADRAO}' nao encontrada na raiz do projeto nem em "
        "~/Downloads (ou informe outro caminho em --planilha)."
    )


def carregar_dados_planilha(caminho, indice=0):
    """
    Le o Excel e devolve {coluna: valor} da linha pedida (ver COLUNAS_MERGE).
    Aborta com sys.exit em coluna ausente, planilha vazia ou indice fora do
    intervalo.
    """
    df = pd.read_excel(caminho)
    faltantes = sorted({c for c in COLUNAS_MERGE if c not in df.columns})
    if faltantes:
        sys.exit(
            f"Colunas obrigatorias ausentes em {os.path.basename(caminho)}: {faltantes} "
            f"(colunas lidas: {list(df.columns)})."
        )
    if df.empty:
        sys.exit(f"Planilha sem linhas de dados: {caminho}")
    if not isinstance(indice, int) or indice < 0 or indice >= len(df):
        sys.exit(f"--linha {indice} fora do intervalo valido (0..{len(df) - 1}).")
    linha = df.iloc[indice]
    return {
        coluna: ("" if pd.isna(linha[coluna]) else str(linha[coluna]).strip())
        for coluna in COLUNAS_MERGE
    }


def carregar_planilha(caminho):
    """Le a planilha inteira e devolve a lista de linhas (uma por servidor)."""
    df = pd.read_excel(caminho)
    faltantes = sorted({c for c in COLUNAS_MERGE if c not in df.columns})
    if faltantes:
        sys.exit(
            f"Colunas obrigatorias ausentes em {os.path.basename(caminho)}: {faltantes} "
            f"(colunas lidas: {list(df.columns)})."
        )
    if df.empty:
        sys.exit(f"Planilha sem linhas de dados: {caminho}")
    linhas = []
    for indice, linha in df.iterrows():
        dados = {
            coluna: ("" if pd.isna(linha[coluna]) else str(linha[coluna]).strip())
            for coluna in COLUNAS_MERGE
        }
        processo = ""
        if "Processo SEI" in df.columns and not pd.isna(linha["Processo SEI"]):
            processo = str(linha["Processo SEI"]).strip()
        linhas.append({"indice": int(indice), "dados": dados, "processo": processo})
    return linhas


def _resolver_processo_da_linha(api, item, args, id_procedimento_padrao):
    """
    Devolve o id_procedimento a usar para a linha do lote.

    Prioridade: coluna "Processo SEI" da propria linha; se ela existir e diferir do
    processo da CLI, o processo e buscado na caixa (com a trava de NAO VISUALIZADO).
    Nenhum processo e adivinhado: sem coluna e sem --processo, aborta.
    """
    indice = item["indice"]
    processo_item = item["processo"]
    if not processo_item:
        if not args.processo:
            sys.exit(
                f"ABORTADO no lote (linha {indice}): a planilha nao tem 'Processo SEI' "
                "e nenhum --processo foi informado na CLI."
            )
        return id_procedimento_padrao
    if args.processo and processo_item == args.processo:
        return id_procedimento_padrao

    achado, encontrado = api._localizar_processo_na_caixa(processo_item)
    if not encontrado:
        sys.exit(
            f"ABORTADO no lote (linha {indice}): processo {processo_item} "
            "nao encontrado na caixa da unidade."
        )
    nao_visualizado = bool(achado.get("NaoVisualizado")) or str(
        achado.get("NaoVisualizado") or ""
    ).strip().lower() in ("true", "1", "sim", "verdadeiro")
    if nao_visualizado and not args.mesmo_assim:
        sys.exit(
            f"ABORTADO no lote (linha {indice}): processo {processo_item} "
            "NAO VISUALIZADO. Use --mesmo-assim se tiver certeza."
        )
    return achado.get("IdProtocolo") or achado.get("id_procedimento")


def _selecionar_linhas(itens, args):
    """Aplica --a-partir-de / --ate-linha sobre a lista de linhas da planilha."""
    inicio = args.a_partir_de if args.a_partir_de is not None else args.linha
    selecionados = [item for item in itens if item["indice"] >= inicio]
    if args.ate_linha is not None:
        selecionados = [item for item in selecionados if item["indice"] <= args.ate_linha]
    if not selecionados:
        sys.exit(
            f"Nenhuma linha no intervalo pedido (inicio={inicio}, "
            f"fim={args.ate_linha if args.ate_linha is not None else 'ultima'}); "
            f"a planilha tem {len(itens)} linha(s)."
        )
    return selecionados


def executar_lote(api, args, id_procedimento_padrao, texto_inicial):
    """
    Modo lote: cria um documento por linha da planilha e aplica o Mail Merge em
    cada um. Falha para no primeiro erro e imprime a linha exata para retomada
    (--a-partir-de).
    """
    caminho = resolver_caminho_planilha(args.planilha)
    itens = _selecionar_linhas(carregar_planilha(caminho), args)

    print(f"\nMODO LOTE: {len(itens)} linha(s) de {os.path.basename(caminho)}")
    print(f"  Intervalo: indice {itens[0]['indice']} .. {itens[-1]['indice']}")
    for item in itens:
        d = item["dados"]
        print(
            f"    [{item['indice']}] termo {d.get('Nº do termo', '?')} - "
            f"{d.get('Nome Servidor', '')} (MASP {d.get('MASP', '')})"
        )
    print("\nATENCAO: cada linha cria UM documento novo e grava o conteudo mesclado.")
    print("Para retomar apos uma falha: --a-partir-de <indice>.\n")

    concluidas = []
    for posicao, item in enumerate(itens, start=1):
        indice = item["indice"]
        dados = item["dados"]
        nome = dados.get("Nome Servidor", "")
        print("=" * 78)
        print(
            f"LOTE [{posicao}/{len(itens)}] linha {indice} - "
            f"termo {dados.get('Nº do termo', '?')} - {nome}"
        )
        print("=" * 78)

        id_procedimento = _resolver_processo_da_linha(api, item, args, id_procedimento_padrao)
        try:
            id_doc = api.incluir_documento_interno(
                id_procedimento=id_procedimento,
                id_serie=args.serie,
                id_hipotese_legal=args.hipotese_legal,
                texto_inicial=texto_inicial,
                protocolo_modelo=args.protocolo_modelo,
                id_documento_modelo=args.id_documento_modelo,
                consultar_lupa_modelo=not args.sem_consultar_modelo,
                confirmar=args.confirmar,
                dry_run=args.dry_run,
                salvar_telas=args.salvar_telas,
            )
            if not api.editar_conteudo_documento(id_procedimento, id_doc, dados):
                raise ErroInclusaoDocumento("SEI nao confirmou o salvamento.", etapa="lote")
        except SystemExit:
            raise
        except Exception as erro:  # noqa: BLE001 - fail-fast com diagnostico acionavel
            print(f"\n!! FALHA na linha {indice} ({nome}): {erro}")
            print("!! Se o documento chegou a ser criado, o id aparece acima do erro.")
            print(f"!! Para retomar: --a-partir-de {indice}")
            sys.exit(1)

        concluidas.append((indice, nome, id_doc))
        print(f"\n>> OK linha {indice}: documento {id_doc} criado e mesclado.\n")

    print("=" * 78)
    print("LOTE CONCLUIDO")
    print("=" * 78)
    for indice, nome, id_doc in concluidas:
        print(f"  linha {indice:>2} | documento {id_doc} | {nome}")
    print(f"\nTotal: {len(concluidas)} documento(s) criado(s) e mesclado(s).")


def main(argv=None):
    """CLI do fluxo de escrita: valida as travas, localiza o processo e inclui o documento."""
    import argparse

    parser = argparse.ArgumentParser(description="Inclusão de documento interno no SEI-MG.")
    parser.add_argument(
        "--id-procedimento", help="ID interno do processo (ex.: 000000000). Use OU --processo."
    )
    parser.add_argument(
        "--processo",
        help="Numero formatado (ex.: 0000.00.0000000/0000-00) - busca automatica na caixa da unidade",
    )
    parser.add_argument(
        "--unidade", default=UNIDADE_ALVO, help="ID da unidade onde o processo esta (padrao: 110007141)"
    )
    parser.add_argument(
        "--serie",
        default="63",
        help=(
            "ID da serie/tipo de documento (padrao 63 = Relatorio; "
            f"{SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE} = Cessao Especial Onus Cedente)"
        ),
    )
    parser.add_argument("--hipotese-legal", default="4", help="ID da hipotese legal do nivel Restrito (padrao 4)")
    parser.add_argument(
        "--texto-inicial",
        default=None,
        choices=["N", "T", "D"],
        help="N = branco (padrao) | T = texto padrao | D = documento modelo (usa --protocolo-modelo)",
    )
    parser.add_argument(
        "--protocolo-modelo",
        help=(
            "Protocolo do documento existente usado como 'Documento Modelo' "
            "(ex.: 148410511). Liga rdoTextoInicial=D e consulta a lupa "
            "documento_modelo_selecionar atras de hdnIdDocumentoTextoBase"
        ),
    )
    parser.add_argument(
        "--id-documento-modelo",
        help="Id interno do documento modelo (hdnIdDocumentoTextoBase), quando ja conhecido",
    )
    parser.add_argument(
        "--sem-consultar-modelo",
        action="store_true",
        help=(
            "Com --protocolo-modelo: nao faz o GET de leitura na lupa "
            "documento_modelo_selecionar; injeta apenas o protocolo no payload"
        ),
    )
    parser.add_argument(
        "--confirmar", action="store_true", help="Confirma e GRAVA o documento (obrigatorio para escrita real)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Prepara e exibe o payload, sem enviar o POST de criacao"
    )
    parser.add_argument("--salvar-telas", action="store_true", help="Salva o HTML de cada etapa em ruas_inclusao/")
    parser.add_argument(
        "--mesmo-assim", action="store_true", help="Prossegue mesmo se o processo estiver NAO VISUALIZADO"
    )
    parser.add_argument(
        "--planilha",
        nargs="?",
        const=NOME_PLANILHA_PADRAO,
        default=None,
        help=(
            "Excel de mesclagem (Mail Merge) com os dados do servidor. Sem valor "
            f"usa '{NOME_PLANILHA_PADRAO}' (raiz do projeto ou ~/Downloads). A "
            "edicao do conteudo ocorre apos a criacao real do documento."
        ),
    )
    parser.add_argument(
        "--linha",
        type=int,
        default=0,
        help="Indice da linha da planilha a mesclar (padrao 0 = primeira linha de dados)",
    )
    parser.add_argument(
        "--editar-documento",
        metavar="ID_DOCUMENTO",
        help=(
            "Aplica o Mail Merge em um documento JA criado, pulando a criacao "
            "(ex.: 900000001). Exige --planilha."
        ),
    )
    parser.add_argument(
        "--lote",
        action="store_true",
        help=(
            "Processa TODAS as linhas da planilha: cria um documento por servidor e "
            "aplica o Mail Merge em cada um. Falha para no primeiro erro."
        ),
    )
    parser.add_argument(
        "--ate-linha",
        type=int,
        default=None,
        help="Com --lote: processa da --linha ate este indice (exclusivo ou inclusivo).",
    )
    parser.add_argument(
        "--a-partir-de",
        type=int,
        default=None,
        help="Com --lote: indice inicial (para retomar apos uma falha).",
    )
    args = parser.parse_args(argv)

    if args.linha < 0:
        parser.error("--linha deve ser um indice >= 0.")
    if (args.ate_linha is not None or args.a_partir_de is not None) and not args.lote:
        parser.error("--ate-linha/--a-partir-de so fazem sentido com --lote.")
    if args.ate_linha is not None and args.a_partir_de is not None:
        if args.a_partir_de > args.ate_linha:
            parser.error("--a-partir-de nao pode ser maior que --ate-linha.")
    if args.lote and args.editar_documento:
        parser.error("--lote nao pode ser combinado com --editar-documento.")
    if args.lote and args.dry_run:
        parser.error("--lote exige gravacao real: remova --dry-run ou processe linha a linha.")
    if args.lote and not args.planilha:
        parser.error("--lote exige --planilha.")
    if args.editar_documento:
        if not re.fullmatch(r"\d+", str(args.editar_documento).strip()):
            parser.error("--editar-documento deve conter apenas digitos (ex.: 900000001).")
        if not args.planilha:
            parser.error("--editar-documento exige --planilha (sem planilha nao ha mesclagem).")

    if not args.dry_run and not args.confirmar:
        parser.error("A gravacao e real: informe --confirmar (ou use --dry-run para simular.)")
    if bool(args.id_procedimento) == bool(args.processo):
        parser.error("Informe --id-procedimento OU --processo (um deles, nao ambos/nenhum.)")
    if args.protocolo_modelo:
        args.protocolo_modelo = args.protocolo_modelo.strip()
        if not re.fullmatch(r"\d+", args.protocolo_modelo):
            parser.error("--protocolo-modelo deve conter apenas digitos (ex.: 148410511).")
        if args.texto_inicial and args.texto_inicial != "D":
            parser.error(
                f"--protocolo-modelo ja implica rdoTextoInicial=D; remova --texto-inicial {args.texto_inicial}."
            )
        if args.serie == parser.get_default("serie"):
            print(
                "Nota: o documento criado e do tipo informado em --serie; para Cessao "
                f"Especial Onus Cedente a serie e {SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE} "
                f"(--serie {SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE})."
            )
    elif args.id_documento_modelo:
        parser.error("--id-documento-modelo so faz sentido junto de --protocolo-modelo.")
    elif args.texto_inicial == "D":
        parser.error("--texto-inicial D exige --protocolo-modelo (nao ha modelo sem protocolo).")
    texto_inicial = args.texto_inicial or ("D" if args.protocolo_modelo else "N")

    print("Autenticando no SEI-MG...")
    api = InclusaoDocumentoSEI(USUARIO, SENHA, unidade_alvo=args.unidade)
    # Sem isto, --editar-documento nao salvaria telas: quem liga a flag hoje e o
    # incluir_documento_interno, que nesse modo nem chega a ser chamado.
    api.salvar_telas = bool(args.salvar_telas)
    if not api.autenticar():
        sys.exit("Falha no login. Verifique credenciais/rede.")
    api.trocar_unidade()

    id_procedimento = args.id_procedimento
    if args.processo:
        print(f"Procurando o processo {args.processo} na caixa da unidade {args.unidade}...")
        achado, encontrado = api._localizar_processo_na_caixa(args.processo)
        if not encontrado:
            sys.exit(f"Processo {args.processo} nao encontrado na caixa da unidade {args.unidade}.")
        id_procedimento = achado.get("IdProtocolo") or achado.get("id_procedimento")
        print("\nProcesso localizado na caixa:")
        for chave in CAMPOS_RESUMO_PROCESSO:
            print(f"    {chave}: {achado.get(chave, '')}")

        nao_visualizado = str(achado.get("NaoVisualizado") or "").strip().lower() in (
            "true",
            "1",
            "sim",
            "verdadeiro",
        ) or bool(achado.get("NaoVisualizado"))
        if nao_visualizado and not args.mesmo_assim:
            sys.exit(
                "ABORTADO: processo NAO VISUALIZADO - operacao de prazo sensivel. "
                "Se tiver certeza, rode com --mesmo-assim."
            )

    if args.lote:
        executar_lote(api, args, id_procedimento, texto_inicial)
        return

    if args.editar_documento:
        id_documento = str(args.editar_documento).strip()
        print(f"Mail Merge em documento existente: {id_documento} (criacao ignorada).")
    else:
        id_documento = api.incluir_documento_interno(
            id_procedimento=id_procedimento,
            id_serie=args.serie,
            id_hipotese_legal=args.hipotese_legal,
            texto_inicial=texto_inicial,
            protocolo_modelo=args.protocolo_modelo,
            id_documento_modelo=args.id_documento_modelo,
            consultar_lupa_modelo=not args.sem_consultar_modelo,
            confirmar=args.confirmar,
            dry_run=args.dry_run,
            salvar_telas=args.salvar_telas,
        )

    print()
    if args.dry_run:
        if args.planilha:
            caminho_planilha = resolver_caminho_planilha(args.planilha)
            dados_merge = carregar_dados_planilha(caminho_planilha, args.linha)
            print(f"[Dry-run] Mesclagem leria {caminho_planilha} (linha {args.linha}):")
            for coluna, valor in dados_merge.items():
                print(f"  {coluna} = {valor}")
            print("[Dry-run] a edicao do conteudo so ocorre em gravacao real (--confirmar).")
        print("Dry-run concluido - nenhum documento foi gravado.")
    else:
        if args.editar_documento:
            print(f"Documento existente: {id_documento}")
        else:
            print(f"Documento criado com sucesso! id_documento = {id_documento}")
        if args.planilha:
            caminho_planilha = resolver_caminho_planilha(args.planilha)
            dados_merge = carregar_dados_planilha(caminho_planilha, args.linha)
            print(f"Mail Merge: {caminho_planilha} (linha {args.linha}):")
            for coluna, valor in dados_merge.items():
                print(f"  {coluna} = {valor}")
            if api.editar_conteudo_documento(id_procedimento, id_documento, dados_merge):
                print("Conteudo do documento mesclado e salvo no SEI.")


if __name__ == "__main__":
    main()
