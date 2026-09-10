# -*- coding: utf-8 -*-
"""
app3.py — Extrator de metadados da CAIXA de Processos Recebidos do SEI-MG (GOVMG).

OBJETIVO
--------
Raspar SOMENTE os dados visíveis na listagem do "Controle de Processos"
(caixa de processos), SEM entrar em NENHUM processo. Nenhuma chamada a
procedimento_trabalhar/infra de documento é feita — apenas as telas de
listagem são acessadas. Isso é importante para não marcar/alterar o estado
de processos que não podem constar como recebidos pela unidade.

Fluxo:
    1) login via biblioteca `requests` (mesma mecânica do app.py/app2.py);
    2) troca de unidade para a FHEMIG usando o infra_hash dinâmico da tela;
    3) navega até o Controle de Processos (inicializar.php -> link fresco);
    4) raspa as 5 primeiras páginas da caixa "Processos Recebidos";
    5) joga os dados num pandas.DataFrame e mantém APENAS os processos
       cujo número começa com "1080" (judiciais FHEMIG).

Bibliotecas: requests, beautifulsoup4 (bs4), pandas, openpyxl.
"""

import os
import re
import sys
import time

import requests
import pandas as pd
from bs4 import BeautifulSoup


def _carregar_credenciais():
    """Lê SEI_USUARIO e SEI_SENHA do ambiente ou do arquivo .env.

    O arquivo .env fica ao lado do script e NÃO é versionado no Git
    (ver .gitignore).
    """
    caminho_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(caminho_env):
        with open(caminho_env, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, _, valor = linha.partition("=")
                os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))

    usuario = os.environ.get("SEI_USUARIO")
    senha = os.environ.get("SEI_SENHA")
    if not usuario or not senha:
        raise RuntimeError(
            "Credenciais do SEI não encontradas. Crie o arquivo .env ao lado do "
            "script com:\n  SEI_USUARIO=<seu login>\n  SEI_SENHA=<sua senha>"
        )
    return usuario, senha


# ==========================================
# 1. CONFIGURAÇÃO
# ==========================================
LOGIN_URL = "https://www.sei.mg.gov.br/sip/login.php?sigla_orgao_sistema=GOVMG&sigla_sistema=SEI"
BASE_SEI = "https://www.sei.mg.gov.br/sei/"

# --- credenciais: vêm do .env / ambiente (nada de senha em texto puro no código) ---
USUARIO, SENHA = _carregar_credenciais()

# --- login: valores capturados da página real (F12) ---
VALUE_SEL_ORGAO = "52"   # FHEMIG (código do órgão no campo selOrgao)
VALUE_HDN_ACAO = "2"     # valor real enviado pelo JS ao clicar em ACESSAR

# --- extração ---
UNIDADE_ALVO = "110007141"   # unidade FHEMIG onde fica a caixa de processos
LIMITE_PAGINAS = 5           # somatório às 5 primeiras páginas da caixa
PREFIXO_FILTRO = "1080"      # mantém somente processos que começam com este número
ARQUIVO_SAIDA = "caixa_recebidos_fhemig_1080.xlsx"

# ==========================================
# 1.5 TRAVAS DE SEGURANÇA — NUNCA entrar em processos
# ==========================================
ARQUIVO_AUDITORIA = "log_auditoria.txt"

# Únicas telas que o robô tem permissão de acessar. Qualquer outra URL
# ABORTA o script imediatamente. A caixa de processos é a tela
# 'procedimento_controlar' (a lista em si, com paginação).
PADROES_PERMITIDOS = (
    "login.php",                     # formulário de login (GET e POST)
    "acao=infra_trocar_unidade",     # tela de troca de unidade
    "inicializar.php",               # raiz da aplicação SEI
    "acao=principal",                # home pós-login (redirect do inicializar)
    "acao=procedimento_controlar",   # CAIXA de processos (lista + paginação)
)

