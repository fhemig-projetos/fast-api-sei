# -*- coding: utf-8 -*-
"""
A orquestradora do fluxo de inclusao/edicao.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from scripts_read.app3 import APIExtratorSEI, BASE_SEI, UNIDADE_ALVO, _limpar

from .arvore import ExtratorArvore
from .auditoria import AuditoriaEscrita
from .blocos import GerenciadorBlocos
from .configuracao import (
    MARCADORES_DO_MODELO,
    REDIRECIONAMENTOS,
    REGRAS_MERGE,
    REGRAS_OBRIGATORIAS,
)
from .erros import ErroInclusaoDocumento
from .mesclagem import MescladorDocumentos
from .modelo import ResolvedorModelo
from .payloads import MontadorPayload
from .transporte import TransporteEscrita

class InclusaoDocumentoSEI(AuditoriaEscrita, TransporteEscrita, MontadorPayload, ExtratorArvore, MescladorDocumentos, ResolvedorModelo, GerenciadorBlocos, APIExtratorSEI):
    """
    Escrita de documentos no SEI-MG.

    Herda de APIExtratorSEI apenas autenticar()/trocar_unidade() e a raspagem da
    caixa (navegar_para_controle). Todas as requisições deste fluxo passam por
    _ir(), que possui whitelist/blacklist próprias de ESCRITA.
    """

    def __init__(self, usuario, senha, unidade_alvo=None):
        super().__init__(usuario, senha)
        self.unidade_alvo = unidade_alvo or UNIDADE_ALVO
        self.salvar_telas = False
        # A ultima pesquisa rapida localizou o processo mas nao pode afirmar a
        # marca de visualizado? Serve para a mensagem de erro distinguir
        # "nao achei em lugar nenhum" de "achei, mas a marca nao se aplica".
        self._ultima_busca_localizou = False

    def trocar_unidade(self):
        """Igual ao app3, mas com a unidade alvo configuravel (--unidade)."""
        if self.unidade_atual == self.unidade_alvo:
            return True
        url = self._link_trocar_unidade(self.html_home)
        if not url:
            # Sem o link na tela nao ha infra_hash legitimo: em vez de chutar um
            # valor (regra de ouro), procuramos o hash de qualquer link da propria
            # pagina. Sem hash, o proprio SEI recusa - e o operador ve o motivo.
            m_hash = re.search(r"infra_hash=([0-9a-f]{32,})", self.html_home or "")
            if not m_hash:
                print(
                    "  Nao encontrei o link de troca nem um infra_hash na tela inicial; "
                    "a troca de unidade nao pode ser feita com seguranca."
                )
                return False
            print("  Link de troca ausente; montando a URL com o hash da tela inicial.")
            url = (
                "controlador.php?acao=infra_trocar_unidade&"
                "acao_origem=infra_trocar_unidade&infra_sistema=100000100&"
                f"infra_unidade_atual={self.unidade_alvo}&"
                f"infra_hash={m_hash.group(1)}"
            )
        if not url.startswith("http"):
            url = BASE_SEI + url
        print(f"Trocando para a unidade {self.unidade_alvo}...")
        try:
            pagina_troca = self._requisicao_segura("get", url, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"  GET da tela de troca falhou: {exc}")
            return False
        action, payload = self._montar_payload_troca(pagina_troca.text, self.unidade_alvo)
        if not action:
            print("  Formulario de troca de unidade nao encontrado na resposta.")
            return False
        try:
            self._requisicao_segura("post", action, data=payload, allow_redirects=False, timeout=30)
        except UnicodeDecodeError:
            pass
        except requests.exceptions.RequestException as exc:
            print(f"  POST da troca falhou: {exc}")
            return False
        # Confere se a troca REALMENTE aconteceu. Antes o codigo assumia que sim
        # e seguia operando: se o SEI recusasse (usuario sem permissao na
        # unidade), o programacontinuaria procurando processo na unidade
        # errada e o erro apareceria no fim, sem relacao com a causa real.
        try:
            confirmacao = self._requisicao_segura(
                "get", BASE_SEI + "controlador.php?acao=principal", timeout=30
            )
        except requests.exceptions.RequestException as exc:
            print(f"  Nao consegui confirmar a troca de unidade: {exc}")
            return False
        self._detectar_unidade_atual(confirmacao.text)
        if self.unidade_atual != self.unidade_alvo:
            print(
                f"  O SEI recusou a troca para a unidade {self.unidade_alvo} "
                f"(continua em {self.unidade_atual or 'desconhecida'}). "
                "Verifique se este usuario tem permissao nessa unidade."
            )
            return False
        print(f"  Unidade confirmada: {self.unidade_atual}")
        return True

    def _buscar_processo_pela_pesquisa(self, numero, mesmo_assim=False):
        """
        Localiza o processo pelo campo de PESQUISA RAPIDA do SEI.

        A alternativa antiga - varrer a caixa de "Recebidos" pagina por pagina -
        so enxerga os primeiros processos da fila: numa unidade grande, um
        processo antigo fica depois da ultima pagina e nunca e encontrado. A
        pesquisa do topo existe em toda tela autenticada e acha pelo numero,
        esteja o processo onde estiver.

        DIVISAO DE PAPEL (a parte que importa para a seguranca):

          - a PESQUISA e a descoberta: acha o processo pelo numero, sem
            limite de paginas, e entrega o link de abertura;
          - a CAIXA e a autoridade: e a unica tela que garante a marca
            "Nao Visualizado".

        Por isso so devolvemos o item quando o resultado traz a marca (branco
        ou vermelho). Sem marca devolvemos None e o chamador cai no scraping
        da caixa, como fazia antes.

        mesmo_assim: consentimento explicito do operador (o checkbox "Abrir
        processo NAO VISUALIZADO"). Ele nao e so um furador da trava - e o que
        autoriza abrir quando a marca NAO pode ser lida, porque o processo
        esta fora da caixa da unidade. Antes disso nao existia: o operador
        marcava a caixa achando que resolveria, mas ela so era consultada
        depois que o processo ja tinha sido localizado, e os processos fora
        da caixa falhavam antes de ela ter alguma efeito.

        REGRA DE OURO nº 2: o infra_hash vem do action do proprio formulario da
        tela - nunca e escrito, previsto nem reaproveitado de outra sessao.
        """
        alvo = _limpar(numero or "").replace(" ", "")
        if not alvo:
            return None

        # O campo de busca fica no topo de toda tela autenticada, mas nao em
        # todas: procuramos na caixa e, se ela nao tiver, na home. Sem isso, um
        # redirecionamento ou uma sessao em outra tela derrubaria a busca em
        # silencio - e o lote voltaria a depender so da pagina 1 da caixa.
        form = None
        for html in (self.navegar_para_controle(), self.html_home):
            if not html:
                continue
            sopa_pagina = BeautifulSoup(html.encode("utf-8", "replace"), "html.parser")
            form = sopa_pagina.find("form", {"id": "frmProtocoloPesquisaRapida"})
            if form is not None:
                break
        if form is None:
            print("  Tela sem o campo de pesquisa rapida.")
            return None

        acao = self._action(form, base_url=BASE_SEI)
        if not acao:
            return None

        dados = self._extrair_hidden_inputs(form)
        campo = next(
            (n for n in ("txtPesquisaRapida", "txtProtocoloPesquisa", "q")
             if n in dados),
            "txtPesquisaRapida",
        )
        dados[campo] = numero

        try:
            resposta, destino = self._ir(
                "post", acao, data=dados, allow_redirects=False, timeout=30
            )
        except Exception as exc:  # noqa: BLE001 - busca e conveniencia
            print(f"  Pesquisa rapida indisponivel ({exc}); usando a caixa.")
            return None
        self._salvar_tela("0a_pesquisa_rapida", resposta)

        # O SEI responde a busca rapida com 302 para o processo:
        #   ...acao=procedimento_trabalhar&acao_origem=protocolo_pesquisa_rapida
        #       &id_protocolo=940848375&...
        # Repare em "id_protocolo". A busca procurava "id_procedimento" e nunca
        # casou: as 249 respostas reais vieram com o corpo VAZIO (por isso os
        # HTMLs salvos com 0 byte) e o resultado era descartado, rendendo
        # "processo nao encontrado" para todos os processos da planilha.
        # Aceitamos as duas grafias: o campo da caixa e "id_procedimento", o
        # redirecionamento da busca e "id_protocolo".
        m_id = re.search(r"[?&]id_(?:protocolo|procedimento)=(\d+)", destino or "")
        if m_id:
            # O redirecionamento aponta direto para o processo (a URL que abre
            # o prazo), mas nao traz a marca de visualizado. Nao devolvemos o
            # item ainda: a marca so existe na caixa, e "desconhecido" nunca
            # pode virar "pode abrir" (regra de ouro nº 1). Quem decide e o
            # consentimento explicito do operador (--mesmo-assim).
            if not mesmo_assim:
                self._ultima_busca_localizou = True
                print("  Processo localizado pela pesquisa, porem FORA da caixa "
                      "da unidade: sem a marca de visualizado. Marque a opcao "
                      "de abrir processo NAO VISUALIZADO para prosseguir.")
                return None
            print("  Processo localizado pela pesquisa (fora da caixa). "
                  "Abrindo sem marca de visualizado: operador confirmou.")
            return {"Processo": _limpar(numero), "IdProtocolo": m_id.group(1),
                    "NaoVisualizado": None, "busca": "rapida",
                    "Link": self._normalizar_url(destino)}

        sopa = self._sopa(resposta)
        for link in sopa.select("a[href]"):
            href = link.get("href") or ""
            if not any(t in href for t in self.TOKENS_ABERTURA):
                continue
            achou_id = re.search(r"[?&]id_(?:protocolo|procedimento)=(\d+)", href)
            if not achou_id:
                continue
            # O texto do LINK e o proprio numero do processo no SEI
            # (<a ... id_procedimento=X>2270.01.0064841/2026-56</a>). E ele
            # que precisa casar: comparar a linha inteira nao serviria, porque
            # o numero procurado sempre estaria ali - o do proprio link - e
            # o resultado errado passaria como se fosse o certo.
            texto_do_link = _limpar(link.get_text(" ", strip=True)).replace(" ", "")
            if alvo not in texto_do_link:
                continue
            classes = link.get("class") or []
            if "processoNaoVisualizado" in classes:
                estado = True
            elif "processoVisualizado" in classes:
                estado = False
            elif not mesmo_assim:
                # Nem vermelho nem branco no link: nao sabemos se o processo ja
                # foi lido. Nao afirmar o contrario - a caixa confirma.
                print("  Resultado da pesquisa sem marca de visualizado; "
                      "confirmando na caixa.")
                return None
            return {"Processo": _limpar(numero), "IdProtocolo": achou_id.group(1),
                    "NaoVisualizado": estado, "busca": "rapida",
                    "Link": self._normalizar_url(href)}
        return None

    def _localizar_processo_na_caixa(self, numero, limite_paginas=20,
                                      mesmo_assim=False):
        """
        Procura o processo: 1) pesquisa rapida, 2) varrendo a caixa.

        A pesquisa acha pelo numero em qualquer lugar; a caixa confirma a
        marca de visualizado quando o processo esta na caixa da unidade.
        """
        from scripts_read.app3 import raspar_pagina_recebidos

        # Caminho rapido: acha pelo numero, sem limite de paginas.
        self._ultima_busca_localizou = False
        rapido = self._buscar_processo_pela_pesquisa(numero, mesmo_assim)
        if rapido and rapido.get("IdProtocolo"):
            return rapido, True

        html = self.navegar_para_controle()
        if not html or not any(
            t in html for t in ("tblProcessosDetalhado", "tblProcessosRecebidos", "tblProcessos")
        ):
            raise ErroInclusaoDocumento(
                "Nao consegui carregar a caixa de processos da unidade atual.", etapa="localizar"
            )

        alvo = _limpar(numero or "").replace(" ", "")
        for pagina in range(1, limite_paginas + 1):
            itens, tem_proxima, action_url, form_data, _ = raspar_pagina_recebidos(html, pagina)
            for item in itens:
                if _limpar(item.get("Processo") or "").replace(" ", "") == alvo:
                    return item, True
            if not tem_proxima:
                print(f"  Fim da caixa na pagina {pagina}; processo nao encontrado.")
                break
            time.sleep(0.4)
            html = self._ir("post", action_url, data=form_data, timeout=30)[0].text
        return None, False

    def incluir_documento_interno(
        self,
        id_procedimento,
        id_serie,
        id_hipotese_legal="4",
        texto_inicial="N",
        dados_adicionais=None,
        confirmar=False,
        dry_run=False,
        salvar_telas=False,
        protocolo_modelo=None,
        id_documento_modelo=None,
        consultar_lupa_modelo=True,
        numero="",
    ):
        """
        Cria um documento interno no processo informado.

        Parâmetros
        ----------
        id_procedimento   : id interno do processo (ex.: "000000000")
        id_serie          : id da série/tipo de documento (ex.: "63" = Relatório;
                            "4110" = Cessão Especial Ônus Cedente)
        id_hipotese_legal : id da hipótese legal (ex.: "4") do nível Restrito
        texto_inicial     : "N" = branco | "T" = texto padrão (ignorado p/ "D")
        dados_adicionais  : dict extra p/ sobrescrever/inserir campos de negócio
        confirmar         : obrigatório True para qualquer gravação real
        dry_run           : prepara o payload final e NÃO envia o POST de criação
        salvar_telas      : grava o HTML de cada etapa em .\\ruas_inclusao\\
        protocolo_modelo  : protocolo do documento usado como "Documento Modelo"
                            (ex.: "148410511"). Força rdoTextoInicial = "D", preenche
                            txtProtocoloDocumentoTextoBase e consulta a lupa
                            documento_modelo_selecionar (GET de leitura) atrás do id
                            interno do modelo.
        id_documento_modelo : id interno do modelo (hdnIdDocumentoTextoBase). Opcional:
                            quando vazio, é descoberto na lupa; quando a lupa não
                            devolve o par id+protocolo, o POST segue só com o protocolo.
        consultar_lupa_modelo : False injeta só o protocolo no payload, sem o GET de
                            leitura na lupa documento_modelo_selecionar (válvula de
                            escape caso essa consulta interfira na sessão/hash).

        Retorno
        -------
        str  — id_documento criado (ex.: "000000000"), quando grava de verdade.
        dict — payload montado, quando dry_run=True (nada é gravado).

        Erros
        -----
        ErroInclusaoDocumento com a etapa descritiva em qualquer falha.
        """
        if not confirmar and not dry_run:
            raise ErroInclusaoDocumento(
                "Operação de ESCRITA cancelada. Passe confirmar=True (ou use --confirmar).",
                etapa="confirmacao",
            )

        id_procedimento, id_serie = str(id_procedimento).strip(), str(id_serie).strip()
        self.salvar_telas = bool(salvar_telas)
        modo = "DRY-RUN (simulacao)" if dry_run else "GRAVACAO"
        detalhe_modelo = f", documento modelo {protocolo_modelo}" if protocolo_modelo else ""
        self._registrar_auditoria_escrita(
            f"[{modo}] serie {id_serie} no processo {id_procedimento} "
            f"(hipotese legal {id_hipotese_legal}, "
            f"texto inicial {self._texto_inicial_efetivo(texto_inicial, protocolo_modelo)}"
            f"{detalhe_modelo})."
        )

        ids = {id_procedimento}

        link_proc = self._link_trabalhar_do_processo(id_procedimento, numero)
        if not link_proc:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado (nem na caixa, nem pela pesquisa rapida).",
                etapa="0_abrir_processo",
            )
        resp_proc, _ = self._ir("get", link_proc, ids_liberados=ids, timeout=30)
        self._salvar_tela("0_processo_aberto", resp_proc)

        iframe = self._sopa(resp_proc).select_one("iframe#ifrArvore")
        src_arvore = (iframe.get("src") if iframe is not None else "") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore (permissao ou layout diferente).",
                etapa="0_abrir_processo",
            )
        resp_arvore, _ = self._ir("get", src_arvore, ids_liberados=ids, timeout=30)
        self._salvar_tela("1_arvore", resp_arvore)
        href_tipo = self._link_tipo_na_arvore(resp_arvore)
        if not href_tipo:
            raise ErroInclusaoDocumento(
                "Botao 'Incluir Documento' (documento_escolher_tipo) nao encontrado na arvore. "
                "Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )

        url_tipo = self._normalizar_url(href_tipo)
        resp_tipo, _ = self._ir("get", url_tipo, ids_liberados=ids, timeout=30)
        form_tipo, campos_tipo = self._form_e_payload(
            self._sopa(resp_tipo),
            ids=("frmDocumentoEscolherTipo", "frmInfraSelecaoSerie"),
            campos=("id_serie", "selSerie", "txtSerie"),
        )
        if form_tipo is None:
            raise ErroInclusaoDocumento(
                "Tela de escolha de tipo nao expoe o formulario de series "
                "(id_serie/selSerie). Verifique permissao do usuario no processo.",
                etapa="1_escolher_tipo",
            )
        self._salvar_tela("1_escolher_tipo", resp_tipo)
        action_escolher = self._action(form_tipo, base_url=url_tipo)

        payload = {c: v for c, v in campos_tipo.items() if not c.startswith("chkInfraItem")}
        self._atribuir_campo(payload, ("id_serie", "selSerie", "hdnIdSerie"), id_serie)
        resp_escolha, loc_gerar = self._ir(
            "post", action_escolher, data=payload, ids_liberados=ids,
            allow_redirects=False, timeout=30,
        )

        if resp_escolha.status_code == 200:
            resp_gerar = resp_escolha
        elif resp_escolha.status_code in REDIRECIONAMENTOS:
            if not loc_gerar:
                raise ErroInclusaoDocumento(
                    "escolher_tipo respondeu sem header Location.", etapa="1_escolher_tipo"
                )
            url_gerar = self._normalizar_url(loc_gerar)
            if "documento_gerar" not in url_gerar.lower():
                raise ErroInclusaoDocumento(
                    f"Location inesperado (nao aponta para documento_gerar): {url_gerar}",
                    etapa="2_tela_gerar",
                )
            resp_gerar, _ = self._ir("get", url_gerar, ids_liberados=ids, timeout=30)
        else:
            raise ErroInclusaoDocumento(
                f"Escolha do tipo nao redirecionou para documento_gerar "
                f"(HTTP {resp_escolha.status_code}).",
                etapa="1_escolher_tipo",
            )
        self._salvar_tela("2_documento_gerar", resp_gerar)

        sopa_gerar = self._sopa(resp_gerar)
        modelo = {"protocolo": "", "id_documento": ""}
        if protocolo_modelo:
            modelo = self._resolver_documento_modelo(
                sopa_gerar,
                protocolo_modelo,
                id_documento_modelo,
                ids,
                consultar_lupa=consultar_lupa_modelo,
            )
        form_gerar, payload = self._payload_gerar(
            sopa_gerar,
            id_hipotese_legal,
            texto_inicial,
            dados_adicionais,
            protocolo_modelo=modelo["protocolo"],
            id_documento_modelo=modelo["id_documento"],
        )
        if not payload:
            raise ErroInclusaoDocumento(
                f"Formulário frmDocumentoGerar não encontrado ou sem campos "
                f"(HTTP {resp_gerar.status_code}) — a tela de geração pode ter carregado "
                "uma mensagem de erro (use --salvar-telas para inspecionar).",
                etapa="3_payload_gerar",
            )
        action_gerar = self._action(form_gerar, base_url=action_escolher)
        if not action_gerar:
            raise ErroInclusaoDocumento(
                "action do frmDocumentoGerar não localizado.", etapa="3_payload_gerar"
            )

        if protocolo_modelo:
            print(
                "  Documento Modelo: rdoTextoInicial = "
                f"{payload.get('rdoTextoInicial', '')}, "
                f"txtProtocoloDocumentoTextoBase = "
                f"{payload.get('txtProtocoloDocumentoTextoBase', '')}, "
                f"hdnIdDocumentoTextoBase = '{payload.get('hdnIdDocumentoTextoBase', '')}'"
            )
        if dry_run:
            print("\n[Dry-run] POST documento_gerar seria enviado para:")
            print("  ", action_gerar)
            print("[Dry-run] Payload (campos relevantes):")
            for chave in sorted(payload):
                valor = str(payload[chave])
                print(f"  {chave} = {valor[:120]}{'...' if len(valor) > 120 else ''}")

            return payload

        resp_final, loc_final = self._ir(
            "post", action_gerar, data=payload, ids_liberados=ids,
            allow_redirects=False, timeout=30,
        )
        if resp_final.status_code not in REDIRECIONAMENTOS:
            self._salvar_tela("3_erro_gerar", resp_final, forcar=True)
            raise ErroInclusaoDocumento(
                f"documento_gerar não redirecionou após criar "
                f"(HTTP {resp_final.status_code}).{self._mensagens_erro(resp_final)}",
                etapa="4_capturar_id",
            )
        if not loc_final:
            raise ErroInclusaoDocumento(
                f"documento_gerar respondeu {resp_final.status_code} sem header Location.",
                etapa="4_capturar_id",
            )
        m = re.search(r"[?&]id_documento=(\d+)", loc_final) or re.search(
            r"id_documento=(\d+)", loc_final, flags=re.I
        )
        if not m:
            raise ErroInclusaoDocumento(
                f"Não foi possível extrair id_documento do Location: {loc_final}",
                etapa="4_capturar_id",
            )
        self._registrar_auditoria_escrita(
            f"[SUCESSO] id_documento criado = {m.group(1)} ({loc_final})"
        )
        return m.group(1)

    def editar_conteudo_documento(
        self, id_procedimento, id_documento, dados, numero=""
    ):
        """
        Mescla os dados da planilha no conteudo do documento e SALVA no SEI (mail merge).

        Reproduz a cadeia capturada no Network do clique no lapis (Editar Conteudo),
        sem hardcode de hash nem de campo dinamico (regra de ouro):
          1. arvore do processo (ja visualizado na inclusao);
          2. href do NO do documento -> arvore "com o documento selecionado", onde o
             lapis e as URLs do editor sao renderizados (com hash fresco);
          3. ajax documento_verificar_assinatura (GET de leitura, como o browser);
          4. editor_montar -> TODOS os textarea txaEditor_XXXXX (cabecalho, corpo,
             rodape...), hidden inputs (infra_hash) e a action do form;
          5. merge por REGRAS_MERGE em cada parte, com contador por regra;
          6. ajax corfirmacao_atualizacao_documento (confirmacao antes de salvar);
          7. POST em editor/editor_processar.php?acao=editor_salvar (confirma "OK").

        dados: {coluna da planilha: valor} (ver COLUNAS_MERGE).
        numero: numero SEI do processo, usado SO para reabrir o processo quando
        ele nao esta na pagina 1 da caixa (ver _link_trabalhar_do_processo).
        Retorna True quando o SEI confirma o salvamento; falhas viram ErroInclusaoDocumento.
        """
        id_procedimento = str(id_procedimento).strip()
        id_documento = str(id_documento).strip()
        dados = dados or {}
        if not id_procedimento or not id_documento:
            raise ErroInclusaoDocumento(
                "id_procedimento e id_documento sao obrigatorios para editar o conteudo.",
                etapa="5_editor_montar",
            )
        if not dados:
            raise ErroInclusaoDocumento(
                "dados da planilha vazios - nada a mesclar no documento.",
                etapa="5_editor_montar",
            )
        ids = {id_procedimento}
        self._registrar_auditoria_escrita(
            f"[EDITOR] mail merge no documento {id_documento} do processo "
            f"{id_procedimento} com {dados}."
        )

        # 1) Arvore do processo (ja visualizado na inclusao).
        link_proc = self._link_trabalhar_do_processo(id_procedimento, numero)
        if not link_proc:
            raise ErroInclusaoDocumento(
                "Link de abertura do processo nao encontrado (nem na caixa, nem pela pesquisa rapida).",
                etapa="5_editor_montar",
            )
        resp_proc, _ = self._ir("get", link_proc, ids_liberados=ids, timeout=30)
        iframe = self._sopa(resp_proc).select_one("iframe#ifrArvore")
        src_arvore = (iframe.get("src") if iframe is not None else "") or ""
        if not src_arvore:
            raise ErroInclusaoDocumento(
                "Tela do processo sem o iframe ifrArvore (permissao ou layout diferente).",
                etapa="5_editor_montar",
            )
        resp_arvore, _ = self._ir("get", src_arvore, ids_liberados=ids, timeout=30)
        self._salvar_tela("4a_arvore_editor", resp_arvore)

        # 2) Arvore COM o documento selecionado: o no traz
        # acao=arvore_visualizar&...&id_documento=<alvo>&infra_hash=<fresh>, e e
        # nessa variante que o SEI renderiza o link do editor.
        href_no = self._link_editor_na_arvore(
            resp_arvore, id_documento, acoes=("arvore_visualizar",)
        )
        resp_alvo = resp_arvore
        if href_no:
            resp_alvo, _ = self._ir("get", href_no, ids_liberados=ids, timeout=30)
            self._salvar_tela("4b_arvore_documento", resp_alvo)
        else:
            print(
                "  Aviso: link do no do documento nao encontrado na arvore; "
                "seguindo com a tela atual."
            )

        # 3) Verificacao de assinatura (GET de leitura): o browser so abre o editor
        # depois dela; aqui e fidelidade de fluxo (nao grava nada).
        url_assinatura = self._link_ajax_editor(
            resp_alvo, "documento_verificar_assinatura", id_documento
        )
        texto_assinatura = ""
        if url_assinatura:
            resp_assinatura, _ = self._ir("get", url_assinatura, ids_liberados=ids, timeout=30)
            texto_assinatura = (resp_assinatura.text or "").strip()
            print(
                f"  Verificacao de assinatura: HTTP {resp_assinatura.status_code} "
                f"({texto_assinatura[:60]!r})"
            )
        else:
            print("  Aviso: ajax de verificacao de assinatura nao encontrado; seguindo.")

        # 4) editor_montar: primeiro o link renderizado na propria tela; se nao
        # houver, ele pode vir na resposta do ajax acima.
        href_montar = self._link_editor_na_arvore(
            resp_alvo, id_documento, acoes=("editor_montar",)
        )
        if not href_montar and "editor_montar" in texto_assinatura:
            achado = re.search(
                r"""(controlador\.php\?acao=editor_montar[^"'<\s]+)""", texto_assinatura
            )
            href_montar = achado.group(1) if achado else ""
        if not href_montar:
            raise ErroInclusaoDocumento(
                f"editor_montar do documento {id_documento} nao localizado (nem na "
                "arvore selecionada, nem na resposta do ajax) - rode com "
                "--salvar-telas e inspecione 4b_arvore_documento_*.html.",
                etapa="5_editor_montar",
            )
        resp_editor, _ = self._ir("get", href_montar, ids_liberados=ids, timeout=30)
        self._salvar_tela("4_editor_montar", resp_editor)
        partes, form = self._todos_editores(resp_editor)
        if not partes or form is None:
            raise ErroInclusaoDocumento(
                "Textarea txaEditor_* ou form de salvamento ausentes na tela do editor "
                f"(HTTP {resp_editor.status_code}; use --salvar-telas).",
                etapa="5_editor_montar",
            )
        print(f"  Editor: {len(partes)} parte(s) -> {', '.join(partes)}")

        action_editor = self._normalizar_url(self._limpar_url(form.get("action")) or "")
        if "editor_processar.php" not in action_editor:
            m_acao = re.search(
                r"""action=["']([^"']*editor_processar\.php[^"']*)["']""",
                resp_editor.text or "",
                flags=re.I,
            )
            action_editor = self._normalizar_url(m_acao.group(1)) if m_acao else ""
        if "editor_processar.php" not in action_editor:
            raise ErroInclusaoDocumento(
                "action do form do editor (editor/editor_processar.php) nao exposta - "
                "nada foi salvo (use --salvar-telas).",
                etapa="6_editor_salvar",
            )

        # 5) Merge em CADA parte do documento (o corpo do texto e a parte editavel).
        payload = self._extrair_hidden_inputs(form)
        contador = {}
        for nome, html in partes.items():
            html_mesclado, contagem = self._mesclar_html(html, dados)
            for chave, qtde in contagem.items():
                contador[chave] = contador.get(chave, 0) + qtde
            payload[nome] = html_mesclado
        print("  Merge:")
        for chave, _coluna, _padrao, _grupo, _trat in REGRAS_MERGE:
            qtde = contador.get(chave, 0)
            print(f"    {chave}: {qtde} substituicao(oes)")
            if not qtde:
                print("      AVISO: marcador nao encontrado no modelo.")
        texto_final = " ".join(payload[nome] for nome in partes)
        # O documento traz um <style> com fonte embutida em base64, e esse
        # base64 contem a sequencia "XX" ao acaso. Varrer o texto inteiro
        # acusava "marcador remanescente" mesmo com o titulo ja preenchido.
        # A trava olha o conteudo SEM o bloco de estilo; o payload continua
        # sendo enviado completo, com a formatacao preservada.
        sem_estilo = re.sub(r"<style\b.*?</style>", " ", texto_final,
                            flags=re.I | re.S)
        # O documento embute fonte em base64, e o base64 contem "XX" ao acaso.
        sem_estilo = re.sub(r"base64,[A-Za-z0-9+/=\s]+", " ", sem_estilo)
        remanescentes = [m for m in MARCADORES_DO_MODELO if m in sem_estilo]
        # Sinal mais confiavel que procurar texto: a CONTAGEM das regras. Se
        # uma regra obrigatoria casou zero vez, o marcador nao foi substituido,
        # e isso vale mesmo quando o texto do marcador tem grafia diferente da
        # lista acima.
        faltando = [r for r in REGRAS_OBRIGATORIAS
                    if not contador.get(r) and not (
                        r == "Nº do termo (titulo)" and contador.get(
                            "Nº do termo (titulo sem span)"))]
        if faltando:
            raise ErroInclusaoDocumento(
                f"Regras de merge que nao casaram com este modelo: {faltando} "
                "- o documento NAO foi salvo. O modelo da unidade deve ter "
                "esses marcadores; ajuste as regras em REGRAS_MERGE.",
                etapa="6_editor_salvar",
            )
        if remanescentes:
            raise ErroInclusaoDocumento(
                f"Marcadores do modelo ainda presentes apos o merge: {remanescentes} "
                "- nada foi salvo.",
                etapa="6_editor_salvar",
            )

        # 6) Confirmacao antes de salvar (o editor consulta esse ajax e so submete
        # depois; se ele falhar, o browser tambem aborta).
        url_confirmacao = self._link_ajax_editor(
            resp_editor, "corfirmacao_atualizacao_documento", id_documento
        )
        if url_confirmacao:
            resp_conf, _ = self._ir("get", url_confirmacao, ids_liberados=ids, timeout=30)
            if resp_conf.status_code != 200:
                raise ErroInclusaoDocumento(
                    f"confirmacao de salvamento falhou (HTTP {resp_conf.status_code}) "
                    "- nada foi salvo.",
                    etapa="6_editor_salvar",
                )
            print(f"  Confirmacao: {(resp_conf.text or '').strip()[:120]!r}")
        else:
            print("  Aviso: ajax de confirmacao nao encontrado; seguindo para o POST.")

        # 7) POST editor_salvar (action lida do form; sem auto-redirect p/ auditar).
        resp_salvar, loc_salvar = self._ir(
            "post",
            action_editor,
            data=payload,
            ids_liberados=ids,
            allow_redirects=False,
            timeout=90,
        )
        self._salvar_tela("5_editor_salvar", resp_salvar)
        if resp_salvar.status_code in REDIRECIONAMENTOS:
            self._registrar_auditoria_escrita(
                f"[SUCESSO-EDITOR] conteudo salvo no documento {id_documento} "
                f"(Location: {loc_salvar or '(sem Location)'})"
            )
            return True
        if resp_salvar.status_code == 200:
            resposta = (self._sopa(resp_salvar).get_text(" ", strip=True) or "").strip()
            if resposta.startswith("OK"):
                self._registrar_auditoria_escrita(
                    f"[SUCESSO-EDITOR] conteudo salvo no documento {id_documento} "
                    f"(versao {resposta[2:] or '?'})."
                )
                print(f"  SEI confirmou: {resposta[:20]!r}")
                return True
            if "INFRA_VALIDACAO" in resposta or "COMPARACAO" in resposta:
                raise ErroInclusaoDocumento(
                    f"o SEI recusou o salvamento: {resposta[:300]}",
                    etapa="6_editor_salvar",
                )
            pendencias = self._mensagens_erro(resp_salvar)
            raise ErroInclusaoDocumento(
                f"resposta inesperada do editor_salvar: {resposta[:200] or '(vazia)'}"
                f"{pendencias}",
                etapa="6_editor_salvar",
            )
        raise ErroInclusaoDocumento(
            f"editor_salvar falhou (HTTP {resp_salvar.status_code})."
            f"{self._mensagens_erro(resp_salvar)}",
            etapa="6_editor_salvar",
        )

