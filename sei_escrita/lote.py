# -*- coding: utf-8 -*-
"""
A esteira de producao do lote.
"""

import os
import sys

from .erros import ErroInclusaoDocumento, LinhaSemProcesso
from .planilha import (
    _processo_marca_nao_visualizado,
    carregar_planilha,
    resolver_caminho_planilha,
)

class _ResolvedorProcessos:
    """
    Resolve o id_procedimento de cada linha do lote, com CACHE por numero.

    Sem o cache, uma planilha com N linhas do mesmo processo raspa a caixa N
    vezes (cada raspagem recarrega a caixa). Aqui cada numero e buscado uma unica
    vez e reaproveitado. Erros ficam guardados por numero e sao levantados
    apenas no pre-voo, com o indice da linha afetada.
    """

    def __init__(self, api, args, id_procedimento_padrao=None):
        self.api = api
        self.args = args
        self.id_procedimento_padrao = id_procedimento_padrao
        self._cache = {}

    def resolver(self, item):
        """
        Devolve {"id": str, "achado": dict, "processo": str} para a linha.

        Prioridade: coluna "Processo SEI" da linha; quando ela existe e coincide
        com o --processo da CLI reaproveitamos a busca ja feita; quando nao ha
        coluna e nem CLI, abortamos. Nenhum processo e adivinhado.
        """
        numero = (item.get("processo") or "").strip()
        if not numero:
            if self.id_procedimento_padrao:
                return {
                    "id": self.id_procedimento_padrao,
                    "achado": None,
                    "processo": self.args.processo or "(da CLI --id-procedimento)",
                }
            raise LinhaSemProcesso(
                "'Processo SEI' em branco na planilha"
            )

        if numero in self._cache:
            return self._cache[numero]

        if self.args.processo and numero == self.args.processo and self.id_procedimento_padrao:
            resultado = {
                "id": self.id_procedimento_padrao,
                "achado": None,
                "processo": numero,
            }
            self._cache[numero] = resultado
            return resultado

        achado, encontrado = self.api._localizar_processo_na_caixa(
            numero, mesmo_assim=getattr(self.args, "mesmo_assim", False)
        )
        if not encontrado:
            resultado = {
                "id": None,
                "achado": None,
                "processo": numero,
                "erro": (
                    f"processo {numero} nao encontrado: a pesquisa rapida "
                    f"localizou o processo, mas ele esta FORA da caixa da unidade "
                    f"(sem marca de visualizado). Marque a opcao de abrir "
                    f"processo NAO VISUALIZADO para prosseguir."
                    if getattr(self.api, "_ultima_busca_localizou", False)
                    else f"processo {numero} nao encontrado (nem na pesquisa "
                         f"rapida, nem na caixa da unidade)"
                ),
            }
            self._cache[numero] = resultado
            return resultado

        resultado = {
            "id": achado.get("IdProtocolo") or achado.get("id_procedimento"),
            "achado": achado,
            "processo": numero,
        }
        if _processo_marca_nao_visualizado(achado) and not self.args.mesmo_assim:
            resultado["erro"] = (
                f"processo {numero} esta NAO VISUALIZADO - abrir iniciaria prazo; "
                "use --mesmo-assim se tiver certeza"
            )
        self._cache[numero] = resultado
        return resultado