# Ações que abririam/alterariam processo, documento ou andamento.
# Se qualquer URL tentar conter uma destas, o robô aborta ANTES de enviar.
TOKENS_PROIBIDOS = (
    "procedimento_trabalhar",            # abrir o processo (marca como visualizado)
    "procedimento_visualizar",
    "procedimento_abrir",
    "procedimento_receber",              # receber processo
    "procedimento_concluir",
    "procedimento_enviar",
    "procedimento_atualizar_andamento",
    "procedimento_atribuicao_cadastrar",
    "procedimento_atribuicao_listar",
    "procedimento_sobrestar",
    "procedimento_devolver",
    "procedimento_anexar",
    "andamento_pesquisar",               # abrir linha do tempo/andamentos
    "andamento_listar",
    "andamento_marcador",
    "documento_listar",
    "documento_visualizar",
    "documento_abrir",
    "documento_incluir",
    "documento_gerar",
    "infra_arquivo",
    "infra_upload",
    "anotacao_registrar",
    "anotacao_alterar",
    "controle_prazo",
    "acompanhamento_cadastrar",
    "peticionamento",
    "bloco_incluir",
    "rel_bloco_protocolo",
    "protocolo_pesquisa_rapida",
)


# ==========================================
# 2. UTILITÁRIOS DE PARSE (testáveis offline)
# ==========================================
def _limpar(texto):
    """Normaliza espaços em branco / quebras de linha de um texto cru."""
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).replace("\u200b", "").strip()


def _tipo_especificacao(aria_label):
    """
    Separa o conteúdo do aria-label do checkbox de cada linha.

    Formatos vistos na página real:
      "Tipo Processo Judicial / Especificação PROC300..."  → (Tipo, Espec)
      "Não recebido / Tipo Correição: ... / Especificação PAD..." → (..., ...)
      "Tipo Pedidos, Oferecimentos e Informações Diversas" → (Tipo, "")
    """
    texto = (aria_label or "").strip()
    texto = re.sub(r"^N[ãa]o recebido\s*/\s*", "", texto, flags=re.I)
    texto = re.sub(r"^Tipo\s+", "", texto, flags=re.I)

    if " / Especificação " in texto:
        tipo, espec = texto.split(" / Especificação ", 1)
        return _limpar(tipo), _limpar(espec)
    return _limpar(texto), ""


def _extrair_tooltip_mouseover(funcao_onmouseover):
    """
    Extrai (titulo, subtitulo) do padrão:
        infraTooltipMostrar('Título','Subtítulo');
    usado em anotações, marcadores e sinalizadores da listagem.
    """
    if not funcao_onmouseover:
        return None
    m = re.search(
        r"infraTooltipMostrar\s*\(\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3\s*\)",
        funcao_onmouseover,
        flags=re.S,
    )
    if m:
        return _limpar(m.group(2)), _limpar(m.group(4))
    return None


def _extrair_status(linha):
    """Lê anotações, marcadores e sinalizadores que aparecem na 2ª célula."""
    anotacoes, marcadores, sinalizadores = [], [], []
    tds = linha.find_all("td")
    if len(tds) > 1:
        celula_status = tds[1]
        for el in celula_status.find_all(["a", "img"]):
            rotulo = _limpar(el.get("aria-label") or el.get("title"))
            tooltip = _extrair_tooltip_mouseover(el.get("onmouseover"))
            if rotulo.startswith("Anotação / "):
                anotacoes.append(rotulo[len("Anotação / "):])
            elif rotulo.startswith("Marcador / "):
                marcadores.append(rotulo[len("Marcador / "):])
            elif tooltip:
                sinalizadores.append(tooltip[0])
            elif rotulo:
                sinalizadores.append(rotulo)
    return (" | ".join(anotacoes), " | ".join(marcadores), " | ".join(sinalizadores))


