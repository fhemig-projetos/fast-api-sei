# -*- coding: utf-8 -*-
"""
Teste OFFLINE do mail merge do editor SEI (nao toca no SEI, nao usa credenciais).

Roda contra a fixture scripts_write/fixtures/editor_montar_exemplo.html (HTML
capturado do editor, com identificadores e nomes ficticios) e valida:
  * descoberta de TODOS os textareas txaEditor_XXXXX + form + action;
  * regras de merge (contagens esperadas) e ausencia de marcadores remanescentes;
  * leitura do ajax de confirmacao e o filtro por id_documento.

Os dados de servidor usados sao SINTETICOS: nenhum dado real e lido ou
versionado por este teste.
"""
import os
import re
import sys
from types import SimpleNamespace

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from app4_inclusao import (  # noqa: E402
    InclusaoDocumentoSEI as C,
    MARCADORES_DO_MODELO,
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "editor_montar_exemplo.html"
)
ID_DOCUMENTO_EXEMPLO = "900000001"
with open(FIXTURE, encoding="utf-8") as fh:
    html = fh.read()
resp = SimpleNamespace(text=html, content=html.encode("utf-8"), status_code=200, url="fixture")

# 1) Descoberta das partes do editor
partes, form = C._todos_editores(resp)
print("PARTES:", list(partes))
esperadas = ["txaEditor_16892", "txaEditor_16893", "txaEditor_16894", "txaEditor_16896"]
assert list(partes) == esperadas, f"partes inesperadas: {list(partes)}"
assert form is not None and form.get("id") == "frmEditor"
assert "editor_processar.php" in (form.get("action") or ""), form.get("action")
assert "infra_hash=" in (form.get("action") or "")
print("ACTION OK:", (form.get("action") or "")[:90], "...")

SERVIDOR_EXEMPLO = "SERVIDOR DE TESTE"
MASP_EXEMPLO = "00000000"
DADOS_EXEMPLO = {
    "Nome Servidor": SERVIDOR_EXEMPLO,
    "MASP": MASP_EXEMPLO,
    "ADM": "1",
    "Cargo": "CARGO_TESTE",
    "Função": "FUNCAO_TESTE",
    "Nº do termo": "1",
}
# A fixture ja traz 1 ocorrencia do nome no bloco de assinatura (anonimizada);
# somada as 4 substituicoes do merge, o total esperado no corpo e 5.
TOTAL_NOME_ESPERADO = 5

# O merge e conferido contra um CONJUNTO DE DADOS SINTETICO (nenhum servidor
# real e validado aqui - a planilha real nunca e lida por este teste).
total = {}
for nome, conteudo in partes.items():
    mesclado, contagem = C._mesclar_html(conteudo, DADOS_EXEMPLO)
    for chave, qtde in contagem.items():
        total[chave] = total.get(chave, 0) + qtde
    if nome == "txaEditor_16894":
        corpo = mesclado
print("CONTAGENS:", total)

esperado = {
    "MASP": 2,
    "ADM": 1,
    "Nº do termo (titulo)": 1,
    "Nome do servidor (cedente)": 1,
    "Nome do servidor (cedido)": 1,
    "Nome do servidor (clausula)": 1,
    "Nome do servidor (assinatura)": 1,
    "Cargo (sigla)": 1,
    "Funcao do servidor": 1,
    "Data do preenchimento": 1,
}
for chave, qtde in esperado.items():
    assert total.get(chave) == qtde, f"{chave}: esperado {qtde}, obtido {total.get(chave)}"

# 4) Marcadores remanescentes + conteudo
for marcador in MARCADORES_DO_MODELO:
    assert marcador not in corpo, f"marcador remanescente: {marcador}"
assert SERVIDOR_EXEMPLO in corpo
assert corpo.count(SERVIDOR_EXEMPLO) == TOTAL_NOME_ESPERADO
# O CKEditor grava "<strong >valor </strong >" (com espacos do HTML original),
# por isso os valores sao conferidos sem depender dos delimitadores exatos.
assert re.search(r"<strong\s*>?" + MASP_EXEMPLO + r"\s*<", corpo), "MASP nao mesclado"
assert re.search(r"<strong\s*>?CARGO_TESTE\s*<", corpo), "cargo nao mesclado"
assert "FUNCAO_TESTE" in corpo
assert re.search(r"N\s*&ordm;\s*<span[^>]*>\s*>?1\s*<", corpo), "numero do termo nao mesclado"
assert "ADM <span" in corpo, "contexto do ADM alterado"
assert re.search(r"JUIZ DE FORA, <span[^>]*><strong\s*>\d{2}/\d{2}/\d{4}", corpo), "data nao mesclada"
print("TRECHO:", corpo[corpo.find("CONSTITUI".lower()) if False else corpo.find("Constitui"):][:420])

