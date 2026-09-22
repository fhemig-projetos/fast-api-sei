# -*- coding: utf-8 -*-
"""
Diagnostico do payload do dry-run de inclusao de documento interno (app4_inclusao).

Objetivo: confirmar quais campos do frmDocumentoGerar sao realmente capturados
pelo _extrair_hidden_inputs (em especial txaObservacoes) ANTES de qualquer POST
de criacao. Nada e gravado no SEI quando se usa dry_run=True.

Uso:
    venv/Scripts/python teste_payload.py <id_procedimento> [id_unidade] [id_serie]

O id_procedimento e obrigatorio (argumento ou variavel de ambiente
SEI_ID_PROCEDIMENTO). Unidade e serie tem padroes configuraveis por
SEI_UNIDADE / SEI_ID_SERIE.
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# Importar app4_inclusao PRIMEIRO garante que o .env da raiz ja entrou em
# os.environ antes de qualquer leitura de credencial do modulo app3.
from app4_inclusao import InclusaoDocumentoSEI  # noqa: E402
from scripts_read.app3 import SENHA, UNIDADE_ALVO, USUARIO  # noqa: E402

ID_PROCEDIMENTO = (
    sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEI_ID_PROCEDIMENTO", "")
)
UNIDADE = (
    sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SEI_UNIDADE", UNIDADE_ALVO)
)
ID_SERIE = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SEI_ID_SERIE", "63")

if not ID_PROCEDIMENTO:
    sys.exit(
        "Informe o id_procedimento do processo:\n"
        "  venv/Scripts/python teste_payload.py <id_procedimento> [id_unidade] [id_serie]\n"
        "  (ou defina a variavel de ambiente SEI_ID_PROCEDIMENTO)"
    )

if UNIDADE_ALVO != UNIDADE:
    print(f"(UNIDADE_ALVO do app3 = {UNIDADE_ALVO}; usando {UNIDADE} nesta execucao)")
print(f"Processo={ID_PROCEDIMENTO} | Unidade={UNIDADE} | Serie={ID_SERIE}")

api = InclusaoDocumentoSEI(USUARIO, SENHA, unidade_alvo=UNIDADE)
if not api.autenticar():
    sys.exit("Falha no login.")
api.trocar_unidade()

payload = api.incluir_documento_interno(
    id_procedimento=ID_PROCEDIMENTO,
    id_serie=ID_SERIE,
    dry_run=True,       # nada e gravado
    salvar_telas=True,  # html de cada etapa em .\ruas_inclusao\
)

print("\n--- RESULTADO ---")
if not payload:
    sys.exit("FALHA: o dry-run nao retornou payload (o fluxo parou antes do passo 3).")

os.makedirs("ruas_inclusao", exist_ok=True)
caminho_dump = os.path.join("ruas_inclusao", "payload_gerar.json")
with open(caminho_dump, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
print(f"Payload salvo em {caminho_dump} ({len(payload)} campos).")

if "txaObservacoes" in payload:
    print("SUCESSO: txaObservacoes ESTA no payload do dry-run!")
else:
    print("AVISO: txaObservacoes NAO apareceu no payload (a tela pode nao ter esse campo).")

print("\nCampos capturados:")
for chave in sorted(payload):
    valor = str(payload[chave])
    if len(valor) > 100:
        valor = valor[:100] + "..."
    print(f"  {chave} = {valor}")