def _dados_base_linha(linha, numero_pagina):
    """Parte comum às duas visualizações: checkbox, link do processo e atribuição."""
    tds = linha.find_all("td")
    if len(tds) < 2:
        return None

    checkbox = tds[0].find("input", {"type": "checkbox"})
    if checkbox is not None:
        protocolo = (checkbox.get("title") or "").strip()
        id_protocolo = (checkbox.get("value") or "").strip()
        aria = checkbox.get("aria-label") or ""
    else:
        protocolo, id_protocolo, aria = "", "", ""

    link_proc = linha.find("a", href=re.compile(r"procedimento_trabalhar"))
    if link_proc is not None:
        texto_link = "".join(link_proc.stripped_strings).replace("\u200b", "")
        texto_link = _limpar(texto_link) or protocolo
        nao_visualizado = "processoNaoVisualizado" in (link_proc.get("class") or [])
        if not id_protocolo:
            m_id = re.search(r"id_procedimento=(\d+)", link_proc.get("href", ""))
            id_protocolo = m_id.group(1) if m_id else ""
    else:
        texto_link = protocolo
        nao_visualizado = False

    nao_recebido = bool(re.match(r"^N[ãa]o recebido", aria, flags=re.I))
    tipo, especificacao = _tipo_especificacao(aria)
    if not tipo and link_proc is not None:
        aria_link = re.sub(
            r"^N[ãa]o recebido\s*/\s*", "", link_proc.get("aria-label") or "", flags=re.I
        )
        tipo, especificacao = _tipo_especificacao("Tipo " + aria_link)

    atribuido_a, cpf_atribuido = "", ""
    link_atrib = linha.find("a", class_="ancoraSigla")
    if link_atrib is not None:
        m_nome = re.search(r"Atribu[íi]do para (.+)", link_atrib.get("title") or "")
        if m_nome:
            atribuido_a = _limpar(m_nome.group(1))
        cpf_atribuido = _limpar(link_atrib.get_text())

    return {
        "Processo": _limpar(texto_link) or protocolo,
        "NumeroCheckbox": protocolo,
        "IdProtocolo": id_protocolo,
        "Tipo": tipo,
        "Especificacao": especificacao,
        "NaoVisualizado": nao_visualizado,
        "NaoRecebido": nao_recebido,
        "AtribuidoA": atribuido_a,
        "CpfAtribuido": cpf_atribuido,
        "Interessados": "",
        "Anotacoes": "",
        "Marcadores": "",
        "Sinalizadores": "",
        "Pagina": numero_pagina,
    }


def _parsear_linha_recebidos(linha, numero_pagina):
    """Linha da visualização RESUMIDA (tblProcessosRecebidos)."""
    base = _dados_base_linha(linha, numero_pagina)
    if base is None or not base["Processo"]:
        return None
    anot, marc, sina = _extrair_status(linha)
    base["Anotacoes"], base["Marcadores"], base["Sinalizadores"] = anot, marc, sina
    return base


def _parsear_linha_detalhada(linha, numero_pagina):
    """Linha da visualização DETALHADA (tblProcessosDetalhado) — a atual do SEI-MG."""
    base = _dados_base_linha(linha, numero_pagina)
    if base is None or not base["Processo"]:
        return None

    anot, marc, sina = _extrair_status(linha)
    base["Anotacoes"], base["Marcadores"], base["Sinalizadores"] = anot, marc, sina

    cel_tipo = linha.find("td", {"data-label": "Tipo"})
    if cel_tipo is not None:
        texto_tipo = _limpar(cel_tipo.get_text())
        if texto_tipo:
            base["Tipo"] = texto_tipo

    cel_inter = linha.find("td", {"data-label": "Interessados"})
    if cel_inter is not None:
        base["Interessados"] = _limpar(cel_inter.get_text())

    return base