# 5) Ajax de confirmacao lido do HTML do editor
url_conf = C._link_ajax_editor(resp, "corfirmacao_atualizacao_documento", ID_DOCUMENTO_EXEMPLO)
print("AJAX_CONFIRMACAO:", url_conf)
assert "acao_ajax=corfirmacao_atualizacao_documento" in url_conf
assert f"id_documento={ID_DOCUMENTO_EXEMPLO}" in url_conf
assert "&amp;" not in url_conf, f"URL com &amp; escapado: {url_conf}"
# Id de outro documento nao pode ser aceito (evita editar o documento errado).
assert C._link_ajax_editor(resp, "corfirmacao_atualizacao_documento", "999999999") == ""

# 6) Regra de ouro: a troca de unidade NUNCA pode chutar um infra_hash.
#    Exercitamos o fallback do app3 sem tocar na rede (classe fake).
from scripts_read.app3 import APIExtratorSEI  # noqa: E402

HASH_TELA = "a" * 64


class _FakeAPI(APIExtratorSEI):
    """APIExtratorSEI sem rede: registra a URL que teria sido chamada."""

    def __init__(self, html_home):
        self.html_home = html_home
        # Unidade atual DIFERENTE do alvo: caso contrario trocar_unidade() retorna
        # cedo ("ja estamos na unidade alvo") e o fallback nem seria exercitado.
        self.unidade_atual = "999999999"
        self.unidade_alvo = "110007141"
        self.urls_chamadas = []

    def _requisicao_segura(self, metodo, url, **kwargs):
        self.urls_chamadas.append((metodo, url))
        return type("R", (), {"text": "<html></html>", "status_code": 200})()

    def _montar_payload_troca(self, html, unidade):
        return "form-action", {"x": "1"}

    @property
    def url_chamada(self):
        """Ultima URL chamada (o POST do formulario vem por ultimo)."""
        return self.urls_chamadas[-1][1] if self.urls_chamadas else None

    @property
    def url_do_get(self):
        """URL da primeira chamada, isto e, a tela de troca construida."""
        return self.urls_chamadas[0][1] if self.urls_chamadas else None


# 6a) Tela SEM link de troca, mas COM hash -> usa o hash da tela (nunca um fixo)
html_sem_link = f'<html><a href="x?acao=principal&infra_hash={HASH_TELA}">p</a></html>'
api = _FakeAPI(html_sem_link)
api.trocar_unidade()
print("FALLBACK_URL:", api.url_do_get)
assert api.url_do_get and HASH_TELA in api.url_do_get, "deveria usar o hash da tela"
assert "infra_trocar_unidade" in api.url_do_get and "acao=infra_trocar_unidade" in api.url_do_get

# 6b) Tela SEM link e SEM hash -> falha explicita, sem requisicao inventada
api = _FakeAPI("<html><body>sem links</body></html>")
assert api.trocar_unidade() is False, "sem hash deve retornar False"
assert api.url_chamada is None, "nao deve chamar a rede sem hash legitimo"
print("SEM_HASH: falhou como esperado, sem chamar a rede")

# 6c) Tela COM o link de troca -> usa o link original (caminho feliz)
hash_b = "b" * 64
html_com_link = (
    "<html><a id='lnkInfraUnidade' onclick=\"window.location.href='"
    "controlador.php?acao=infra_trocar_unidade&infra_hash=" + hash_b + "'\">u</a></html>"
)
api = _FakeAPI(html_com_link)
api.trocar_unidade()
print("LINK_URL:", api.url_do_get)
assert api.url_do_get and hash_b in api.url_do_get, "deveria usar o link da tela"

# 7) PRE-VOO multi-processo: cada linha segue o processo da PRÓPRIA planilha.
#    Tudo offline: _FakeCaixa conta as buscas e _FakeLoteAPI conta as criacoes.
import argparse  # noqa: E402

from app4_inclusao import (  # noqa: E402
    _preflight_lote,
    _processo_marca_nao_visualizado,
    _ResolvedorProcessos,
)


def _item(indice, processo):
    return {
        "indice": indice,
        "dados": {"Nome Servidor": f"SERVIDOR {indice}", "Nº do termo": str(indice + 1)},
        "processo": processo,
    }


class _FakeCaixa:
    """Caixa falsa: mapeia numero -> id e NAO.Visualizado, contando as buscas."""

    def __init__(self, mapa, nao_visualizados=()):
        self.mapa = mapa
        self.nao_visualizados = set(nao_visualizados)
        self.buscas = []

    def _localizar_processo_na_caixa(self, numero):
        self.buscas.append(numero)
        if numero not in self.mapa:
            return None, False
        achado = {
            "IdProtocolo": self.mapa[numero],
            "NaoVisualizado": numero in self.nao_visualizados,
        }
        return achado, True


class _FakeLoteAPI(_FakeCaixa):
    """Acrescenta contadores de criacao/edicao (devem permanecer 0 no pre-voo)."""

    def __init__(self, mapa, nao_visualizados=()):
        super().__init__(mapa, nao_visualizados)
        self.criados = 0
        self.editados = 0

    def incluir_documento_interno(self, **kwargs):
        self.criados += 1
        return "900000099"

    def editar_conteudo_documento(self, *args, **kwargs):
        self.editados += 1
        return True


