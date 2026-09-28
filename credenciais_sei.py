# -*- coding: utf-8 -*-
"""
Credenciais do SEI-MG: guarda o usuario e a SENHA do operador que esta usando o
programa, nunca os de outra pessoa.

Por que existe isso: o app4_inclusao.py le as credenciais do ambiente
(SEI_USUARIO / SEI_SENHA). Sem a tela, todo mundo que copiasse o executavel
para o PC dele acabaria usando o login de quem distribuiu - e o SEI registra o
AUTOR de cada documento, entao os termos criados ficariam no nome da pessoa
errada. Aqui cada operador informa a propria conta.

A senha e gravada com CryptProtectData (DPAPI do proprio Windows), sem
dependencia externa: o arquivo so abre para o usuario do Windows que o criou,
naquela maquina. Copiar o .dat para outro PC ou outra conta nao decifra nada.

O arquivo fica em %APPDATA%\\AutomacaoSEI\\credenciais.dat - fora da pasta do
projeto e fora do .exe, e nunca entra no Git.
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys

# ---------------------------------------------------------------- constantes

APP_DIR = "AutomacaoSEI"
ARQ_CREDENCIAIS = "credenciais.dat"
# Assinatura simples: se o arquivo mudar de formato, recusamos em vez de
# tentar interpretar bytes desconhecidos.
MARCADOR = "AutomacaoSEI:credenciais:v1"

CHAVE_USUARIO = "SEI_USUARIO"
CHAVE_SENHA = "SEI_SENHA"

# ------------------------------------------------------------- DPAPI (ctypes)

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_CRYPTPROTECT_LOCAL_MACHINE = 0x04


class ErroCredenciais(Exception):
    """Falha ao gravar, ler ou apagar as credenciais salvas."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_disponivel():
    if not hasattr(ctypes, "windll"):  # fora do Windows nao ha DPAPI
        return False
    return hasattr(ctypes.windll, "crypt32")


def _proteger(dados: bytes) -> bytes:
    """Cifra com CryptProtectData. Somente o usuario atual consegue ler."""
    entrada = _Blob()
    entrada.cbData = len(dados)
    entrada.pbData = ctypes.cast(
        ctypes.create_string_buffer(dados, len(dados)), ctypes.POINTER(ctypes.c_byte)
    )
    saida = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(entrada), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(saida),
    ):
        raise ErroCredenciais("Windows recusou a cifragem da senha (CryptProtectData).")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


def _desproteger(cifrado: bytes) -> bytes:
    entrada = _Blob()
    entrada.cbData = len(cifrado)
    entrada.pbData = ctypes.cast(
        ctypes.create_string_buffer(cifrado, len(cifrado)), ctypes.POINTER(ctypes.c_byte)
    )
    saida = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida),
    ):
        raise ErroCredenciais(
            "Nao foi possivel ler a senha salva: ela foi gravada em outra "
            "conta de usuario do Windows ou em outro computador. Digite a senha."
        )
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


# ----------------------------------------------------------------- caminho

def caminho_credenciais() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR, ARQ_CREDENCIAIS)


# --------------------------------------------------------------- API publica


def tem_credenciais() -> bool:
    return os.path.isfile(caminho_credenciais())


def salvar(usuario: str, senha: str) -> str:
    """
    Grava usuario + senha (cifrada) para o usuario do Windows atual.

    Devolve o caminho gravado. Levanta ErroCredenciais se a cifragem falhar
    (nesse caso NADA e escrito, para nao sobrar senha em texto puro).
    """
    usuario = (usuario or "").strip()
    if not usuario:
        raise ErroCredenciais("Informe o usuario do SEI.")
    if not _dpapi_disponivel():
        raise ErroCredenciais(
            "Cifragem do Windows (DPAPI) indisponivel nesta maquina. "
            "Desmarque 'Lembrar usuario' e digite a senha a cada uso."
        )

    carga = json.dumps(
        {"marcador": MARCADOR, "usuario": usuario, "senha": senha or ""},
        ensure_ascii=False,
    ).encode("utf-8")
    cifrado = _proteger(carga)  # levanta ANTES de criar o arquivo

    destino = caminho_credenciais()
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    # Grava em temporario e troca: evita deixar credencial pela metade se a
    # maquina for desligada no meio da escrita.
    temporario = destino + ".tmp"
    with open(temporario, "wb") as arquivo:
        arquivo.write(MARCADOR.encode("ascii") + b"|" + base64.b64encode(cifrado))
    os.replace(temporario, destino)
    return destino