def raspar_pagina_recebidos(html, numero_pagina):
    """
    Lê a caixa 'Processos Recebidos' (visão resumida OU detalhada) e prepara o
    POST da próxima página.

    Retorna: (dados, tem_proxima, action_url, form_data, total_registros)
    """
    sopa = BeautifulSoup(html, "html.parser")

    # O SEI-MG atual usa a visualização DETALHADA; versões antigas, a RESUMIDA.
    if sopa.find("table", {"id": "tblProcessosDetalhado"}):
        visualizacao = "D"
        tabela = sopa.find("table", {"id": "tblProcessosDetalhado"})
    else:
        visualizacao = "R"
        tabela = (
            sopa.find("table", {"id": "tblProcessosRecebidos"})
            or sopa.find("table", {"id": "tblProcessos"})
        )

    dados = []
    tem_proxima = False
    action_url = ""
    form_data = {}
    total_registros = None

    if tabela is not None:
        caption = tabela.find("caption")
        if caption is not None:
            m_total = re.search(r"\((\d+)\s+registros", caption.get_text())
            if m_total:
                total_registros = int(m_total.group(1))

        for linha in tabela.find_all("tr")[1:]:  # pula o cabeçalho
            if visualizacao == "D":
                item = _parsear_linha_detalhada(linha, numero_pagina)
            else:
                item = _parsear_linha_recebidos(linha, numero_pagina)
            if item:
                dados.append(item)

    bloco = "Detalhado" if visualizacao == "D" else "Recebidos"
    campo_pagina = (
        "hdnDetalhadoPaginaAtual" if visualizacao == "D" else "hdnRecebidosPaginaAtual"
    )

    # Setinha "Próxima Página" do bloco ativo => existe página seguinte
    proxima = sopa.find(
        "a",
        onclick=re.compile(
            r"infraAcaoPaginar\(\s*['\"]\+['\"][^)]*?['\"]%s['\"]" % re.escape(bloco)
        ),
    )
    if proxima is not None:
        tem_proxima = True

    # Formulário que carrega os campos ocultos da paginação (hash + itens + página)
    form = sopa.find("form", {"id": "frmProcedimentoControlar"})
    if form is not None:
        action_url = form.get("action", "")
        if not action_url.startswith("http"):
            action_url = BASE_SEI + action_url

        for inp in form.find_all("input", {"type": "hidden"}):
            nome = inp.get("name")
            if nome:
                form_data[nome] = inp.get("value", "")

        try:
            pagina_atual = int(form_data.get(campo_pagina, 0) or 0)
        except ValueError:
            pagina_atual = 0
        form_data[campo_pagina] = str(pagina_atual + 1)

    return dados, tem_proxima, action_url, form_data, total_registros


