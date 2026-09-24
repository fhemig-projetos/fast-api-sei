# -*- coding: utf-8 -*-
"""
Validacao OFFLINE do fluxo "Documento Modelo" do app4_inclusao.

Nao autentica e nao faz nenhuma requisicao: a instancia e criada com __new__ e o
payload e montado a partir do HTML REAL capturado em ruas_inclusao/.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # raiz do projeto
sys.path.insert(0, RAIZ)

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from app4_inclusao import (  # noqa: E402
    SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE,
    InclusaoDocumentoSEI,
)

TELA = "ruas_inclusao/2_documento_gerar_1790173515.html"
LUPA_ESPERADA = (
    "controlador.php?acao=documento_modelo_selecionar&tipo_selecao=1"
    "&id_object=objLupaDocumentoTextoBase&arvore=1&id_procedimento=900000002"
    "&id_serie=12&infra_sistema=100000100&infra_unidade_atual=110031457"
    "&infra_hash=HASH_DE_EXEMPLO_NAO_USAR_EM_PRODUCAO"
)

FALHAS = []


def checar(nome, obtido, esperado):
    ok = obtido == esperado
    print(f"  {'OK   ' if ok else 'FALHA'} | {nome}: {obtido!r} (esperado {esperado!r})")
    if not ok:
        FALHAS.append(nome)


def checar_verdade(nome, condicao, detalhe=""):
    print(f"  {'OK   ' if condicao else 'FALHA'} | {nome} {detalhe}")
    if not condicao:
        FALHAS.append(nome)


sopa = BeautifulSoup(open(TELA, "rb").read(), "html.parser")
api = InclusaoDocumentoSEI.__new__(InclusaoDocumentoSEI)  # instancia sem login/rede

print("\n[1] Payload SEM modelo (comportamento antigo preservado)")
form, base = api._payload_gerar(sopa, "4", "N")
checar("form.id", form.get("id"), "frmDocumentoCadastro")
checar("rdoTextoInicial", base.get("rdoTextoInicial"), "N")
checar("hdnFlagDocumentoCadastro", base.get("hdnFlagDocumentoCadastro"), "2")
checar("rdoFormato", base.get("rdoFormato"), "N")
checar("txtProtocoloDocumentoTextoBase", base.get("txtProtocoloDocumentoTextoBase"), "")
checar("hdnIdDocumentoTextoBase", base.get("hdnIdDocumentoTextoBase"), "")

print("\n[2] Payload COM documento modelo (protocolo 148410511)")
_, modelo = api._payload_gerar(sopa, "4", "N", protocolo_modelo="148410511")
checar("rdoTextoInicial", modelo.get("rdoTextoInicial"), "D")
checar("txtProtocoloDocumentoTextoBase", modelo.get("txtProtocoloDocumentoTextoBase"), "148410511")
checar("hdnIdDocumentoTextoBase", modelo.get("hdnIdDocumentoTextoBase"), "")
checar("txtTextoPadrao zerado", modelo.get("txtTextoPadrao"), "")
checar("hdnIdTextoPadrao zerado", modelo.get("hdnIdTextoPadrao"), "")
checar("hdnFlagDocumentoCadastro", modelo.get("hdnFlagDocumentoCadastro"), "2")
checar("rdoNivelAcesso", modelo.get("rdoNivelAcesso"), "1")
checar("selHipoteseLegal", modelo.get("selHipoteseLegal"), "4")
checar("hdnIdSerie", modelo.get("hdnIdSerie"), "12")

print("\n[3] Payload COM modelo + id interno informado")
_, modelo_id = api._payload_gerar(
    sopa, "4", "N", protocolo_modelo="148410511", id_documento_modelo="987654321"
)
checar("hdnIdDocumentoTextoBase", modelo_id.get("hdnIdDocumentoTextoBase"), "987654321")

print("\n[4] 'D' vence o texto_inicial pedido no CLI")
checar(
    "_texto_inicial_efetivo('T', modelo)",
    api._texto_inicial_efetivo("T", "148410511"),
    "D",
)
checar("_texto_inicial_efetivo('N')", api._texto_inicial_efetivo("N"), "N")
checar("SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE", SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE, "4110")

print("\n[4b] Resolucao do modelo sem tocar na rede (guardas do _resolver_documento_modelo)")
checar(
    "consultar_lupa=False -> so protocolo",
    api._resolver_documento_modelo(sopa, "148410511", consultar_lupa=False),
    {"protocolo": "148410511", "id_documento": ""},
)
checar(
    "id informado -> respeitado",
    api._resolver_documento_modelo(sopa, "148410511", "987654321", consultar_lupa=False),
    {"protocolo": "148410511", "id_documento": "987654321"},
)

print("\n[5] URL da lupa lida do JS da tela (hash dinamico, nunca fixado)")
checar("lupa", api._url_lupa_documento_modelo(sopa), LUPA_ESPERADA)
checar_verdade(
    "lupa contem infra_hash da tela",
    "infra_hash=HASH_DE_EXEMPLO_NAO_USAR_EM_PRODUCAO" in api._url_lupa_documento_modelo(sopa),
)
checar(
    "lupa ausente na tela ->",
    api._url_lupa_documento_modelo(BeautifulSoup("<html></html>", "html.parser")),
    "",
)
api._autorizar(api._normalizar_url(LUPA_ESPERADA), ())
checar_verdade("whitelist aceita documento_modelo_selecionar", True)
checar(
    "normalizacao",
    api._normalizar_url(LUPA_ESPERADA),
    "https://www.sei.mg.gov.br/sei/" + LUPA_ESPERADA,
)

print("\n[6] Extracao do hidden na lupa (infraTableToHiddenText: txt=title / hdn=value)")
checar(
    "radio title=protocolo -> value (ordem real do SEI)",
    api._extrair_id_modelo_na_lupa(
        '<input id="chkInfraItem0" name="chkInfraItem" tabindex="1013" title="148410511"'
        ' type="radio" value="123456789"/>',
        "148410511",
    ),
    "123456789",
)
checar(
    "ordem invertida (value antes de title)",
    api._extrair_id_modelo_na_lupa(
        '<input name="chkInfraItem" value="987654321" title="148410511"/>', "14.841.051-1"
    ),
    "987654321",
)
checar(
    "id_documento do link de visualizar e IGNORADO",
    api._extrair_id_modelo_na_lupa(
        '<a href="controlador.php?acao=documento_visualizar&id_documento=940402743">148410511</a>',
        "148410511",
    ),
    "",
)
checar(
    "fallback selecionarItem(id, protocolo)",
    api._extrair_id_modelo_na_lupa(
        "objLupa.selecionarItem('123456789', '148410511', 'CESSao');", "148410511"
    ),
    "123456789",
)
checar(
    "protocolo ausente na lupa",
    api._extrair_id_modelo_na_lupa(
        '<input name="chkInfraItem" title="999999999" type="radio" value="123456789"/>',
        "148410511",
    ),
    "",
)

print("\n[7] Payload e URL-encodavel pelo requests (o POST real sai daqui)")
preparada = requests.Request("POST", api._action(form), data=modelo).prepare()
checar_verdade(
    "action do form",
    "acao=documento_gerar" in preparada.url,
    f"-> {preparada.url[:80]}...",
)
checar_verdade(
    "body contem rdoTextoInicial=D",
    "rdoTextoInicial=D" in preparada.body,
    "",
)
checar_verdade(
    "body contem txtProtocoloDocumentoTextoBase=148410511",
    "txtProtocoloDocumentoTextoBase=148410511" in preparada.body,
    "",
)
checar_verdade("body contem hdnFlagDocumentoCadastro=2", "hdnFlagDocumentoCadastro=2" in preparada.body)

print("\n[8] Fluxo completo com sessao FALSA (nenhuma requisicao real, nada gravado)")


class RespostaFalsa:
    """Resposta minima compativel com o que o app4 consome (content/status/headers)."""

    def __init__(self, conteudo=b"", status=200, url="", location=""):
        self.content = conteudo
        self.status_code = status
        self.url = url
        self.headers = {"Location": location} if location else {}
        # _link_tipo_na_arvore/_mensagens_erro usam .text (regex no HTML bruto)
        self.text = conteudo.decode("utf-8", "replace") if conteudo else ""


def ler_tela(nome):
    return open(os.path.join("ruas_inclusao", nome), "rb").read()


TELA_PROC = ler_tela("0_processo_aberto_1790173514.html")
TELA_ARVORE = ler_tela("1_arvore_1790173515.html")
TELA_TIPO = ler_tela("1_escolher_tipo_1790173515.html")
TELA_GERAR = ler_tela("2_documento_gerar_1790173515.html")
LUPA_OK = (
    b'<html><tr>'
    b'<td><input id="chkInfraItem0" name="chkInfraItem" tabindex="1013" title="148410511"'
    b' type="radio" value="555000111"/></td>'
    # link da coluna de acao: id_documento de OUTRO fim - o extractor tem de ignora-lo
    b'<td><a href="controlador.php?acao=documento_visualizar&id_documento=940402743">'
    b'148410511</a></td></tr></html>'
)


def rodar_fluxo(protocolo=None, consultar_lupa=True, lupa_status=200, serie="4110", dry_run=False):
    api2 = InclusaoDocumentoSEI.__new__(InclusaoDocumentoSEI)
    api2.unidade_alvo, api2.salvar_telas = "110031457", False
    chamadas, posts = [], {}

    def fake_ir(metodo, url, ids_liberados=None, **kwargs):
        url = api2._normalizar_url(url)
        api2._autorizar(url, ids_liberados or ())  # guardas reais de escrita
        chamadas.append((metodo.upper(), url))
        # Criterio: o param "acao=" principal (o Location do escolher traz
        # acao=documento_gerar E acao_origem=documento_escolher_tipo ao mesmo tempo).
        if "acao=documento_modelo_selecionar" in url:
            return RespostaFalsa(LUPA_OK, lupa_status, url=url), ""
        if metodo == "post" and "acao=documento_escolher_tipo" in url:
            posts["escolher"] = dict(kwargs.get("data") or {})
            destino = (
                "/sei/controlador.php?acao=documento_gerar&acao_origem=documento_escolher_tipo"
                f"&arvore=1&id_procedimento=900000002&id_serie={serie}&infra_hash=hash-do-teste"
            )
            return RespostaFalsa(b"", 302, url=url, location=destino), destino
        if metodo == "get" and "acao=documento_gerar" in url:  # antes do escolher!
            return RespostaFalsa(TELA_GERAR, 200, url=url), ""
        if metodo == "post" and "acao=documento_gerar" in url:
            posts["gerar"] = dict(kwargs.get("data") or {})
            destino = "/sei/controlador.php?acao=documento_visualizar&id_documento=123456789"
            return RespostaFalsa(b"", 302, url=url, location=destino), destino
        if metodo == "get" and "acao=documento_escolher_tipo" in url:
            return RespostaFalsa(TELA_TIPO, 200, url=url), ""
        if "acao=procedimento_visualizar" in url:  # iframe da arvore
            return RespostaFalsa(TELA_ARVORE, 200, url=url), ""
        if "acao=procedimento_trabalhar" in url:
            return RespostaFalsa(TELA_PROC, 200, url=url), ""
        raise AssertionError(f"URL inesperada no teste: {metodo} {url}")

    api2._ir = fake_ir
    api2._salvar_tela = lambda *a, **k: None
    api2._registrar_auditoria_escrita = lambda *a, **k: None
    api2._link_trabalhar_na_caixa = lambda idp: (
        "controlador.php?acao=procedimento_trabalhar&id_procedimento=900000002&infra_hash=do-teste"
    )
    id_criado = api2.incluir_documento_interno(
        id_procedimento="900000002",
        id_serie=serie,
        texto_inicial="N",
        protocolo_modelo=protocolo,
        consultar_lupa_modelo=consultar_lupa,
        confirmar=not dry_run,  # so libera o POST; a sessao e falsa, nada sai da maquina
        dry_run=dry_run,
    )
    return id_criado, posts, chamadas


id_gerado, posts, chamadas = rodar_fluxo(protocolo="148410511")
checar("id_documento criado", id_gerado, "123456789")
checar("escolher_tipo -> hdnIdSerie", posts["escolher"].get("hdnIdSerie"), "4110")
checar("gerar -> rdoTextoInicial", posts["gerar"].get("rdoTextoInicial"), "D")
checar(
    "gerar -> txtProtocoloDocumentoTextoBase",
    posts["gerar"].get("txtProtocoloDocumentoTextoBase"),
    "148410511",
)
checar(
    "gerar -> hdnIdDocumentoTextoBase (veio da lupa)",
    posts["gerar"].get("hdnIdDocumentoTextoBase"),
    "555000111",
)
checar("gerar -> hdnFlagDocumentoCadastro", posts["gerar"].get("hdnFlagDocumentoCadastro"), "2")
checar("gerar -> txtTextoPadrao", posts["gerar"].get("txtTextoPadrao", ""), "")
checar_verdade(
    "lupa documento_modelo_selecionar foi consultada",
    any("documento_modelo_selecionar" in url for _, url in chamadas),
)
print(f"      (hdnIdSerie da tela falsa de gerar: {posts['gerar'].get('hdnIdSerie')})")

print("\n[9] Variacoes (lupa fora do ar, lupa desativada, sem modelo)")
_, posts_erro, _ = rodar_fluxo(protocolo="148410511", lupa_status=500)
checar(
    "lupa HTTP 500 -> protocolo vai assim mesmo, hdn vazio",
    (
        posts_erro["gerar"].get("rdoTextoInicial"),
        posts_erro["gerar"].get("txtProtocoloDocumentoTextoBase"),
        posts_erro["gerar"].get("hdnIdDocumentoTextoBase"),
    ),
    ("D", "148410511", ""),
)
_, posts_sem, chamadas_sem = rodar_fluxo(protocolo="148410511", consultar_lupa=False)
checar_verdade(
    "--sem-consultar-modelo nao chama a lupa",
    not any("documento_modelo_selecionar" in url for _, url in chamadas_sem),
)
checar(
    "so protocolo no payload",
    (
        posts_sem["gerar"].get("rdoTextoInicial"),
        posts_sem["gerar"].get("txtProtocoloDocumentoTextoBase"),
        posts_sem["gerar"].get("hdnIdDocumentoTextoBase"),
    ),
    ("D", "148410511", ""),
)
_, posts_base, _ = rodar_fluxo(protocolo=None)
checar("fluxo antigo (sem modelo) -> rdoTextoInicial", posts_base["gerar"].get("rdoTextoInicial"), "N")
checar(
    "fluxo antigo -> campo do modelo vazio",
    posts_base["gerar"].get("txtProtocoloDocumentoTextoBase"),
    "",
)

print("\n[10] --dry-run no fluxo completo (README: dry-run + salvar-telas)")
payload_dry, posts_dry, chamadas_dry = rodar_fluxo(protocolo="148410511", dry_run=True)
checar_verdade("dry-run devolve dict de payload", isinstance(payload_dry, dict))
checar("dry-run -> rdoTextoInicial", payload_dry.get("rdoTextoInicial"), "D")
checar(
    "dry-run -> txtProtocoloDocumentoTextoBase",
    payload_dry.get("txtProtocoloDocumentoTextoBase"),
    "148410511",
)
checar(
    "dry-run -> hdnIdDocumentoTextoBase (da lupa)",
    payload_dry.get("hdnIdDocumentoTextoBase"),
    "555000111",
)
checar_verdade(
    "dry-run -> NENHUM POST de gerar enviado",
    not any(m == "POST" and "acao=documento_gerar" in u for m, u in chamadas_dry),
)
checar_verdade(
    "dry-run -> lupa consultada (GET somente leitura)",
    any(m == "GET" and "acao=documento_modelo_selecionar" in u for m, u in chamadas_dry),
)

print("\n" + ("TODAS AS VERIFICACOES PASSARAM" if not FALHAS else f"FALHAS: {FALHAS}"))
sys.exit(1 if FALHAS else 0)
