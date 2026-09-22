# -*- coding: utf-8 -*-
"""
Repara a indentacao quebrada de _extrair_hidden_inputs em app4_inclusao.py.

O arquivo ficou com o decorator @staticmethod dentro da classe e o def no nivel
de modulo (IndentationError: unexpected unindent, linha 158), alem de um bloco
de codigo morto (duplicata inalcancavel) depois do return payload.

Este script faz cirurgia por numero de linha e restaura o metodo staticmethod
dentro da classe, removendo o codigo morto.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALVO = os.path.join(RAIZ, "app4_inclusao.py")

with io.open(ALVO, encoding="utf-8") as fh:
    linhas = fh.read().splitlines()

BLOCO_NOVO = [
    "    @staticmethod",
    "    def _extrair_hidden_inputs(form):",
    '        """',
    "        Colhe todos os campos enviaveis de um formulario (hidden, text, textarea e",
    "        select), como um submit padrao faria. E daqui que saem todos os infra_hash",
    "        e IDs fresquinhos da tela (regra de ouro: nunca inventar/prever hash).",
    '        """',
    "        payload = {}",
    "        if form is None:",
    "            return payload",
    '        for tag in form.find_all(["input", "select", "textarea"]):',
    '            nome = tag.get("name")',
    "            if not nome:",
    "                continue",
    '            if tag.name == "select":',
    '                opcao = tag.find("option", selected=True) or tag.find("option")',
    '                payload[nome] = opcao.get("value", "") if opcao is not None else ""',
    "                continue",
    '            if tag.name == "textarea":',
    '                valor = tag.get_text() or ""',
    "            else:  # <input>",
    '                tipo = (tag.get("type") or "text").lower()',
    '                if tipo in ("submit", "button", "image", "checkbox", "radio", "file"):',
    "                    continue",
    '                valor = tag.get("value", "")',
    '            payload[nome] = valor if valor is not None else ""',
    "        return payload",
    "",
]

idx_def = None
for i, linha in enumerate(linhas):
    if linha.strip() == "def _extrair_hidden_inputs(form):":
        idx_def = i
        break
if idx_def is None:
    sys.exit("ERRO: 'def _extrair_hidden_inputs(form):' nao encontrado.")

if linhas[idx_def - 1].strip() != "@staticmethod":
    sys.exit(
        "ERRO: o @staticmethod nao esta imediatamente antes (linha %d): %r"
        % (idx_def, linhas[idx_def - 1])
    )

idx_fim = None
for j in range(idx_def + 1, len(linhas)):
    if linhas[j].strip() == "@staticmethod":
        idx_fim = j
        break
if idx_fim is None:
    sys.exit("ERRO: nao encontrei o proximo @staticmethod depois do bloco quebrado.")

print(
    "Substituindo linhas %d..%d (1-based) por bloco limpo (%d linhas -> %d linhas)."
    % (idx_def, idx_fim, idx_fim - (idx_def - 1), len(BLOCO_NOVO))
)

novas = linhas[: idx_def - 1] + BLOCO_NOVO + linhas[idx_fim:]

with io.open(ALVO, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(novas) + "\n")

print("OK: app4_inclusao.py reparado.")
