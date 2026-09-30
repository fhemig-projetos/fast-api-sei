# -*- coding: utf-8 -*-
"""
Testa a BUSCA RAPIDA por numero de processo, offline.

Nao toca na rede: a tela de origem e o HTML real do SEI (drawable/controle_atual.html)
e as respostas da pesquisa sao montadas aqui no formato real do SEI.

O que precisa ficar provado:
  1. o formulario real da tela e reconhecido e o payload sai certo;
  2. processo visualizado -> devolvido com a marca, e o link de abertura junto;
  3. processo NAO VISUALIZADO -> a trava de ouro segura; com --mesmo-assim, abre;
  4. resultado de outro numero -> rejeitado (nao pode abrir o processo errado);
  5. resposta SEM marca de visualizado -> cai na caixa, nunca devolve "pode abrir";
  6. redirect sem marca -> cai na caixa;
  7. processo fora da pagina 1 da caixa -> a pesquisa supplies o link de abertura.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup  # noqa: E402

import app4_inclusao as app  # noqa: E402

HTML_REAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drawable", "controle_atual.html"
)
NUM = "2270.01.0064841/2026-56"
ID = "941528881"

falhas = []


def checa(descricao, condicao):
    print(("  [ok]   " if condicao else "  [FALHA] ") + descricao)
    if not condicao:
        falhas.append(descricao)


class Resp:
    def __init__(self, html, headers=None):
        self.content = html.encode("utf-8")
        self.status_code = 200
        self.headers = headers or {}
        self.url = "https://www.sei.mg.gov.br/sei/controlador.php"

    @property
    def text(self):
        return self.content.decode("utf-8")


def pagina(classe, numero=NUM, id_proc=ID, sem_classe=False):
    """Reproduz o formato real do SEI: o numero do processo e o texto do link."""
    atributo = "" if sem_classe else f'class="{classe}"'
    return (
        "<html><body><table><tr><td>"
        f'<a {atributo} href="controlador.php?acao=procedimento_trabalhar'
        f'&id_procedimento={id_proc}&infra_hash=hash_fresco">'
        f"{numero}</a></td><td>RH: Estagio</td></tr></table></body></html>"
    )


class Api(app.InclusaoDocumentoSEI):
    """API de mentira: nenhuma chamada sai para a rede."""

    def __init__(self, resposta_html, caixa_html=None, headers=None):
        self._resposta = resposta_html
        self._caixa = caixa_html if caixa_html is not None else html_real()
        self._headers = headers or {}
        self.posts = []
        self.salvar_telas = False
        self.unidade_alvo = "110031457"

    def navegar_para_controle(self):
        return self._caixa

    @property
    def html_home(self):
        # A home e a tela de login: tem o campo de busca no topo.
        return html_real()

    def _ir(self, metodo, url, **kw):
        self.posts.append((metodo, url, kw.get("data")))
        return Resp(self._resposta, self._headers), self._headers.get("Location", "")


def html_real():
    with io.open(HTML_REAL, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def args(mesmo_assim=False):
    return type("A", (), {"mesmo_assim": mesmo_assim, "processo": NUM})()


print("\n1) formulario real da tela")
form = BeautifulSoup(html_real().encode("utf-8", "replace"), "html.parser").find(
    "form", {"id": "frmProtocoloPesquisaRapida"}
)
checa("formulario frmProtocoloPesquisaRapida encontrado", form is not None)
checa("action traz infra_hash (regra de ouro nº 2)", "infra_hash=" in (form.get("action") or ""))
campos = app.InclusaoDocumentoSEI._extrair_hidden_inputs(form)
checa("payload traz txtPesquisaRapida", "txtPesquisaRapida" in campos)

api = Api(pagina("processoVisualizado"))
item = api._buscar_processo_pela_pesquisa(NUM)
checa("numero vai no payload", api.posts and api.posts[0][2].get("txtPesquisaRapida") == NUM)

print("\n2) processo visualizado")
item = api._buscar_processo_pela_pesquisa(NUM)
checa("localizou o processo", bool(item))
checa("id correto", (item or {}).get("IdProtocolo") == ID)
checa("marcado como visualizado", (item or {}).get("NaoVisualizado") is False)
checa("traz o link de abertura", "procedimento_trabalhar" in (item or {}).get("Link", ""))
checa("travas NAO bloqueiam", not app._processo_marca_nao_visualizado(item))

print("\n3) processo NAO VISUALIZADO (vermelho)")
vermelho = Api(pagina("processoNaoVisualizado"))._buscar_processo_pela_pesquisa(NUM)
checa("marcado como nao visualizado", (vermelho or {}).get("NaoVisualizado") is True)
checa("trava de ouro segura", app._processo_marca_nao_visualizado(vermelho))
checa("com --mesmo-assim libera", not (app._processo_marca_nao_visualizado(vermelho)
                                       and not args(True).mesmo_assim))

print("\n4) resultado de outro numero")
outro = Api(pagina("processoVisualizado", "999.99.9999999/2020-99", "999888777"))._buscar_processo_pela_pesquisa(NUM)
checa("rejeitado (nao pode abrir o processo errado)", outro is None)

print("\n5) resposta sem marca de visualizado")
sem_marca = Api(pagina("", sem_classe=True))._buscar_processo_pela_pesquisa(NUM)
checa("cai no fallback em vez de devolver 'pode abrir'", sem_marca is None)

print("\n6) redirect sem marca")
redir = Api(
    "",
    headers={"Location": f"controlador.php?acao=procedimento_trabalhar&id_procedimento={ID}"},
)._buscar_processo_pela_pesquisa(NUM)
checa("cai no fallback", redir is None)

print("\n7) processo fora da pagina 1 da caixa")
caixa_vazia = "<html><body><table></table></body></html>"
api7 = Api(pagina("processoVisualizado"), caixa_html=caixa_vazia)
checa("a caixa nao acha", api7._link_trabalhar_na_caixa(ID) == "")
achado = api7._link_trabalhar_do_processo(ID, NUM)
checa("a pesquisa entrega o link de abertura", "procedimento_trabalhar" in achado)
checa("o link traz id e hash", "id_procedimento=" in achado and "infra_hash=" in achado)
checa("sem numero, nao inventa link", api7._link_trabalhar_do_processo(ID, "") == "")
checa("id diferente nao usa o link de outro processo",
      api7._link_trabalhar_do_processo("111222333", NUM) == "")

print("\n8) regressao: caixa presente mantem o caminho antigo")
caixa_com_link = (
    "<html><body><a href='controlador.php?acao=procedimento_trabalhar"
    f"&id_procedimento={ID}&infra_hash=da_caixa'>{NUM}</a></body></html>"
)
api8 = Api(pagina("processoVisualizado"), caixa_html=caixa_com_link)
checa("usa a caixa quando ela tem o link", api8._link_trabalhar_do_processo(ID, NUM) == api8._link_trabalhar_na_caixa(ID))

print("\n9) resposta REAL do SEI (302 -> id_protocolo)")
# Copiado do log_auditoria_escrita.txt de uma execucao real de 30/09. O SEI
# responde a busca rapida com redirecionamento para o processo e usa
# "id_protocolo" - a busca procurava "id_procedimento" e nunca casava, o que
# rendia "processo nao encontrado" para todos os processos da planilha.
LOCAL_REAL = ("controlador.php?acao=procedimento_trabalhar"
              "&acao_origem=protocolo_pesquisa_rapida"
              f"&id_protocolo={ID}&infra_sistema=100000100"
              "&infra_unidade_atual=110031718&infra_hash=21639fee485bf0877")

api9 = Api("", headers={"Location": LOCAL_REAL})
sem = api9._buscar_processo_pela_pesquisa(NUM)
checa("checkbox desligado: nao devolve item", sem is None)
checa("mas registra que o processo foi localizado", api9._ultima_busca_localizou is True)

com = Api("", headers={"Location": LOCAL_REAL})._buscar_processo_pela_pesquisa(
    NUM, mesmo_assim=True)
checa("checkbox ligado: devolve o item", bool(com))
checa("id correto", (com or {}).get("IdProtocolo") == ID)
checa("link tem id_protocolo", "id_protocolo=" in (com or {}).get("Link", ""))
checa("link tem infra_hash fresco", "infra_hash=" in (com or {}).get("Link", ""))

api9b = Api("", headers={"Location": LOCAL_REAL})
checa("abre pelo link da busca",
      "procedimento_trabalhar" in api9b._link_trabalhar_do_processo(ID, NUM))
checa("id diferente nao usa link alheio",
      api9b._link_trabalhar_do_processo("111222333", NUM) == "")


print("\n10) guarda de escrita: a URL da BUSCA passa, as outras continuam barradas")
# Regressao do lote travado na linha 7: a guarda procurava "id_procedimento"
# e nao achava o "id_protocolo" da URL devolvida pela pesquisa, entao
# bloqueava como se fosse abertura nao autorizada.
_g = Api.__new__(Api)
BASE = "https://www.sei.mg.gov.br/sei/controlador.php"
DA_BUSCA = (BASE + "?acao=procedimento_trabalhar&acao_origem=protocolo_pesquisa_rapida"
            f"&id_protocolo={ID}&infra_hash=abc")
DA_CAIXA = BASE + f"?acao=procedimento_trabalhar&id_procedimento={ID}&infra_hash=abc"
LIB = {ID}

try:
    _g._autorizar(DA_BUSCA, LIB)
    checa("URL da busca com processo liberado passa", True)
except app.ErroInclusaoDocumento as e:
    checa(f"URL da busca com processo liberado passa ({e})", False)

try:
    _g._autorizar(DA_CAIXA, LIB)
    checa("URL da caixa com processo liberado passa", True)
except app.ErroInclusaoDocumento as e:
    checa(f"URL da caixa com processo liberado passa ({e})", False)

for descricao, url, liberados in [
    ("processo NAO liberado", DA_BUSCA, {"999999"}),
    ("URL sem id nenhum", BASE + "?acao=procedimento_trabalhar", LIB),
    ("escrita destrutiva", BASE + f"?acao=procedimento_enviar&id_protocolo={ID}", LIB),
]:
    try:
        _g._autorizar(url, liberados)
        checa(f"continua barrada: {descricao}", False)
    except app.ErroInclusaoDocumento:
        checa(f"continua barrada: {descricao}", True)

print("\n" + ("FALHAS: %d" % len(falhas) if falhas else "TODOS OS TESTES PASSARAM"))
for f in falhas:
    print("  - " + f)
sys.exit(1 if falhas else 0)