def carregar() -> dict:
    """
    Devolve {"usuario": str, "senha": str} ou {} quando nao ha credencial salva.

    Nao levanta excecao para "nao existe" (so para arquivo corrompido ou senha
    de outra conta) - a tela trata os dois casos.
    """
    destino = caminho_credenciais()
    if not os.path.isfile(destino):
        return {}
    if not _dpapi_disponivel():
        return {}
    try:
        with open(destino, "rb") as arquivo:
            bruto = arquivo.read()
    except OSError:
        return {}

    separador = bruto.find(b"|")
    if separador < 0 or bruto[:separador].decode("ascii", "replace") != MARCADOR:
        raise ErroCredenciais("Arquivo de credenciais invalido ou de outra versao.")

    try:
        carga = json.loads(
            _desproteger(base64.b64decode(bruto[separador + 1:])).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError) as erro:
        raise ErroCredenciais(f"Credenciais corrompidas: {erro}")
    return {"usuario": carga.get("usuario", ""), "senha": carga.get("senha", "")}


def apagar() -> bool:
    """Apaga o arquivo de credenciais. Devolve True se havia algo para apagar."""
    destino = caminho_credenciais()
    if not os.path.isfile(destino):
        return False
    try:
        os.remove(destino)
        return True
    except OSError as erro:
        raise ErroCredenciais(f"Nao foi possivel apagar as credenciais: {erro}")



def aplicar_no_ambiente(usuario: str, senha: str) -> None:
    """
    Coloca as credenciais onde o app4_inclusao.py le: os.environ.

    O backend nao muda - ele continua lendo SEI_USUARIO/SEI_SENHA do ambiente.
    A GUI apenas preenche antes de chamar.
    """
    os.environ[CHAVE_USUARIO] = (usuario or "").strip()
    os.environ[CHAVE_SENHA] = senha or ""


def tem_credencial_no_ambiente() -> bool:
    return bool(os.environ.get(CHAVE_USUARIO)) and bool(os.environ.get(CHAVE_SENHA))


def aviso_credenciais() -> str:
    """Texto pronto para o console, sobre onde a senha fica guardada."""
    if not _dpapi_disponivel():
        return "Armazenamento seguro indisponivel: a senha NAO sera lembrada."
    return f"Credenciais guardadas em: {caminho_credenciais()}"


if __name__ == "__main__":  # diagnostico manual: python credenciais_sei.py
    print("DPAPI disponivel:", _dpapi_disponivel())
    print("Caminho:", caminho_credenciais())
    print("Credencial ja salva:", tem_credenciais())
    if "--teste" in sys.argv:
        destino = caminho_credenciais() + ".teste"
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        carga = json.dumps({"marcador": MARCADOR, "usuario": "u", "senha": "s"})
        with open(destino, "wb") as arquivo:
            arquivo.write(
                MARCADOR.encode("ascii") + b"|" + base64.b64encode(_proteger(carga.encode()))
            )
        with open(destino, "rb") as arquivo:
            bruto = arquivo.read()
        lido = json.loads(
            _desproteger(base64.b64decode(bruto.split(b"|", 1)[1])).decode("utf-8")
        )
        print("Cifra/decifra DPAPI:", lido)
        os.remove(destino)
        print("Arquivo de teste removido:", not os.path.exists(destino))