def _preflight_lote(api, itens, args, id_procedimento_padrao=None, somente_plano=False):
    """
    Roda ANTES de qualquer gravacao: resolve todos os processos do lote (com
    cache), aplica a trava de NAO VISUALIZADO e aborta listando tudo que deu
    errado. Evita o pior cenario do modo lote - gravar 40 documentos e so
    descobrir, no fim, que a linha 41 aponta para um processo inexistente.

    Devolve o dicionario {numero_do_item: id_procedimento}.
    """
    resolvido = _ResolvedorProcessos(api, args, id_procedimento_padrao)
    por_id = {}
    falhas = []
    ignoradas = []
    usados = {}  # numero do processo -> {"id": str, "linhas": int}

    for item in itens:
        indice = item["indice"]
        try:
            resultado = resolvido.resolver(item)
        except LinhaSemProcesso as erro:
            # Nao e falha: nao ha processo onde gravar. A linha e pulada, e o
            # operador ve a lista no resumo para notar um eventual erro de
            # digitacao.
            ignoradas.append((indice, item["dados"]))
            continue
        except ValueError as erro:
            falhas.append(f"linha {indice}: {erro}")
            continue
        if resultado.get("erro"):
            falhas.append(f"linha {indice}: {resultado['erro']}")
            continue
        por_id[indice] = resultado["id"]
        registro = usados.setdefault(resultado["processo"], {"id": resultado["id"], "linhas": 0})
        registro["linhas"] += 1

    if ignoradas:
        nomes = ", ".join(
            f"{indice} ({d.get('Nome Servidor', '').strip()})" for indice, d in ignoradas[:40]
        )
        if len(ignoradas) > 40:
            nomes += f", ... e mais {len(ignoradas) - 40}"
        print(
            f"\nIGNORADAS: {len(ignoradas)} linha(s) sem 'Processo SEI' - "
            "nao ha processo onde incluir documento, entao nada e gravado nelas."
        )
        print(f"  linhas: {nomes}")

    print("\nPRE-VOO: conferindo processos antes de gravar")
    for numero, registro in usados.items():
        print(f"  {numero} -> id {registro['id']} ({registro['linhas']} linha(s))")
    print(f"  {len(usados)} processo(s) distinto(s) no lote.")

    if not por_id and ignoradas:
        sys.exit(
            "Nenhuma linha da planilha tem 'Processo SEI' preenchido: nao ha "
            "nenhum processo para_include. Confira se a coluna foi preenchida."
        )

    if falhas:
        print("\nABORTADO no pre-voo - NENHUM documento foi criado:")
        for linha in falhas:
            print(f"  - {linha}")
        sys.exit(1)

    if somente_plano:
        print("\nPLANO (--plano): nenhuma gravacao sera feita. Resumo:")
        for item in itens:
            if item["indice"] not in por_id:
                # Linha sem 'Processo SEI': nada sera criado para ela.
                continue
            d = item["dados"]
            print(
                f"  linha {item['indice']:>3} | {item['processo'] or '(CLI)'} | "
                f"id {por_id[item['indice']]} | termo {d.get('Nº do termo', '?')} | "
                f"{d.get('Nome Servidor', '')}"
            )
        print(f"\n{len(itens)} linha(s) prontas. Rode sem --plano para gravar.")
        return por_id

    return por_id


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

    bloco1 = str(getattr(args, "bloco1", "") or "").strip()
    bloco2 = str(getattr(args, "bloco2", "") or "").strip()
    if bloco1 and bloco1 == bloco2:
        sys.exit(
            f"Os dois blocos informedos sao o mesmo ({bloco1}). Informe dois "
            "blocos diferentes, ou deixe o segundo vazio."
        )
    print(
        f"Blocos de assinatura: "
        f"1={bloco1 or '(nao informado)'} | 2={bloco2 or '(nao informado)'}"
    )
    if not (bloco1 or bloco2):
        print(
            "  -> Nenhum bloco informado: os documentos serao criados e "
            "mesclados, mas NAO entram em bloco de assinatura."
        )

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

    # Pre-voo: nenhum documento e criado antes de TODOS os processos serem
    # resolvidos e validados (incluindo a trava de NAO VISUALIZADO).
    if getattr(args, "plano", False):
        _preflight_lote(
            api, itens, args, id_procedimento_padrao, somente_plano=True
        )
        return
    por_id = _preflight_lote(api, itens, args, id_procedimento_padrao)

    concluidas = []
    # Separada de "concluidas" de proposito: essa lista guarda
    # tuplas e o resumo final desempacota em tres campos.
    ignoradas_no_lote = []
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

        if indice not in por_id:
            # Linha ignorada no pre-voo (sem 'Processo SEI'). Sem este teste
            # o por_id[indice] levantaria KeyError aqui, DEPOIS do pre-voo
            # ter saido bem - a falha apareceria como erro de programa, longe
            # da causa real.
            print(
                f"\nLinha {indice} ignorada: 'Processo SEI' em branco "
                f"({dados.get('Nome Servidor', '')}). Nada a fazer."
            )
            ignoradas_no_lote.append(indice)
            continue

        id_procedimento = por_id[indice]
        try:
            numero_processo = item.get("processo") or ""
            id_doc = api.incluir_documento_interno(
                id_procedimento=id_procedimento,
                numero=numero_processo,
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
            if not api.editar_conteudo_documento(
                id_procedimento, id_doc, dados, numero_processo
            ):
                raise ErroInclusaoDocumento("SEI nao confirmou o salvamento.", etapa="lote")

            # Ultima etapa do ciclo: o documento recem-mesclado entra nos blocos
            # de assinatura informados na tela. Um bloco por chamada (o metodo
            # le a tela de novo a cada vez, porque o estado do SEI muda).
            for rotulo, numero_bloco in (("Bloco 1", bloco1), ("Bloco 2", bloco2)):
                if not numero_bloco:
                    continue
                api.incluir_em_bloco_assinatura(
                    id_procedimento=id_procedimento,
                    id_documento=id_doc,
                    numero_bloco=numero_bloco,
                    numero=numero_processo,
                    disponibilizar=args.disponibilizar_bloco,
                )
                print(
                    f">> Documento {id_doc} incluido no {rotulo} ({numero_bloco}) "
                    f"e {'disponibilizado' if args.disponibilizar_bloco else 'incluido'}."
                )
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
    if ignoradas_no_lote:
        print(
            f"Ignoradas por falta de 'Processo SEI' ({len(ignoradas_no_lote)}): "
            + ", ".join(str(i) for i in ignoradas_no_lote)
        )


