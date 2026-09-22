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

# Garante que scripts_read é importável via import from package
if _root_ not in sys.path:
    sys.path.insert(0, _root_)

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Herança da base de autenticação (login/troca de unidade). Nada do app3.py é alterado.
from scripts_read.app3 import (  # noqa: E402
    UNIDADE_ALVO,
    USUARIO,
    SENHA,
    BASE_SEI,
    VALUE_HDN_ACAO,
    VALUE_SEL_ORGAO,
    APIExtratorSEI,
    _limpar,
)

HOST_SEI = "https://www.sei.mg.gov.br"
PASTA_TELAS = "ruas_inclusao"


class ErroInclusaoDocumento(Exception):
    """Exceção descritiva do fluxo de inclusão de documento no SEI."""

    def __init__(self, mensagem, etapa=None):
        self.etapa = etapa or "geral"
        super().__init__(f"[{self.etapa}] {mensagem}")


class InclusaoDocumentoSEI(APIExtratorSEI):
    """
    Escrita de documentos no SEI-MG.

    Herda de APIExtratorSEI apenas autenticar()/trocar_unidade() (métodos que só
    tocam login/troca de unidade). Todas as requisições deste fluxo passam por
    _requisicao_escrita(), que possui whitelist/blacklist próprias de ESCRITA.
    """

    def __init__(self, usuario, senha, unidade_alvo=None):
        super().__init__(usuario, senha)
        self.unidade_alvo = unidade_alvo or UNIDADE_ALVO

    # ---- guardas dedicadas ao fluxo de escrita --------------------------------
    PADROES_ESCRITA = (
        "login.php",
        "inicializar.php",
        "acao=principal",
        "acao=infra_trocar_unidade",
        "acao=procedimento_controlar",
        "acao=documento_escolher_tipo",
        "acao=documento_gerar",
        "acao=arvore_visualizar",
        "acao=procedimento_trabalhar",
        "acao=procedimento_visualizar",
    )

    # Abertura de processo / leitura de documento continua PROIBIDA aqui (leitura é do app3).
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

    ARQUIVO_AUDITORIA_ESCRITA = "log_auditoria_escrita.txt"

    # ------------------------------- utilitários ------------------------------

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
        Limpa URL (especialmente header Location) contra o clássico mojibake do SEI-MG.

        O servidor frequentemente devolve localizações com bytes 'utf-8' lidos como
        latin-1 (ex.: 'Â§' no lugar de 'ç'), o que derruba o requests em
        UnicodeDecodeError. A correção 'latin-1 -> utf-8' só é aplicada quando o
        conteúdo for de fato mojibake (re-encode válido).
        """
        if url is None:
            return None
        if isinstance(url, bytes):
            url = url.decode("iso-8859-1")
        url = url.strip().strip('"').strip("'")
        if not url:
            return url
        try:
            url = url.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return url

    @staticmethod
    def _normalizar_url(url):
        """Converte action/Location relativos ou absolutos-de-caminho em URL completa."""
        url_limpa = InclusaoDocumentoSEI._limpar_url(url) or ""
        if url_limpa.startswith("http://") or url_limpa.startswith("https://"):
            return url_limpa
        if url_limpa.startswith("/"):
            return HOST_SEI + url_limpa
        return urljoin(BASE_SEI, url_limpa)


    @staticmethod
    def _sopa(resposta):
        """
        Constrói BeautifulSoup a partir dos BYTES da resposta, deixando o bs4
        respeitar o <meta charset> da página (evita mojibake do .text do requests).
        """
        if hasattr(resposta, "content"):
            return BeautifulSoup(resposta.content, "html.parser")
        return BeautifulSoup(resposta, "html.parser")

    @staticmethod
    def _extrair_hidden_inputs(form):
        """
        Colhe todos os campos enviaveis de um formulario (hidden, text, textarea e
        select), como um submit padrao faria. E daqui que saem todos os infra_hash
        e IDs fresquinhos da tela (regra de ouro: nunca inventar/prever hash).
        """
        payload = {}
        if form is None:
            return payload
        for tag in form.find_all(["input", "select", "textarea"]):
            nome = tag.get("name")
            if not nome:
                continue
            if tag.name == "select":
                opcao = tag.find("option", selected=True) or tag.find("option")
                payload[nome] = opcao.get("value", "") if opcao is not None else ""
                continue
            if tag.name == "textarea":
                valor = tag.get_text() or ""
            else:  # <input>
                tipo = (tag.get("type") or "text").lower()
                if tipo in ("submit", "button", "image", "checkbox", "radio", "file"):
                    continue
                valor = tag.get("value", "")
            payload[nome] = valor if valor is not None else ""
        return payload

    @staticmethod
    def _encontrar_form(sopa, ids_forms=(), nomes_campos=()):
        for identif in ids_forms:
            form = sopa.find("form", id=identif)
            if form is not None:
                return form
        for nome in nomes_campos:
            for form in sopa.find_all("form"):
                if (
                    form.find("input", {"name": nome})
                    or form.find("select", {"name": nome})
                    or form.find("textarea", {"name": nome})
                ):
                    return form
        return None

    @staticmethod
    def _atribuir_campo(payload, candidatos, valor):
        """
        Sobrescreve o campo de negócio usando a primeira chave presente no payload
        (a nomenclatura varia entre versões do SEI). Se nenhuma existir, insere a
        primeira chave candidata.
        """
        for chave in candidatos:
            if chave in payload:
                payload[chave] = str(valor)
                return chave
        payload[candidatos[0]] = str(valor)
        return candidatos[0]


    # ----------------------------- requisição (escrita) -----------------------



    def trocar_unidade(self):
        """Igual ao app3, mas com a unidade alvo configuravel (ex.: RHMAISSIMPLES)."""
        if self.unidade_atual == self.unidade_alvo:
            return True
        url_dinamica = self._link_trocar_unidade(self.html_home)
        if not url_dinamica:
            url_dinamica = (
                "controlador.php?acao=infra_trocar_unidade&"
                "acao_origem=infra_trocar_unidade&infra_sistema=100000100&"
                "infra_unidade_atual=" + self.unidade_alvo + "&"
                "infra_hash=c9e91b2dc924e6ed02cb49ce1a4a58fcf4cfbd3d49a4960b9ed924f1a"
            )
        if not url_dinamica.startswith("http"):
            url_dinamica = BASE_SEI + url_dinamica
        print(f"Trocando para a unidade {self.unidade_alvo}...")
        try:
            pagina_troca = self._requisicao_segura("get", url_dinamica, timeout=30)
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

    def _montar_payload_troca(self, html, unidade):
        sopa = BeautifulSoup(html, "html.parser")
        form = sopa.find("form", {"id": "frmInfraSelecaoUnidade"})
        if form is None:
            for f in sopa.find_all("form"):
                if f.find("input", {"name": "hdnInfraItens"}) or f.find("input", {"name": "chkInfraItem"}):
                    form = f
                    break
        if form is None:
            return "", {}
        action = form.get("action", "")
        if not action.startswith("http"):
            action = BASE_SEI + action
        payload = {}
        for inp in form.find_all("input"):
            nome = inp.get("name")
            if not nome:
                continue
            tipo = (inp.get("type") or "text").lower()
            if tipo == "radio":
                if inp.get("value") == unidade:
                    payload[nome] = unidade
            elif tipo == "checkbox":
                if inp.has_attr("checked"):
                    payload[nome] = inp.get("value", "on")
            else:
                payload[nome] = inp.get("value", "")
        for sel in form.find_all("select"):
            nome = sel.get("name")
            if not nome:
                continue
            opcao = sel.find("option", selected=True) or sel.find("option")
            payload[nome] = opcao.get("value", "") if opcao else ""
        disponiveis = [ck.get("value", "") for ck in form.find_all("input", {"name": "chkInfraItem"})]
        if unidade not in disponiveis:
            print(f"  Unidade {unidade} nao esta disponivel em {disponiveis}.")
            return "", {}
        payload["selInfraUnidades"] = unidade
        payload["chkInfraItem"] = unidade
        payload["hdnInfraNroItens"] = str(len(disponiveis))
        return action, payload

    def _localizar_processo_na_caixa(self, numero, limite_paginas=20):
        """Varre a caixa de processos da unidade atual e acha o processo pelo numero."""
        from scripts_read.app3 import raspar_pagina_recebidos

        html = self.navegar_para_controle()
        if not html or not any(t in html for t in ("tblProcessosDetalhado", "tblProcessosRecebidos", "tblProcessos")):
            raise ErroInclusaoDocumento("Nao consegui carregar a caixa de processos da unidade atual.", etapa="localizar")

        alvo = _limpar(re.sub(r"\s+", " ", (numero or "").replace("\u200b", ""))).strip()
        pagina = 1
        while pagina <= limite_paginas:
            itens, tem_proxima, action_url, form_data, total = raspar_pagina_recebidos(html, pagina)
            for item in itens:
                proc = _limpar(item.get("Processo") or "")
                if proc == alvo or proc.replace(" ", "") == alvo.replace(" ", ""):
                    return item, True
            if not tem_proxima:
                print(f"  Fim da caixa na pagina {pagina}; processo nao encontrado.")
                break
            time.sleep(0.4)
            resposta = self._requisicao_escrita("post", action_url, data=form_data, timeout=30)
            html = resposta.text
            pagina += 1
        return None, False
    def _requisicao_escrita(self, metodo, url, ids_liberados=None, **kwargs):
        """Camada de rede do fluxo de escrita (whitelist/blacklist próprias).

        ids_liberados: conjunto de id_procedimento cuja ABERTURA (trabalhar/visualizar)
        é permitida — somente processos já VISUALIZADOS passam por aqui.
        """
        url_limpa = self._normalizar_url(url)
        url_low = url_limpa.lower()
        ids = set(ids_liberados or [])
        m_id = re.search(r"[?&]id_procedimento=(\d+)", url_limpa)
        id_url = m_id.group(1) if m_id else ""
        for token in self.TOKENS_PROIBIDOS_ESCRITA:
            if token in url_low:
                if token in ("procedimento_trabalhar", "procedimento_visualizar") and id_url in ids:
                    continue
                raise ErroInclusaoDocumento(
                    f"URL bloqueada na escrita (contém '{token}'): {url_limpa}", etapa="guardas"
                )
        if not any(padrao in url_limpa for padrao in self.PADROES_ESCRITA):
            raise ErroInclusaoDocumento(
                f"URL fora da whitelist de escrita: {url_limpa}", etapa="guardas"
            )

        self._registrar_auditoria_escrita(f"{metodo.upper()} {url_limpa}")
        if metodo.lower() == "get":
            resposta = self.session.get(url_limpa, **kwargs)
        else:
            resposta = self.session.post(url_limpa, **kwargs)

        loc = ""
        for chave in ("Location", "location", "LOCATION"):
            try:
                if chave in resposta.headers:
                    loc = self._limpar_url(resposta.headers[chave])
                    break
            except UnicodeDecodeError:
                continue
        if loc:
            self._registrar_auditoria_escrita(
                f"  <- status {resposta.status_code} | Location: {loc}"
            )
        else:
            self._registrar_auditoria_escrita(
                f"  <- status {resposta.status_code} | url_final: {resposta.url}"
            )
        return resposta

    # ------------------------- geração de hash/URL -----------------------------

    def _obter_hash_vigente(self):
        """Colhe um infra_hash fresco da própria sessão (nunca inventa)."""
        fontes = []
        try:
            res = self._requisicao_segura("get", BASE_SEI + "inicializar.php", timeout=30)
            fontes.append(res.text)
        except requests.exceptions.RequestException:
            pass
        if self.html_home:
            fontes.append(self.html_home)
        for html in fontes:
            if not html:
                continue
            m = re.search(r"infra_hash=([0-9a-fA-F]{16,128})", html, flags=re.I)
            if m:
                return m.group(1)
        return ""

    def _url_acao_processo(self, acao, id_procedimento):
        """Monta URL de uma ação vinculada a um processo usando o hash vigente da sessão."""
        from urllib.parse import urlencode

        hash_vigente = self._obter_hash_vigente()
        if not hash_vigente:
            raise ErroInclusaoDocumento(
                "Não foi possível obter um infra_hash válido da sessão "
                "(faça login/troca de unidade antes).",
                etapa="hash",
            )
        query = urlencode(
            {
                "acao": acao,
                "id_procedimento": str(id_procedimento),
                "infra_sistema": "100000100",
                "infra_unidade_atual": self.unidade_atual or UNIDADE_ALVO,
                "infra_hash": hash_vigente,
            }
        )
        return f"{BASE_SEI}controlador.php?{query}"


    # ------------------------- fluxo principal (4 passos) ----------------------

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
    ):
        """
        Cria um documento interno no processo informado.

        Parâmetros
        ----------
        id_procedimento      : id interno do processo (ex.: "000000000")
        id_serie             : id da série/tipo de documento (ex.: "63" = Relatório)
        id_hipotese_legal    : id da hipótese legal (ex.: "4") para nível Restrito
        texto_inicial        : "N" = branco | "T" = texto padrão
        dados_adicionais     : dict extra p/ sobrescrever/inserir campos de negócio
        confirmar            : obrigatório True para qualquer gravação real
        dry_run              : prepara o payload final e NÃO envia o POST de criação
        salvar_telas         : grava o HTML de cada etapa em .\\ruas_inclusao\\

        Retorno
        -------
        str  — id_documento criado (ex.: "000000000"), quando grava de verdade.
        dict — payload montado, quando dry_run=True (nada e gravado).

        Erros
        -----
        ErroInclusaoDocumento com etapa descritiva em qualquer falha.
        """
        if not confirmar and not dry_run:
            raise ErroInclusaoDocumento(
                "Operação de ESCRITA cancelada. Passe confirmar=True (ou use --confirmar).",
                etapa="confirmacao",
            )

        if dry_run:
            self._registrar_auditoria_escrita(
                f"[DRY-RUN] Inclusão simulada no processo {id_procedimento}, série {id_serie}."
            )
        else:
            self._registrar_auditoria_escrita(
                f"[GRAVAÇÃO] Incluindo série {id_serie} no processo {id_procedimento} "
                f"(hipótese legal {id_hipotese_legal})."
            )

        id_procedimento = str(id_procedimento).strip()
        id_serie = str(id_serie).strip()

        if salvar_telas:
            os.makedirs(PASTA_TELAS, exist_ok=True)

        # ----------------------------- PASSOS 1 e 2 ---------------------------
        pagina_gerar, action_escolher = self._passos_1_2_preparar(
            id_procedimento, id_serie, salvar_telas=salvar_telas
        )

        # ----------------------------- PASSO 3 --------------------------------
        payload = self._montar_payload_gerar(
            pagina_gerar,
            id_hipotese_legal=id_hipotese_legal,
            texto_inicial=texto_inicial,
            dados_adicionais=dados_adicionais or {},
        )
        if not payload:
            raise ErroInclusaoDocumento(
                "Formulário frmDocumentoGerar não encontrado ou sem campos — "
                "a tela de geração pode ter carregado uma mensagem de erro.",
                etapa="3_payload_gerar",
            )

        action_gerar = self._action_do_form(pagina_gerar, base_url=action_escolher)
        if not action_gerar:
            raise ErroInclusaoDocumento(
                "action do frmDocumentoGerar não localizado.", etapa="3_payload_gerar"
            )

        if dry_run:
            print("\n[Dry-run] POST documento_gerar seria enviado para:")
            print("  ", action_gerar)
            print("[Dry-run] Payload (campos relevantes):")
            for chave in sorted(payload):
                valor = payload[chave]
                if len(str(valor)) > 120:
                    valor = str(valor)[:120] + "..."
                print(f"  {chave} = {valor}")
            # Dry-run devolve o PAYLOAD montado (e nao o id_documento), permitindo
            # inspecionar/validar todos os campos antes de qualquer gravacao real.
            return payload

        resp_final = self._requisicao_escrita(
            "post", action_gerar, data=payload, allow_redirects=False, timeout=30
        )
        if resp_final.status_code not in (301, 302, 303, 307, 308):
            # Servidor devolveu a propria tela (200) — provavel validacao invalida.
            # Salva a resposta e extrai a mensagem de erro para diagnostico.
            self._salvar_tela("3_erro_gerar", resp_final)
            sopa_erro = self._sopa(resp_final)
            msgs = []
            for tag in sopa_erro.select(".infraBarraProgresso, .infraMensagemErro, #divInfraMsg, fieldset legend"):
                t = tag.get_text(strip=True)
                if t and t not in msgs:
                    msgs.append(t)
            for script_m in re.finditer(r"alert\(['\"]([^'\"]{5,200})['\"]\)", resp_final.text):
                if script_m.group(1) not in msgs:
                    msgs.append(script_m.group(1))
            detalhe = (" | Mensagens: " + " ; ".join(msgs[:5])) if msgs else ""
            raise ErroInclusaoDocumento(
                f"documento_gerar não redirecionou após criar (HTTP {resp_final.status_code}).{detalhe}",
                etapa="4_capturar_id",
            )

        # ----------------------------- PASSO 4 --------------------------------
        loc_final = ""
        for chave in ("Location", "location", "LOCATION"):
            try:
                if chave in resp_final.headers:
                    loc_final = self._limpar_url(resp_final.headers[chave])
                    break
            except UnicodeDecodeError:
                continue
        if not loc_final:
            raise ErroInclusaoDocumento(
                f"documento_gerar respondeu {resp_final.status_code} sem header Location.",
                etapa="4_capturar_id",
            )

        m = re.search(r"[?&]id_documento=(\d+)", loc_final)
        if not m:
            m = re.search(r"(?:id_documento|IdDocumento)=(\d+)", loc_final, flags=re.I)
        if not m:
            raise ErroInclusaoDocumento(
                f"Não foi possível extrair id_documento do Location: {loc_final}",
                etapa="4_capturar_id",
            )

        id_documento = m.group(1)
        self._registrar_auditoria_escrita(
            f"[SUCESSO] id_documento criado = {id_documento} ({loc_final})"
        )
        return id_documento


    # ------------------------- etapas auxiliares do fluxo ---------------------



    def _link_trabalhar_na_caixa(self, id_procedimento):
        """Localiza o href de procedimento_trabalhar do processo na caixa da unidade."""
        html = self.navegar_para_controle()
        if not html:
            return ""
        sopa = BeautifulSoup(html, "html.parser")
        alvo = f"id_procedimento={id_procedimento}"
        for a in sopa.find_all("a", href=True):
            href = a.get("href") or ""
            if "procedimento_trabalhar" in href and alvo in href:
                return href
        return ""

    def _passos_1_2_preparar(self, id_procedimento, id_serie, salvar_telas=False):
        """
        Fluxo real do SEI-MG para inclusao de documento interno:
          0) abre o processo (gate: somente processo JA VISUALIZADO, via ids_liberados)
          1) lê o iframe ifrArvore (procedimento_visualizar) -> arvore do processo
          2) acha o botão 'Incluir Documento' (documento_escolher_tipo) dentro da arvore
          3) GET escolher_tipo -> POST (hidden + id_serie) -> 302 para documento_gerar
          4) GET documento_gerar -> devolve o html da tela
        Retorna (html_pagina_gerar, url_action_escolher).
        """
        ids = {str(id_procedimento)}
        etapa = "0_abrir_processo"

        # 0) link de abertura retirado da propria caixa (hash valido da tela)
        link_trab = self._link_trabalhar_na_caixa(id_procedimento)
        if not link_trab:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado na caixa da unidade.",
                etapa=etapa,
            )
        resp_trab = self._requisicao_escrita(
            "get", self._normalizar_url(link_trab), ids_liberados=ids, timeout=30
        )
        if salvar_telas:
            self._salvar_tela("0_processo_aberto", resp_trab)

        # 1) iframe da arvore do processo
        sopa_trab = self._sopa(resp_trab)
        iframe = sopa_trab.find("iframe", id="ifrArvore")
        if iframe is None:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore (permissao ou layout diferente).",
                etapa=etapa,
            )
        src_arvore = iframe.get("src") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento("iframe ifrArvore sem src.", etapa=etapa)
        resp_arvore = self._requisicao_escrita(
            "get", self._normalizar_url(src_arvore), ids_liberados=ids, timeout=30
        )
        if salvar_telas:
            self._salvar_tela("1_arvore", resp_arvore)

        # 2) link Incluir Documento dentro da arvore
        sopa_arv = self._sopa(resp_arvore)
        link_tipo = sopa_arv.find("a", href=re.compile(r"documento_escolher_tipo"))
        href_tipo = (link_tipo.get("href") or "") if link_tipo is not None else ""
        if not href_tipo:
            # SEI-MG 5.x: os nos da arvore ficam em strings JS
            # ("Nos[N].acoes = '<a href="..."]" ...>') e sao invisiveis ao parser DOM.
            # Fallback: extrair o href direto do HTML bruto — ele ja vem com o
            # infra_hash fresco do proprio no da arvore (regra de ouro: nunca inventar).
            m_tipo = re.search(
                r'href=["\'](controlador\.php\?acao=documento_escolher_tipo[^"\']+)["\']',
                resp_arvore.text,
                flags=re.I,
            )
            if m_tipo:
                href_tipo = m_tipo.group(1)
        if not href_tipo:
            raise ErroInclusaoDocumento(
                "Botao 'Incluir Documento' (documento_escolher_tipo) nao encontrado na arvore. "
                "Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )
        url_tipo = self._normalizar_url(href_tipo)

        # 3) GET tela de escolha de tipo (usa o hash do proprio link da arvore)
        resp_tipo = self._requisicao_escrita("get", url_tipo, ids_liberados=ids, timeout=30)
        sopa_tipo = self._sopa(resp_tipo)
        form = self._encontrar_form(
            sopa_tipo,
            ids_forms=("frmDocumentoEscolherTipo", "frmInfraSelecaoSerie"),
            nomes_campos=("id_serie", "selSerie", "txtSerie"),
        )
        if form is None:
            raise ErroInclusaoDocumento(
                "Tela de escolha de tipo nao expoe o formulario de series "
                "(id_serie/selSerie). Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )
        if salvar_telas:
            self._salvar_tela("1_escolher_tipo", resp_tipo)

        payload_escolher = self._extrair_hidden_inputs(form)
        self._atribuir_campo(payload_escolher, ["id_serie", "selSerie", "hdnIdSerie"], id_serie)
        # SEI-MG 5.x: a escolha da serie via JS seta o hidden hdnIdSerie e submete.
        # Checkboxes da listagem (chkInfraItemN) nao fazem parte da escolha.
        for chave in [k for k in payload_escolher if k.startswith("chkInfraItem")]:
            del payload_escolher[chave]
        action_escolher = self._action_do_form(
            sopa_tipo, base_url=url_tipo, form=form
        ) or url_tipo

        resposta_escolha = self._requisicao_escrita(
            "post", action_escolher, data=payload_escolher, ids_liberados=ids,
            allow_redirects=False, timeout=30,
        )

        # algumas versoes carregam a tela de geracao direto (HTTP 200 com o form)
        if resposta_escolha.status_code == 200:
            sopa_pos = self._sopa(resposta_escolha)
            if self._encontrar_form(
                sopa_pos,
                ids_forms=("frmDocumentoGerar",),
                nomes_campos=("rdoNivelAcesso", "txtDataElaboracao"),
            ) is not None:
                if salvar_telas:
                    self._salvar_tela("2_documento_gerar", resposta_escolha)
                return resposta_escolha.text, action_escolher

        if resposta_escolha.status_code not in (301, 302, 303, 307, 308):
            raise ErroInclusaoDocumento(
                f"Escolha do tipo nao redirecionou para documento_gerar "
                f"(HTTP {resposta_escolha.status_code}).",
                etapa="1_escolher_tipo",
            )

        loc_gerar = None
        for chave in ("Location", "location", "LOCATION"):
            try:
                if chave in resposta_escolha.headers:
                    loc_gerar = self._limpar_url(resposta_escolha.headers[chave])
                    break
            except UnicodeDecodeError:
                continue
        if not loc_gerar:
            raise ErroInclusaoDocumento(
                "escolher_tipo respondeu sem header Location.", etapa="1_escolher_tipo"
            )

        # 4) GET tela de geracao
        etapa = "2_tela_gerar"
        url_gerar = self._normalizar_url(loc_gerar)
        if "documento_gerar" not in url_gerar.lower():
            raise ErroInclusaoDocumento(
                f"Location inesperado (nao aponta para documento_gerar): {url_gerar}",
                etapa=etapa,
            )

        resposta_gerar = self._requisicao_escrita("get", url_gerar, ids_liberados=ids, timeout=30)
        sopa_gerar = self._sopa(resposta_gerar)
        form_gerar = self._encontrar_form(
            sopa_gerar,
            ids_forms=("frmDocumentoGerar",),
            nomes_campos=("rdoNivelAcesso", "txtDataElaboracao"),
        )
        if form_gerar is None:
            raise ErroInclusaoDocumento(
                "Tela documento_gerar carregada sem o frmDocumentoGerar — "
                "provável erro de permissao ou de hash expirado.",
                etapa=etapa,
            )

        if salvar_telas:
            self._salvar_tela("2_documento_gerar", resposta_gerar)

        return resposta_gerar.text, action_escolher

    def _montar_payload_gerar(
        self, html_pagina, id_hipotese_legal="4", texto_inicial="N", dados_adicionais=None
    ):
        """Extrai payload_base de frmDocumentoGerar e sobrescreve os campos de negócio."""
        sopa = BeautifulSoup(html_pagina, "html.parser")
        form = self._encontrar_form(
            sopa,
            ids_forms=("frmDocumentoGerar",),
            nomes_campos=("rdoNivelAcesso", "txtDataElaboracao"),
        )
        if form is None:
            return {}

        payload = self._extrair_hidden_inputs(form)

        # Metadados obrigatórios do documento interno
        # Flag de ação do formulário: o JS do SEI (submeter(), disparado pelo
        # botão Salvar) faz hdnFlagDocumentoCadastro = '2' imediatamente antes do
        # submit. Enviar o valor original da tela ('1') faz o servidor apenas
        # re-renderizar a página (HTTP 200) SEM criar o documento.
        self._atribuir_campo(payload, ["hdnFlagDocumentoCadastro"], "2")
        # rdoFormato: N = Nato-digital (documento interno sem arquivo anexo) —
        # sem esse radio o SEI-MG trata como anexo e reclama de extensao de arquivo.
        self._atribuir_campo(payload, ["rdoFormato"], "N")
        self._atribuir_campo(payload, ["rdoNivelAcesso", "selNivelAcesso"], "1")  # 1 = Restrito
        self._atribuir_campo(
            payload,
            ["selHipotesesLegais", "selHipoteseLegal", "hdnHipotesesLegais", "txtHipoteseLegal"],
            str(id_hipotese_legal),
        )
        self._atribuir_campo(payload, ["rdoTextoInicial", "selTextoInicial"], str(texto_inicial))
        self._atribuir_campo(payload, ["txtDataElaboracao"], time.strftime("%d/%m/%Y"))

        # Campos extras de negócio (sobrescrevem/inserem o que o chamador quiser)
        for chave, valor in (dados_adicionais or {}).items():
            payload[chave] = str(valor)

        return payload

    def _action_do_form(self, pagina_ou_sopa, base_url="", form=None):
        """Extrai o atributo action de um formulário; fallback para a própria base_url."""
        if isinstance(pagina_ou_sopa, BeautifulSoup):
            sopa = pagina_ou_sopa
        elif isinstance(pagina_ou_sopa, str):
            sopa = BeautifulSoup(pagina_ou_sopa, "html.parser")
        else:
            sopa = BeautifulSoup(pagina_ou_sopa.text, "html.parser")
        if form is None:
            form = self._encontrar_form(
                sopa,
                ids_forms=("frmDocumentoGerar",),
                nomes_campos=("rdoNivelAcesso", "txtDataElaboracao"),
            )
        if form is None:
            return base_url
        action = self._limpar_url(form.get("action")) or ""
        if not action:
            return base_url
        return self._normalizar_url(action)

    @staticmethod
    def _salvar_tela(nome_etapa, resposta):
        try:
            caminho = os.path.join(PASTA_TELAS, f"{nome_etapa}_{int(time.time())}.html")
            with open(caminho, "wb") as fh:
                fh.write(getattr(resposta, "content", str(resposta).encode("utf-8")))
            print(f"[telas] {nome_etapa} -> {caminho}")
        except OSError as exc:
            print(f"[telas] aviso: não foi possível salvar {nome_etapa}: {exc}")


# ------------------------------ linha de comando ------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Inclusão de documento interno no SEI-MG.")
    parser.add_argument(
        "--id-procedimento",
        help="ID interno do processo (ex.: 000000000). Use OU --processo.",
    )
    parser.add_argument(
        "--processo",
        help="Numero formatado do processo (ex.: 0000.00.0000000/0000-00) — busca automatica na caixa da unidade",
    )
    parser.add_argument(
        "--unidade",
        default=UNIDADE_ALVO,
        help="ID da unidade onde o processo esta (veja no SEI -> Trocar Unidade. Padrao: atual",
    )
    parser.add_argument(
        "--serie", default="63", help="ID da serie/tipo de documento (padrao 63 = Relatório)"
    )
    parser.add_argument("--hipotese-legal", default="4", help="ID da hipotese legal (padrao 4)")
    parser.add_argument(
        "--texto-inicial",
        default="N",
        choices=["N", "T"],
        help="N = branco (padrao) | T = texto padrao a definir",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        dest="confirmar",
        help="Confirma e GRAVA o documento (obrigatorio para escrita real)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepara o payload e NAO envia o POST de criacao",
    )
    parser.add_argument(
        "--salvar-telas",
        action="store_true",
        help="Salva o HTML de cada etapa na pasta ruas_inclusao/",
    )
    parser.add_argument(
        "--mesmo-assim",
        action="store_true",
        help="Permite prosseguir mesmo se o processo aparecer como NAO VISUALIZADO",
    )
    args = parser.parse_args(argv)

    if not args.dry_run and not args.confirmar:
        parser.error("A gravacao e real: informe --confirmar (ou use --dry-run para simular.)")
    if bool(args.id_procedimento) == bool(args.processo):
        parser.error("Informe --id-procedimento OU --processo (um deles, nao ambos/nenhum.)")

    print("Autenticando no SEI-MG...")
    api = InclusaoDocumentoSEI(USUARIO, SENHA, unidade_alvo=args.unidade)
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
        print()
        print("Processo localizado na caixa:")
        for chave in ("Processo", "IdProtocolo", "Tipo", "Especificacao", "NaoVisualizado", "NaoRecebido", "AtribuidoA", "Interessados"):
            print(f"    {chave}: {achado.get(chave, '')}")
        if str(achado.get("NaoVisualizado")).strip().lower() in ("true", "1", "sim", "verdadeiro"):
            nao_visu = True
        else:
            nao_visu = bool(achado.get("NaoVisualizado"))
        if nao_visu and not args.mesmo_assim:
            sys.exit("ABORTADO: processo NAO VISUALIZADO - operacao de prazo sensivel. Se tiver certeza, rode com --mesmo-assim.")

    id_documento = api.incluir_documento_interno(
        id_procedimento=id_procedimento,
        id_serie=args.serie,
        id_hipotese_legal=args.hipotese_legal,
        texto_inicial=args.texto_inicial,
        confirmar=args.confirmar,
        dry_run=args.dry_run,
        salvar_telas=args.salvar_telas,
    )

    if args.dry_run:
        print()
        print("Dry-run concluido - nenhum documento foi gravado.")
    else:
        print()
        print(f"Documento criado com sucesso! id_documento = {id_documento}")


if __name__ == "__main__":
    main()