def _args_lote(**overrides):
    base = dict(
        processo=None,
        mesmo_assim=False,
        linha=0,
        ate_linha=None,
        a_partir_de=None,
        lote=True,
        plano=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


P_A = "1000.01.0000001/2026-11"
P_B = "1000.01.0000002/2026-22"
P_C = "1000.01.0000003/2026-33"

# 7a) CACHE: 5 linhas em 2 processos distintos -> 2 buscas, nao 5.
api = _FakeLoteAPI({P_A: "900000001", P_B: "900000002"})
itens = [_item(0, P_A), _item(1, P_B), _item(2, P_A), _item(3, P_B), _item(4, P_A)]
por_id = _preflight_lote(api, itens, _args_lote())
print("CACHE_BUSCAS:", api.buscas)
assert api.buscas == [P_A, P_B], f"esperava 2 buscas em cache, veio {api.buscas}"
assert por_id[0] == "900000001" and por_id[1] == "900000002"
assert por_id[4] == "900000001", "linhas do mesmo processo devem reaproveitar o id"
assert api.criados == 0 and api.editados == 0, "pre-voo nao pode criar/editar nada"

# 7b) PROCESSO INEXISTENTE: aborta ANTES de qualquer criacao (todos os erros juntos).
api = _FakeLoteAPI({P_A: "900000001"})
itens = [_item(0, P_A), _item(1, P_C), _item(2, "1000.01.9999999/2026-99")]
try:
    _preflight_lote(api, itens, _args_lote())
except SystemExit as erro:
    assert erro.code == 1, f"deveria sair com codigo 1, saiu {erro.code}"
    print("PRE_VOO_ABORTOU_COMO_ESPERADO")
else:
    raise AssertionError("pre-voo deveria abortar com processo inexistente")
assert api.criados == 0, "NENHUM documento pode ter sido criado antes do abort"

# 7c) REGRA DE OURO 1: processo NAO VISUALIZADO aborta sem --mesmo-assim.
api = _FakeLoteAPI({P_A: "900000001", P_B: "900000002"}, nao_visualizados=[P_B])
itens = [_item(0, P_A), _item(1, P_B)]
try:
    _preflight_lote(api, itens, _args_lote())
except SystemExit as erro:
    assert erro.code == 1
    print("NAO_VISUALIZADO_BLOQUEOU_O_LOTE")
else:
    raise AssertionError("NAO VISUALIZADO deveria abortar sem --mesmo-assim")
assert api.criados == 0

# 7d) --mesmo-assim: o mesmo processo NAO VISUALIZADO passa (decisao explicita).
api = _FakeLoteAPI({P_A: "900000001"}, nao_visualizados=[P_A])
por_id = _preflight_lote(api, [_item(0, P_A)], _args_lote(mesmo_assim=True))
assert por_id[0] == "900000001"
print("MESMO_ASSIM_PERMITIU:", por_id[0])

# 7e) Sem coluna "Processo SEI" e sem --processo -> erro claro, sem inventar.
api = _FakeLoteAPI({})
try:
    _preflight_lote(api, [_item(0, "")], _args_lote())
except SystemExit as erro:
    assert erro.code == 1
    print("SEM_PROCESSO_ABORTOU_COMO_ESPERADO")
else:
    raise AssertionError("sem processo na planilha e sem CLI deveria abortar")
assert api.buscas == [], "nao deveria procurar nada quando falta o numero"

# 7f) --processo da CLI como fallback para linhas SEM "Processo SEI".
api = _FakeLoteAPI({P_A: "900000001"})
por_id = _preflight_lote(
    api, [_item(0, ""), _item(1, "")], _args_lote(processo=P_A), "900000001"
)
assert por_id[0] == "900000001" and por_id[1] == "900000001"
assert api.buscas == [], "com --processo na CLI nao deve raspar a caixa de novo"

# 7g) --plano: imprime o plano e NAO grava nada.
api = _FakeLoteAPI({P_A: "900000001", P_B: "900000002"})
itens = [_item(0, P_A), _item(1, P_B)]
_preflight_lote(api, itens, _args_lote(plano=True), somente_plano=True)
assert api.criados == 0 and api.editados == 0
print("PLANO_NAO_GRAVOU_NADA")

# 7h) Guarda da regra de ouro 1: variantes de "NaoVisualizado".
for valor, esperado in [
    (True, True), (False, False), (None, False), ("", False),
    ("true", True), ("TRUE", True), ("1", True), ("sim", True),
    ("verdadeiro", True), ("S", True), ("false", False), ("nao", False),
]:
    obtido = _processo_marca_nao_visualizado({"NaoVisualizado": valor})
    assert obtido is esperado, f"NaoVisualizado={valor!r}: {obtido} != {esperado}"
assert _processo_marca_nao_visualizado(None) is False
assert _processo_marca_nao_visualizado({}) is False
print("GUARDA_NAO_VISUALIZADO_OK")

print("TODOS_OS_TESTES_OFFLINE_PASSARAM")

