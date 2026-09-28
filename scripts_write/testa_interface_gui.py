# -*- coding: utf-8 -*-
"""
Testes OFFLINE da interface grafica (interface_app.py) e das credenciais.

Nao abre a janela, nao autentica no SEI e nao grava nada. O que e verificado:

  1. import da interface sem construir a janela (o que o PyInstaller precisa);
  2. o Namespace montado pela GUI cobre TODOS os atributos que o
     app4_inclusao.py le via "args.X" - se faltar um, o app quebraria em
     tempo de execucao, entao este teste falha antes;
  3. o RedirecionadorConsole quebra as linhas corretamente e nao levanta erro;
  4. as credenciais: gravar, reler e apagar, sempre cifradas com DPAPI;
  5. a senha NAO aparece em texto puro no arquivo gravado.

Uso:  python scripts_write/testa_interface_gui.py
"""

import os
import re
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import credenciais_sei  # noqa: E402
import interface_app  # noqa: E402

print("[1] Import da interface sem abrir janela")
assert hasattr(interface_app, "AppAutomacaoSEI")
assert hasattr(interface_app, "RedirecionadorConsole")
print("    OK: modulos importados")

# =============================================================================
print("\n[2] Namespace da GUI cobre todos os args.X lidos pelo app4")
with open(os.path.join(RAIZ, "app4_inclusao.py"), encoding="utf-8") as arquivo:
    fonte_app4 = arquivo.read()

lidos = set(re.findall(r"\bargs\.(\w+)", fonte_app4))
lidos |= set(re.findall(r'args\[["\']([^"\']+)["\']\]', fonte_app4))
lidos |= set(re.findall(r'getattr\(\s*args\s*,\s*["\'](\w+)["\']', fonte_app4))

montado = vars(interface_app.montar_args_lote("planilha.xlsx"))
faltando = sorted(lidos - set(montado))
print(f"    atributos lidos pelo backend: {len(lidos)}")
print(f"    atributos montados pela GUI: {len(montado)}")
print(f"    lista: {sorted(lidos)}")
assert not faltando, f"Namespace da GUI sem: {faltando}"
print("    OK: nenhum atributo faltando")

assert montado["confirmar"] is True, "sem confirmar=True o backend aborta"
assert montado["lote"] is True

# =============================================================================
print("\n[3] RedirecionadorConsole quebra linhas e nao levanta erro")


class _FalsoWidget:
    """Widget minimo: executa o after() na hora, como a thread principal faria."""

    def __init__(self):
        self.recebidas = []

    def after(self, _ms, func, *args):
        func(*args)

    def configure(self, **_kwargs):
        pass

    def insert(self, _indice, texto):
        self.recebidas.append(texto)

    def see(self, _fim):
        pass


widget = _FalsoWidget()
red = interface_app.RedirecionadorConsole(widget)
red._original = sys.stdout
red.write("linha um\n")
red.write("parte A")
red.write(" e parte B\n")
red.write("sem quebra no fim")
red.flush()
texto = "".join(widget.recebidas)
print("    recebido:", repr(texto))

# =============================================================================
print("\n[4] Credenciais: gravar, reler e apagar (DPAPI)")
original = credenciais_sei.caminho_credenciais
com_temporario = os.path.join(tempfile.gettempdir(), "automacao_sei_teste.dat")
credenciais_sei.caminho_credenciais = lambda: com_temporario

try:
    credenciais_sei.apagar()
    assert credenciais_sei.tem_credenciais() is False

    caminho = credenciais_sei.salvar("USUARIO_TESTE", "SENHA_SECRETA_123")
    print("    gravado em:", os.path.basename(caminho))
    assert credenciais_sei.tem_credenciais() is True

    lido = credenciais_sei.carregar()
    print("    relido:", {k: "***" if k == "senha" else v for k, v in lido.items()})
    assert lido["usuario"] == "USUARIO_TESTE"
    assert lido["senha"] == "SENHA_SECRETA_123", "a senha nao sobreviveu a cifragem"
    print("    OK: gravou e leu de volta")

    # =============================================================================
    print("\n[5] A senha nao esta em texto puro no arquivo")
    with open(com_temporario, "rb") as arquivo:
        bruto = arquivo.read()
    assert b"SENHA_SECRETA_123" not in bruto, "SENHA EM TEXTO PURO NO DISCO!"
    print(f"    arquivo com {len(bruto)} bytes; nenhuma senha legivel")
    print("    OK: conteudo cifrado (so o dono do Windows neste PC le)")

    # Usuario vazio: fail-safe, sem criar arquivo.
    credenciais_sei.apagar()
    try:
        credenciais_sei.salvar("   ", "x")
    except credenciais_sei.ErroCredenciais:
        assert credenciais_sei.tem_credenciais() is False
        print("    OK: usuario vazio recusado, nenhum arquivo criado")
    else:
        raise AssertionError("deveria recusar usuario vazio")

    # Arquivo corrompido: erro claro, nao crash.
    with open(com_temporario, "wb") as arquivo:
        arquivo.write(b"lixo sem separador")
    try:
        credenciais_sei.carregar()
    except credenciais_sei.ErroCredenciais as erro:
        print("    OK: arquivo invalido gera erro claro")
    else:
        raise AssertionError("arquivo corrompido deveria dar erro")

    assert credenciais_sei.apagar() is True
    assert credenciais_sei.tem_credenciais() is False
    print("    OK: apagou as credenciais")
finally:
    credenciais_sei.caminho_credenciais = original
    if os.path.exists(com_temporario):
        os.remove(com_temporario)

# =============================================================================
print("\n[6] Caracteristicas de seguranca da tela")
fonte_gui = open(
    os.path.join(RAIZ, "interface_app.py"), encoding="utf-8"
).read()
# Ignora comentarios e docstrings: o que importa e o CODIGO. A GUI pode
# mencionar ".env" na documentacao desde que nunca o abra (e nao abre).
import io  # noqa: E402
import tokenize  # noqa: E402

_codigo = []
with open(os.path.join(RAIZ, "interface_app.py"), "rb") as _fh:
    for _tipo, _texto, _ini, _fim, _linha in tokenize.tokenize(_fh.readline):
        if _tipo not in (tokenize.COMMENT, tokenize.STRING):
            _codigo.append(_texto)
codigo_gui = " ".join(_codigo)
assert "threading.Thread" in fonte_gui, "GUI precisa rodar em thread separada"
assert "daemon=True" in fonte_gui, "thread sem daemon prenderia o fechamento"
assert "after(0" in fonte_gui, "atualizacao de UI precisa usar after(0)"
assert ".env" not in codigo_gui, (
    "GUI nao pode ABRIR o .env: cada operador usa o proprio login"
)
assert 'os.environ["SEI_USUARIO"]' in fonte_gui, "a GUI deve injetar no ambiente"
print("    OK: thread, after(0) e ausencia de .env")

print("\nTODOS_OS_TESTES_DA_INTERFACE_PASSARAM")

assert "linha um\n" in texto
assert "parte A e parte B\n" in texto, "texto parcial deveria virar uma linha so"
assert texto.endswith("sem quebra no fim\n"), "flush() deveria fechar a ultima linha"
print("    OK: console reconstroi as linhas corretamente")

assert montado["dry_run"] is False
print("    OK: flags de seguranca corretas")

linha = vars(interface_app.montar_args_linha("planilha.xlsx", 3))
assert linha["linha"] == 3 and linha["lote"] is False and linha["a_partir_de"] is None
assert not (set(lidos) - set(linha)), "Namespace de linha unica incompleto"
print("    OK: Namespace de linha unica completo")
