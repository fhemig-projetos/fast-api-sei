# -*- coding: utf-8 -*-
"""
A linha de comando (argparse).
"""

import re
import sys

from scripts_read.app3 import SENHA, UNIDADE_ALVO, USUARIO

from .configuracao import CAMPOS_RESUMO_PROCESSO, NOME_PLANILHA_PADRAO, SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE
from .inclusao import InclusaoDocumentoSEI
from .lote import executar_lote
from .planilha import _processo_marca_nao_visualizado, carregar_dados_planilha, resolver_caminho_planilha

def main(argv=None):
    """CLI do fluxo de escrita: valida as travas, localiza o processo e inclui o documento."""
    import argparse

    parser = argparse.ArgumentParser(description="Inclusão de documento interno no SEI-MG.")
    parser.add_argument(
        "--id-procedimento", help="ID interno do processo (ex.: 000000000). Use OU --processo."
    )
    parser.add_argument(
        "--processo",
        help="Numero formatado (ex.: 0000.00.0000000/0000-00) - busca automatica na caixa da unidade",
    )
    parser.add_argument(
        "--unidade", default=UNIDADE_ALVO, help="ID da unidade onde o processo esta (padrao: 110007141)"
    )
    parser.add_argument(
        "--serie",
        default="63",
        help=(
            "ID da serie/tipo de documento (padrao 63 = Relatorio; "
            f"{SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE} = Cessao Especial Onus Cedente)"
        ),
    )
    parser.add_argument("--hipotese-legal", default="4", help="ID da hipotese legal do nivel Restrito (padrao 4)")
    parser.add_argument(
        "--texto-inicial",
        default=None,
        choices=["N", "T", "D"],
        help="N = branco (padrao) | T = texto padrao | D = documento modelo (usa --protocolo-modelo)",
    )
    parser.add_argument(
        "--protocolo-modelo",
        help=(
            "Protocolo do documento existente usado como 'Documento Modelo' "
            "(ex.: 148410511). Liga rdoTextoInicial=D e consulta a lupa "
            "documento_modelo_selecionar atras de hdnIdDocumentoTextoBase"
        ),
    )
    parser.add_argument(
        "--id-documento-modelo",
        help="Id interno do documento modelo (hdnIdDocumentoTextoBase), quando ja conhecido",
    )
    parser.add_argument(
        "--sem-consultar-modelo",
        action="store_true",
        help=(
            "Com --protocolo-modelo: nao faz o GET de leitura na lupa "
            "documento_modelo_selecionar; injeta apenas o protocolo no payload"
        ),
    )
    parser.add_argument(
        "--confirmar", action="store_true", help="Confirma e GRAVA o documento (obrigatorio para escrita real)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Prepara e exibe o payload, sem enviar o POST de criacao"
    )
    parser.add_argument("--salvar-telas", action="store_true", help="Salva o HTML de cada etapa em ruas_inclusao/")
    parser.add_argument(
        "--bloco1",
        default=None,
        help="ID do 1o bloco de assinatura (ex.: 7712033). Vazio = nao incluir",
    )
    parser.add_argument(
        "--bloco2",
        default=None,
        help="ID do 2o bloco de assinatura. Vazio = nao incluir",
    )
    parser.add_argument(
        "--nao-disponibilizar-bloco",
        dest="disponibilizar_bloco",
        action="store_false",
        help="No --lote, inclui no bloco sem disparar a disponibilizacao",
    )
    parser.set_defaults(disponibilizar_bloco=True)
    parser.add_argument(
        "--mesmo-assim", action="store_true", help="Prossegue mesmo se o processo estiver NAO VISUALIZADO"
    )
    parser.add_argument(
        "--planilha",
        nargs="?",
        const=NOME_PLANILHA_PADRAO,
        default=None,
        help=(
            "Excel de mesclagem (Mail Merge) com os dados do servidor. Sem valor "
            f"usa '{NOME_PLANILHA_PADRAO}' (raiz do projeto ou ~/Downloads). A "
            "edicao do conteudo ocorre apos a criacao real do documento."
        ),
    )
    parser.add_argument(
        "--linha",
        type=int,
        default=0,
        help="Indice da linha da planilha a mesclar (padrao 0 = primeira linha de dados)",
    )
    parser.add_argument(
        "--editar-documento",
        metavar="ID_DOCUMENTO",
        help=(
            "Aplica o Mail Merge em um documento JA criado, pulando a criacao "
            "(ex.: 900000001). Exige --planilha."
        ),
    )
    parser.add_argument(
        "--lote",
        action="store_true",
        help=(
            "Processa TODAS as linhas da planilha: cria um documento por servidor e "
            "aplica o Mail Merge em cada um. Falha para no primeiro erro."
        ),
    )
    parser.add_argument(
        "--ate-linha",
        type=int,
        default=None,
        help="Com --lote: processa da --linha ate este indice (exclusivo ou inclusivo).",
    )
    parser.add_argument(
        "--a-partir-de",
        type=int,
        default=None,
        help="Com --lote: indice inicial (para retomar apos uma falha).",
    )
    parser.add_argument(
        "--plano",
        action="store_true",
        help=(
            "Com --lote: apenas confere e mostra o plano (processo de cada linha) "
            "sem criar NENHUM documento."
        ),
    )
    args = parser.parse_args(argv)

    if args.linha < 0:
        parser.error("--linha deve ser um indice >= 0.")
    if (args.ate_linha is not None or args.a_partir_de is not None) and not args.lote:
        parser.error("--ate-linha/--a-partir-de so fazem sentido com --lote.")
    if args.ate_linha is not None and args.a_partir_de is not None:
        if args.a_partir_de > args.ate_linha:
            parser.error("--a-partir-de nao pode ser maior que --ate-linha.")
    if args.lote and args.editar_documento:
        parser.error("--lote nao pode ser combinado com --editar-documento.")
    if args.lote and args.dry_run:
        parser.error("--lote exige gravacao real: remova --dry-run ou processe linha a linha.")
    if args.lote and not args.planilha:
        parser.error("--lote exige --planilha.")
    if args.plano and not args.lote:
        parser.error("--plano so faz sentido com --lote.")
    if args.editar_documento:
        if not re.fullmatch(r"\d+", str(args.editar_documento).strip()):
            parser.error("--editar-documento deve conter apenas digitos (ex.: 900000001).")
        if not args.planilha:
            parser.error("--editar-documento exige --planilha (sem planilha nao ha mesclagem).")

    if not args.dry_run and not args.confirmar and not (args.lote and args.plano):
        parser.error("A gravacao e real: informe --confirmar (ou use --dry-run para simular.)")
    # Regra dos argumentos de processo: NUNCA os dois ao mesmo tempo. No --lote
    # nenhum e obrigatorio, porque cada linha traz o seu em "Processo SEI" e o
    # pre-voo resolve um a um (com cache).
    if bool(args.id_procedimento) and bool(args.processo):
        parser.error("Informe --id-procedimento OU --processo, nunca os dois.")
    if not args.lote and not args.id_procedimento and not args.processo:
        parser.error("Informe --id-procedimento OU --processo (um deles, nao nenhum).")
    if args.protocolo_modelo:
        args.protocolo_modelo = args.protocolo_modelo.strip()
        if not re.fullmatch(r"\d+", args.protocolo_modelo):
            parser.error("--protocolo-modelo deve conter apenas digitos (ex.: 148410511).")
        if args.texto_inicial and args.texto_inicial != "D":
            parser.error(
                f"--protocolo-modelo ja implica rdoTextoInicial=D; remova --texto-inicial {args.texto_inicial}."
            )
        if args.serie == parser.get_default("serie"):
            print(
                "Nota: o documento criado e do tipo informado em --serie; para Cessao "
                f"Especial Onus Cedente a serie e {SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE} "
                f"(--serie {SERIE_CESSAO_ESPECIAL_ONUS_CEDENTE})."
            )
    elif args.id_documento_modelo:
        parser.error("--id-documento-modelo so faz sentido junto de --protocolo-modelo.")
    elif args.texto_inicial == "D":
        parser.error("--texto-inicial D exige --protocolo-modelo (nao ha modelo sem protocolo).")
    texto_inicial = args.texto_inicial or ("D" if args.protocolo_modelo else "N")

    print("Autenticando no SEI-MG...")
    api = InclusaoDocumentoSEI(USUARIO, SENHA, unidade_alvo=args.unidade)
    # Sem isto, --editar-documento nao salvaria telas: quem liga a flag hoje e o
    # incluir_documento_interno, que nesse modo nem chega a ser chamado.
    api.salvar_telas = bool(args.salvar_telas)
    if not api.autenticar():
        sys.exit("Falha no login. Verifique credenciais/rede.")
    api.trocar_unidade()

    id_procedimento = args.id_procedimento
    if args.processo:
        print(f"Procurando o processo {args.processo} na caixa da unidade {args.unidade}...")
        achado, encontrado = api._localizar_processo_na_caixa(
            args.processo, mesmo_assim=getattr(args, "mesmo_assim", False)
        )
        if not encontrado:
            sys.exit(f"Processo {args.processo} nao encontrado na caixa da unidade {args.unidade}.")
        id_procedimento = achado.get("IdProtocolo") or achado.get("id_procedimento")
        print("\nProcesso localizado na caixa:")
        for chave in CAMPOS_RESUMO_PROCESSO:
            print(f"    {chave}: {achado.get(chave, '')}")

        if _processo_marca_nao_visualizado(achado) and not args.mesmo_assim:
            sys.exit(
                "ABORTADO: processo NAO VISUALIZADO - operacao de prazo sensivel. "
                "Se tiver certeza, rode com --mesmo-assim."
            )

    if args.lote:
        executar_lote(api, args, id_procedimento, texto_inicial)
        return

    if args.editar_documento:
        id_documento = str(args.editar_documento).strip()
        print(f"Mail Merge em documento existente: {id_documento} (criacao ignorada).")
    else:
        id_documento = api.incluir_documento_interno(
            id_procedimento=id_procedimento,
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

    print()
    if args.dry_run:
        if args.planilha:
            caminho_planilha = resolver_caminho_planilha(args.planilha)
            dados_merge = carregar_dados_planilha(caminho_planilha, args.linha)
            print(f"[Dry-run] Mesclagem leria {caminho_planilha} (linha {args.linha}):")
            for coluna, valor in dados_merge.items():
                print(f"  {coluna} = {valor}")
            print("[Dry-run] a edicao do conteudo so ocorre em gravacao real (--confirmar).")
        print("Dry-run concluido - nenhum documento foi gravado.")
    else:
        if args.editar_documento:
            print(f"Documento existente: {id_documento}")
        else:
            print(f"Documento criado com sucesso! id_documento = {id_documento}")
        if args.planilha:
            caminho_planilha = resolver_caminho_planilha(args.planilha)
            dados_merge = carregar_dados_planilha(caminho_planilha, args.linha)
            print(f"Mail Merge: {caminho_planilha} (linha {args.linha}):")
            for coluna, valor in dados_merge.items():
                print(f"  {coluna} = {valor}")
            if api.editar_conteudo_documento(
                id_procedimento, id_documento, dados_merge,
                getattr(args, "processo", "") or "",
            ):
                print("Conteudo do documento mesclado e salvo no SEI.")