# ==========================================
# 3. CLASSE PRINCIPAL DA API
# ==========================================
class APIExtratorSEI:
    def __init__(self, usuario, senha):
        self.usuario = usuario
        self.senha = senha
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36")
        })
        self.html_home = ""
        self.unidade_atual = ""

    def _requisicao_segura(self, metodo, url, **kwargs):
        """
        Wrapper OBRIGATÓRIO de todas as requisições HTTP.

        Bloqueia qualquer URL fora da whitelist (PADROES_PERMITIDOS) e qualquer
        URL que contenha ações perigosas (TOKENS_PROIBIDOS), abortando o script
        ANTES de enviar a requisição. Também grava um log de auditoria com cada
        acesso e a URL final (após redirects).
        """
        url_lower = url.lower()
        for token in TOKENS_PROIBIDOS:
            if token in url_lower:
                raise SystemExit(
                    f"SEGURANÇA: URL bloqueada (contém '{token}'): {url}\n"
                    "O script NÃO abre processos, documentos ou andamentos.\n"
                    "Operação abortada."
                )

        if not any(p in url for p in PADROES_PERMITIDOS):
            raise SystemExit(
                f"SEGURANÇA: URL fora da whitelist: {url}\n"
                "Operação abortada para não tocar em nada além da caixa de processos."
            )

        self._registrar_auditoria(f"{metodo.upper()} {url}")
        if metodo.lower() == "get":
            resposta = self.session.get(url, **kwargs)
        else:
            resposta = self.session.post(url, **kwargs)

        # Verifica também a URL final (depois de redirects legítimos).
        for token in TOKENS_PROIBIDOS:
            if token in resposta.url.lower():
                raise SystemExit(
                    f"SEGURANÇA: redirect bloqueado (contém '{token}'): {resposta.url}"
                )
        self._registrar_auditoria(
            f"  -> final: {resposta.url} | status: {resposta.status_code}"
        )
        return resposta

    @staticmethod
    def _registrar_auditoria(linha):
        try:
            with open(ARQUIVO_AUDITORIA, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {linha}\n")
        except OSError:
            pass

    # ------------------------------------------------------------
    # 3.1 LOGIN
    # ------------------------------------------------------------
    def autenticar(self):
        """Entra no SEI (GET login -> cookie do órgão -> POST com credenciais)."""
        print("Conectando ao SEI-MG...")
        self._requisicao_segura("get", LOGIN_URL, timeout=30)
        # O JS grava este cookie com o código do órgão antes de enviar o form.
        self.session.cookies.set("SIP_U_GOVMG_SEI", VALUE_SEL_ORGAO)

        dados_login = {
            "txtUsuario": self.usuario,
            "pwdSenha": self.senha,
            "selOrgao": VALUE_SEL_ORGAO,
            "hdnAcao": VALUE_HDN_ACAO,
        }
        resposta = self._requisicao_segura("post", LOGIN_URL, data=dados_login, timeout=30)

        if "sair.php" in resposta.text or "Sair" in resposta.text:
            print("  Login realizado com sucesso!")
            self.html_home = resposta.text
            self._detectar_unidade_atual(resposta.text)
            return True
        print("  Login falhou — revise as credenciais/rede.")
        return False

    def _detectar_unidade_atual(self, html):
        """Descobre a unidade logada pelo link 'trocar unidade' da barra superior."""
        sopa = BeautifulSoup(html, "html.parser")
        link = sopa.find("a", {"id": "lnkInfraUnidade"})
        if link is not None and link.get("onclick"):
            m = re.search(r"infra_unidade_atual=(\d+)", link["onclick"])
            if m:
                self.unidade_atual = m.group(1)

    # ------------------------------------------------------------
    # 3.2 TROCA DE UNIDADE (hash dinâmico, não fixo)
    # ------------------------------------------------------------
    def _link_trocar_unidade(self, html):
        sopa = BeautifulSoup(html, "html.parser")
        link = sopa.find("a", {"id": "lnkInfraUnidade"})
        if link is None or not link.get("onclick"):
            return ""
        m = re.search(
            r"window\.location\.href='([^']*?acao=infra_trocar_unidade[^']*)'",
            link["onclick"],
        )
        return m.group(1) if m else ""

    def trocar_unidade(self):
        """Troca para a unidade FHEMIG alvo usando o infra_hash fresco da tela."""
        if self.unidade_atual == UNIDADE_ALVO:
            print(f"Já estamos na unidade alvo {UNIDADE_ALVO}. Pulando a troca.")
            return True

        url_dinamica = self._link_trocar_unidade(self.html_home)
        if not url_dinamica:
            print("  Nao encontrei o link de troca; usando URL de fallback (pode expirar).")
            url_dinamica = (
                "controlador.php?acao=infra_trocar_unidade&"
                "acao_origem=infra_trocar_unidade&infra_sistema=100000100&"
                "infra_unidade_atual=110007141&"
                "infra_hash=c9e91b2dc924e6ed02cb49ce1a4a58fcf4cfbd3d49a4960b9ed924f1a"
            )

        if not url_dinamica.startswith("http"):
            url_dinamica = BASE_SEI + url_dinamica

        print(f"Trocando para a unidade FHEMIG {UNIDADE_ALVO}...")
        try:
            pagina_troca = self._requisicao_segura("get", url_dinamica, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"  GET da tela de troca falhou: {exc}")
            return False

        action, payload = self._montar_payload_troca(pagina_troca.text, UNIDADE_ALVO)
        if not action:
            print("  Formulário de troca de unidade não encontrado na resposta.")
            return False

        try:
            # allow_redirects=False: o SEI responde com redirect com acentuação
            # "quebrada"; o requests lança UnicodeDecodeError, o que é esperado.
            self._requisicao_segura("post", action, data=payload, allow_redirects=False, timeout=30)
        except UnicodeDecodeError:
            pass
        except requests.exceptions.RequestException as exc:
            print(f"  POST da troca falhou: {exc}")
            return False

        self.unidade_atual = UNIDADE_ALVO
        print("  Comando de troca de unidade enviado.")
        return True

    def _montar_payload_troca(self, html, unidade):
        """
        Lê o form 'frmInfraSelecaoUnidade' e devolve (action, payload) fiel ao
        que o JavaScript do SEI envia.

        O JS da versão 5.0.4 cria um campo oculto 'selInfraUnidades' com o id
        da unidade escolhida antes de submeter; sem ele a troca não acontece.
        """
        sopa = BeautifulSoup(html, "html.parser")
        form = sopa.find("form", {"id": "frmInfraSelecaoUnidade"})
        if form is None:
            for f in sopa.find_all("form"):
                if f.find("input", {"name": "hdnInfraItens"}) or f.find(
                    "input", {"name": "chkInfraItem"}
                ):
                    form = f
                    break
        if form is None:
            return "", {}

        action = form.get("action", "")
        if not action.startswith("http"):
            action = BASE_SEI + action

        # Todos os campos nomeados enumerados do form (hidden, texto, radios, selects).
        payload = {}
        for inp in form.find_all("input"):
            nome = inp.get("name")
            if not nome:
                continue
            tipo = (inp.get("type") or "text").lower()

            if tipo == "radio":
                # marca a unidade escolhida; demais rádios ficam de fora
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

        disponiveis = [
            ck.get("value", "") for ck in form.find_all("input", {"name": "chkInfraItem"})
        ]
        if unidade not in disponiveis:
            print(f"  Unidade {unidade} não está disponível em {disponiveis}.")
            return "", {}

        # Campo que o JavaScript cria na hora do submit (a chave da troca).
        payload["selInfraUnidades"] = unidade
        payload["chkInfraItem"] = unidade     # rádio marcado = unidade alvo
        payload["hdnInfraNroItens"] = str(len(disponiveis))
        return action, payload

    # ------------------------------------------------------------
    # 3.3 NAVEGAÇÃO ATÉ O CONTROLE DE PROCESSOS
    # ------------------------------------------------------------
    def navegar_para_controle(self):
        """Garante uma página do Controle de Processos com infra_hash fresco."""
        try:
            res = self._requisicao_segura("get", BASE_SEI + "inicializar.php", timeout=30)
            html = res.text
        except requests.exceptions.RequestException as exc:
            print(f"  inicializar.php falhou ({exc}); usando a home do login.")
            html = self.html_home

        if any(
            t in html for t in ("tblProcessosDetalhado", "tblProcessosRecebidos", "tblProcessos")
        ):
            return html

        sopa = BeautifulSoup(html, "html.parser")
        link = sopa.find("a", href=re.compile(r"acao=procedimento_controlar"))
        if link is not None:
            url = link.get("href", "")
            if not url.startswith("http"):
                url = BASE_SEI + url
            return self._requisicao_segura("get", url, timeout=30).text

        print("  Link do Controle de Processos nao encontrado; usando HTML atual.")
        return html

    # ------------------------------------------------------------
    # 3.4 RASPAR AS N PRIMEIRAS PÁGINAS DA CAIXA DE RECEBIDOS
    # ------------------------------------------------------------
    def extrair_caixa_recebidos(self, limite_paginas=LIMITE_PAGINAS, atraso=0.6):
        """Varre somente a caixa 'Processos Recebidos' (1ª até N-ésima página).

        `atraso` é o intervalo (em segundos) entre uma página e outra.
        Menor atraso => mais rápido, porém mais "agressivo" com o servidor.
        """
        html = self.navegar_para_controle()
        if not html or not any(
            t in html for t in ("tblProcessosDetalhado", "tblProcessosRecebidos", "tblProcessos")
        ):
            print("Nao consegui carregar a caixa de Processos (Recebidos/Detalhado).")
            return []

        todos = []
        ids_vistos = set()
        pagina = 1

        while pagina <= limite_paginas:
            print(f"Página {pagina} de até {limite_paginas} da caixa...")
            itens, tem_proxima, action_url, form_data, total_reg = raspar_pagina_recebidos(html, pagina)

            if pagina == 1 and total_reg:
                print(f"  Total de registros na caixa: {total_reg}")

            if not itens:
                print("  A tabela veio vazia — a sessão/hash pode ter expirado.")
                break

            todos.extend(itens)
            n_nao_visu = sum(1 for i in itens if i["NaoVisualizado"])
            print(f"  {len(itens)} processos visíveis nesta página ({n_nao_visu} nao visualizados).")

            ids_pagina = {i["IdProtocolo"] for i in itens if i["IdProtocolo"]}
            if pagina > 1 and ids_pagina and ids_pagina <= ids_vistos:
                print("  ATENÇÃO: a próxima página repetiu os mesmos itens "
                      "(paginação pode ter ficado parada).")
            ids_vistos |= ids_pagina

            if not tem_proxima or pagina >= limite_paginas:
                if pagina >= limite_paginas:
                    print(f"  Limite de {limite_paginas} páginas atingido.")
                else:
                    print("  Fim da fila de Recebidos alcançado.")
                break

            pagina += 1
            time.sleep(max(0.0, float(atraso)))  # delicadeza com o servidor
            resposta = self._requisicao_segura("post", action_url, data=form_data, timeout=30)
            html = resposta.text

        return todos

# ==========================================
# 4. PROCESSAMENTO COM PANDAS
# ==========================================
def gerar_dataframe_filtrado(dados, prefixo=PREFIXO_FILTRO, arquivo=ARQUIVO_SAIDA):
    """
    Converte os dados em DataFrame.

    Se `prefixo` for None (ou vazio), mantém TODOS os processos raspados.
    Caso contrário, mantém apenas os que começam com o prefixo (ex.: "1080").
    Retorna o DataFrame resultante (ou None se não houver dados).
    """
    if not dados:
        print("Nada para processar (lista vazia).")
        return None

    df = pd.DataFrame(dados)
    n_paginas = df["Pagina"].nunique()
    print(f"\nTotal raspado nas {n_paginas} página(s): {len(df)} processos.")

    if prefixo:
        df_filtrado = df[df["Processo"].astype(str).str.startswith(prefixo)].copy()
        if df_filtrado.empty:
            print(f"Nenhum processo começando com '{prefixo}' na caixa destas páginas.")
            return df_filtrado

        print(f"Mantidos APENAS os processos {prefixo}*: "
              f"{len(df_filtrado)} de {len(df)}.")
    else:
        df_filtrado = df.copy()
        print(f"Sem filtro de número: mantidos TODOS os {len(df_filtrado)} processos raspados.")

    n_nao_visu = int(df_filtrado["NaoVisualizado"].sum())
    if n_nao_visu:
        print(f"  Obs.: {n_nao_visu} deles estão como NÃO VISUALIZADOS "
              f"(evite computá-los como recebidos).")

    if arquivo:
        try:
            df_filtrado.to_excel(arquivo, index=False, engine="openpyxl")
            print(f"Planilha '{arquivo}' gerada com sucesso!")
        except PermissionError:
            print(f"O arquivo '{arquivo}' está aberto no Excel. Feche-o e rode de novo.")

    return df_filtrado


# ==========================================
# 5. TESTE OFFLINE (usa um HTML já salvo, sem tocar no SEI)
# ==========================================
def testar_offline(caminhos_html=None):
    """Testa o parser com HTMLs já salvos, sem tocar no SEI."""
    if caminhos_html is None:
        caminhos_html = ["resposta_login.html", "controle_atual.html"]
    elif isinstance(caminhos_html, str):
        caminhos_html = [caminhos_html]

    itens = []
    for caminho in caminhos_html:
        try:
            with open(caminho, encoding="utf-8", errors="replace") as f:
                html = f.read()
        except FileNotFoundError:
            print(f"OFFLINE: '{caminho}' não encontrado; pulando.\n")
            continue

        pagina_itens, tem_proxima, action_url, form_data, total = raspar_pagina_recebidos(html, 1)
        visual = "Detalhada" if "tblProcessosDetalhado" in html else "Resumida"
        print(f"OFFLINE [{os.path.basename(caminho)}] — visão {visual}:")
        print(f"   {len(pagina_itens)} linhas | tem_proxima={tem_proxima} | total registros={total}")
        print(f"   action_url: {action_url[:100]}...")
        print(f"   campos de paginação: {', '.join(sorted(form_data))[:150]}")
        for item in pagina_itens[:3]:
            print("   ", item)
        print()
        itens.extend(pagina_itens)

    if not itens:
        print("Nenhum HTML para validar.")
        return
    gerar_dataframe_filtrado(itens, arquivo=None)


# ==========================================
# 6. EXECUÇÃO
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--offline":
        alvo = sys.argv[2] if len(sys.argv) > 2 else "resposta_login.html"
        testar_offline(alvo)
        sys.exit(0)

    # --todos: mantém TODOS os processos das páginas raspadas (sem filtro 1080).
    manter_todos = "--todos" in sys.argv[1:]
    prefixo = None if manter_todos else PREFIXO_FILTRO
    arquivo_saida = "caixa_recebidos_todos.xlsx" if manter_todos else ARQUIVO_SAIDA

    api = APIExtratorSEI(USUARIO, SENHA)
    if not api.autenticar():
        sys.exit(1)

    api.trocar_unidade()

    dados_caixa = api.extrair_caixa_recebidos(limite_paginas=LIMITE_PAGINAS)
    gerar_dataframe_filtrado(dados_caixa, prefixo=prefixo, arquivo=arquivo_saida)