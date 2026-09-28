# -*- coding: utf-8 -*-
"""
Interface grafica (GUI) do fluxo de escrita do SEI-MG.

O QUE ESTA TELA FAZ
  1. pede a planilha Excel (Mail Merge);
  2. pede o numero do Bloco de Assinatura (guardado para a proxima etapa);
  3. pede o USUARIO e a SENHA de quem esta usando - nunca os de outra pessoa
     (o SEI registra o autor de cada documento criado);
  4. roda a automacao em uma THREAD separada, para a janela nunca congelar
     enquanto o backend cria e salva os documentos;
  5. mostra tudo em tempo real no console da tela.

POR QUE A THREAD E OBRIGATORIA
    Todo acesso a widgets do Tkinter precisa acontecer na thread principal. Se o
    loop do SEI rodasse na thread da interface, a janela ficaria branca e o
    Windows mostraria "Nao esta respondendo". Por isso a thread de trabalho
    escreve no console atraves de widget.after(0, ...) - que agenda a
    atualizacao para a thread principal.

O app4_inclusao.py NAO e alterado: importamos executar_lote e montamos o
argparse.Namespace que ele espera. As credenciais vao para os.environ
(SEI_USUARIO/SEI_SENHA), que e de onde o backend sempre leu.

Uso:  python interface_app.py
"""

import argparse
import os
import sys
import threading
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox

import credenciais_sei

# =============================================================================
#  Parametros do SEI - ajuste aqui quando a proxima etapa chegar
# =============================================================================

SERIE = "4110"                      # tipo: Cessao Especial Onus Cedente
PROTOCOLO_MODELO = "148410511"      # protocolo do "Documento Modelo"
UNIDADE = "110031457"               # unidade onde os processos estao
HIPOTESE_LEGAL = "4"                # nivel Restrito
TEXTO_INICIAL = "D"                 # D = usa o Documento Modelo

# Etiquetas da interface
TITULO = "Automacao SEI-MG — Cessao Especial"
LARGURA, ALTURA = 820, 700

# =============================================================================
#  Console: captura os prints do backend e joga na caixa de texto
# =============================================================================


class RedirecionadorConsole:
    """
    Substitui sys.stdout por um objeto que empurra cada linha para a CTkTextbox.

    A thread de trabalho NAO toca no widget: ela enfileira a mensagem e pede
    que a thread principal a escreva (after). Isso e o unico caminho seguro
    para atualizar o Tkinter de fora da thread principal - chamar
    textbox.insert() direto de outra thread corrompe a interface.
    """

    def __init__(self, widget):
        self.widget = widget
        self._original = sys.stdout
        self._linha = ""
        self.fechado = False

    def write(self, texto):
        """Recebe o que o print() escreveria. Reconstitui as linhas."""
        if self.fechado:
            return
        try:
            self._linha += texto
            while "\n" in self._linha:
                linha, self._linha = self._linha.split("\n", 1)
                self._mostrar(linha)
        except Exception:  # noqa: BLE001 - nunca derrubar o log por causa dele
            pass

    def flush(self):
        if self._linha:
            self._mostrar(self._linha)
            self._linha = ""

    def _mostrar(self, linha):
        # after(0, ...) = agenda para a thread principal. show="center" faz
        # aparecer de primeira.
        self.widget.after(0, self._inserir, linha)

    def _inserir(self, linha):
        self.widget.configure(state="normal")
        self.widget.insert("end", linha + "\n")
        self.widget.see("end")
        self.widget.configure(state="disabled")

    def restaurar(self):
        """Devolve o stdout original (chamado no fim da execucao)."""
        self.fechado = True
        sys.stdout = self._original

    def log(self, texto, prefixo="[INTERFACE] "):
        """Escreve de dentro da propria interface (com prefixo)."""
        self._mostrar(prefixo + texto)



# =============================================================================
#  Ponte com o app4_inclusao.py
# =============================================================================


def montar_args_lote(planilha: str, linha_inicio: int = 0, plano: bool = False):
    """
    Monta o argparse.Namespace que o executar_lote() do app4_inclusao.py espera.

    Nao alteramos o backend: os mesmos nomes de flag da CLI sao reproduzidos
    aqui como atributos. O teste scripts_write/testa_interface_gui.py confere
    que TODOS os atributos lidos pelo backend existem neste Namespace.
    """
    return argparse.Namespace(
        planilha=planilha,
        linha=linha_inicio,
        lote=True,
        a_partir_de=linha_inicio,
        ate_linha=None,
        plano=plano,
        # A gravacao ja foi confirmada na tela (messagebox) antes da thread
        # existir, entao aqui o backend pode ir direto na escrita real.
        confirmar=True,
        dry_run=False,
        mesmo_assim=False,
        editar_documento=None,
        processo=None,
        id_procedimento=None,
        serie=SERIE,
        hipotese_legal=HIPOTESE_LEGAL,
        texto_inicial=TEXTO_INICIAL,
        protocolo_modelo=PROTOCOLO_MODELO,
        id_documento_modelo=None,
        sem_consultar_modelo=False,
        unidade=UNIDADE,
        salvar_telas=False,
    )


def montar_args_linha(planilha: str, linha: int):
    """Mesma ideia do lote, mas para mesclar UMA linha em um processo fixo."""
    args = montar_args_lote(planilha, linha_inicio=linha)
    args.lote = False
    args.a_partir_de = None
    return args


# =============================================================================
#  Aplicacao
# =============================================================================


class AppAutomacaoSEI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(TITULO)
        self.geometry(f"{LARGURA}x{ALTURA}")
        self.resizable(False, False)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Estado
        self.caminho_planilha = ""
        self.executando = False
        self._redirecionador = None
        self._api = None

        self._montar_layout()
        self._carregar_credenciais_salvas()
        self.log = RedirecionadorConsole(self.console)
        self.log.log("Interface pronta. Selecione a planilha e clique em Iniciar.")

    # ------------------------------------------------------------------ layout
    def _montar_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        titulo = ctk.CTkLabel(
            self, text="Automacao SEI-MG", font=ctk.CTkFont(size=24, weight="bold")
        )
        titulo.grid(row=0, column=0, padx=24, pady=(24, 0), sticky="w")
        sub = ctk.CTkLabel(self, text="Cessao Especial — Mail Merge via planilha Excel")
        sub.grid(row=1, column=0, padx=24, pady=(0, 18), sticky="w")

        self._secao_credenciais()   # 2
        self._secao_planilha()     # 3
        self._secao_bloco()        # 4
        self._secao_console()      # 5
        self._secao_botao()        # 6

    def _secao_credenciais(self):
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=2, column=0, padx=24, pady=(0, 12), sticky="ew")
        quadro.grid_columnconfigure(1, weight=1)

        rotulo = ctk.CTkLabel(quadro, text="Usuario SEI:", font=ctk.CTkFont(weight="bold"))
        rotulo.grid(row=0, column=0, padx=(14, 8), pady=(12, 6), sticky="w")
        self.entrada_usuario = ctk.CTkEntry(quadro, placeholder_text="SEI_USUARIO")
        self.entrada_usuario.grid(row=0, column=1, padx=(0, 10), pady=(12, 6), sticky="ew")

    def _secao_planilha(self):
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=3, column=0, padx=24, pady=(0, 12), sticky="ew")
        quadro.grid_columnconfigure(0, weight=1)

        linha = ctk.CTkFrame(quadro, fg_color="transparent")
        linha.grid(row=0, column=0, padx=14, pady=(12, 6), sticky="ew")
        linha.grid_columnconfigure(0, weight=1)

        self.botao_planilha = ctk.CTkButton(
            linha, text="Selecionar Planilha Excel", width=210, height=34,
            command=self.selecionar_planilha,
        )
        self.botao_planilha.grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.rotulo_planilha = ctk.CTkLabel(
            linha, text="Nenhuma planilha selecionada", anchor="w"
        )
        self.rotulo_planilha.grid(row=0, column=1, sticky="ew")

        self.rotulo_colunas = ctk.CTkLabel(
            quadro, text="", anchor="w", font=ctk.CTkFont(size=11), wraplength=680, justify="left"
        )
        self.rotulo_colunas.grid(row=1, column=0, padx=14, pady=(0, 12), sticky="w")

    def _secao_bloco(self):
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=4, column=0, padx=24, pady=(0, 12), sticky="ew")
        quadro.grid_columnconfigure(1, weight=1)

        rotulo = ctk.CTkLabel(
            quadro, text="Bloco de Assinatura:", font=ctk.CTkFont(weight="bold")
        )
        rotulo.grid(row=0, column=0, padx=(14, 8), pady=12, sticky="w")
        self.entrada_bloco = ctk.CTkEntry(
            quadro, placeholder_text="Numero do Bloco de Assinatura"
        )
        self.entrada_bloco.grid(row=0, column=1, padx=(0, 14), pady=12, sticky="ew")
        # O numero do bloco e guardado para a proxima etapa da automacao; hoje
        # nao altera o documento (nao ha campo correspondente no SEI ainda).
        self.entrada_bloco.bind("<Return>", lambda _e: self._registrar_bloco())
        self.entrada_bloco.bind("<FocusOut>", lambda _e: self._registrar_bloco())

    def _secao_console(self):
        self.console = ctk.CTkTextbox(self, wrap="word", activate_scrollbars=True)
        self.console.grid(row=5, column=0, padx=24, pady=(0, 12), sticky="nsew")
        self.console.configure(state="disabled")

    def _secao_botao(self):
        self.botao_iniciar = ctk.CTkButton(
            self,
            text="Iniciar Automacao",
            height=52,
            font=ctk.CTkFont(size=18, weight="bold"),
            hover_color="#1f6aa5",
            command=self.iniciar_automacao,
        )
        self.botao_iniciar.grid(row=6, column=0, padx=24, pady=(0, 24), sticky="ew")

    def _registrar_bloco(self):
        """Guarda o numero do bloco para a proxima etapa da automacao."""
        self.bloco_assinatura = self.entrada_bloco.get().strip()
        return self.bloco_assinatura

    # ------------------------------------------------------------- credenciais
    def _carregar_credenciais_salvas(self):
        """Preenche os campos com o que foi salvo para ESTE usuario do Windows."""
        try:
            if not credenciais_sei.tem_credenciais():
                return
            dados = credenciais_sei.carregar()
        except credenciais_sei.ErroCredenciais as erro:
            self.etiqueta_credenciais.configure(text=f"Credencial anterior: {erro}")
            return
        if not dados.get("usuario"):
            return
        self.entrada_usuario.insert(0, dados["usuario"])
        self.entrada_senha.insert(0, dados.get("senha", ""))
        self.caixa_lembrar.select()
        self.etiqueta_credenciais.configure(
            text="Credencial carregada (somente neste usuario do Windows)."
        )

    def _limpar_credenciais(self):
        try:
            apagou = credenciais_sei.apagar()
        except credenciais_sei.ErroCredenciais as erro:
            self.etiqueta_credenciais.configure(text=str(erro))
            return
        self.etiqueta_credenciais.configure(
            text="Credenciais apagadas deste computador." if apagou else "Nada estava salvo."
        )

    def _obter_credenciais(self):
        """
        Le usuario/senha da tela. Devolve (usuario, senha) ou None se faltar algo.

        Fail-safe: sem credencial NAO tentamos o .env escondido - assim ninguem
        roda a automacao com a conta de outra pessoa sem perceber.
        """
        usuario = self.entrada_usuario.get().strip()
        senha = self.entrada_senha.get()
        if not usuario or not senha:
            self.log.log("Informe o usuario e a senha do SEI para continuar.")
            return None

        if self.caixa_lembrar.get():
            try:
                caminho = credenciais_sei.salvar(usuario, senha)
                self.etiqueta_credenciais.configure(text=credenciais_sei.aviso_credenciais())
                self.log.log(f"Credencial salva em: {caminho}")
            except credenciais_sei.ErroCredenciais as erro:
                self.etiqueta_credenciais.configure(text=str(erro))
                self.log.log(f"Nao foi possivel lembrar a senha: {erro}")
        else:
            # Desmarcou "lembrar": apaga o que estava salvo antes.
            try:
                credenciais_sei.apagar()
            except credenciais_sei.ErroCredenciais:
                pass

        credenciais_sei.aplicar_no_ambiente(usuario, senha)
        return usuario, senha

    # ----------------------------------------------------------------- acoes
    def selecionar_planilha(self):
        if self.executando:
            return
        caminho = filedialog.askopenfilename(
            title="Selecione a planilha de Mail Merge",
            filetypes=[("Planilhas Excel", "*.xlsx"), ("Todos os arquivos", "*.*")],
        )
        if not caminho:
            return
        self.caminho_planilha = caminho
        self.rotulo_planilha.configure(text=caminho)
        self.log.log(f"Planilha selecionada: {caminho}")
        self._mostrar_colunas(caminho)

    def _mostrar_colunas(self, caminho):

        """
        Le os cabecalhos do Excel e mostra na tela. E so leitura: nao grava nada
        e nao precisa de login, entao falha aqui e problema da planilha.
        """
        try:
            import pandas as pd

            df = pd.read_excel(caminho)
        except Exception as erro:  # noqa: BLE001 - erro de arquivo e do usuario
            self.rotulo_colunas.configure(text=f"Nao consegui ler o Excel: {erro}")
            return
        self.rotulo_colunas.configure(
            text=f"{len(df)} linha(s) - colunas: {', '.join(map(str, df.columns))}"
        )
    # ------------------------------------------------------- execucao (thread)
    def iniciar_automacao(self):
        """
        Ponto de entrada do botao: valida TUDO na thread principal e sobe a
        thread de trabalho. Nenhuma chamada de rede acontece aqui.
        """
        if self.executando:
            self.log.log("Ja existe uma execucao em andamento.")
            return
        if not self.caminho_planilha or not os.path.isfile(self.caminho_planilha):
            self.log.log("Selecione uma planilha Excel valida primeiro.")
            messagebox.showwarning("Planilha", "Selecione uma planilha Excel valida.")
            return

        credencial = self._obter_credenciais()
        if credencial is None:
            messagebox.showwarning("Credenciais", "Informe o usuario e a senha do SEI.")
            return
        usuario, _senha = credencial

        self._registrar_bloco()

        # Confirmacao explicita: e o equivalente ao --confirmar da linha de
        # comando. Sem este "ok" nada e gravado no SEI.
        if not messagebox.askyesno(
            "Confirmar",
            "Vai criar documentos no SEI usando o usuario:\n\n"
            f"    {usuario}\n\n"
            f"Planilha: {os.path.basename(self.caminho_planilha)}\n"
            f"Bloco de assinatura: {self.bloco_assinatura or '(nao informado)'}\n\n"
            "Cada linha da planilha cria UM documento. Confirma?",
        ):
            self.log.log("Operacao cancelada pelo usuario.")
            return

        self.executando = True
        self.botao_iniciar.configure(state="disabled", text="Executando...")
        self.botao_planilha.configure(state="disabled")
        self.entrada_usuario.configure(state="disabled")
        self.entrada_senha.configure(state="disabled")
        self.entrada_bloco.configure(state="disabled")

        # daemon=True: se a janela fechar no meio, a thread nao segura o
        # processo do Python. O log avisa que o lote pode ficar incompleto.
        thread = threading.Thread(target=self._thread_automacao, daemon=True)
        thread.start()

    def _thread_automacao(self):
        """
        Roda FORA da thread principal. Tudo que precisar da interface volta
        por self.after(...), e a escrita no console passa pelo Redirecionador.
        """
        try:
            # O backend le credenciais do ambiente: garantimos que sao as deste
            # operador antes de qualquer import que capture os valores.
            sys.stdout = self.log
            self.log.log("=" * 66)
            self.log.log(f"Usuario SEI: {self.entrada_usuario.get().strip()}")
            self.log.log(f"Planilha   : {self.caminho_planilha}")
            self.log.log(f"Bloco assinatura: {self.bloco_assinatura or '(nao informado)'}")
            self.log.log("=" * 66)

            # Import tardio: puxa o app4 (que le o .env) dentro da thread, ja
            # com as credenciais do operador no ambiente.
            from app4_inclusao import InclusaoDocumentoSEI, executar_lote
            from scripts_read.app3 import UNIDADE_ALVO

            self.log.log("Conectando ao SEI...")
            api = InclusaoDocumentoSEI(
                os.environ["SEI_USUARIO"],
                os.environ["SEI_SENHA"],
                unidade_alvo=UNIDADE,
            )
            if not api.autenticar():
                raise RuntimeError("Falha no login. Verifique usuario e senha.")
            api.trocar_unidade()

            args = montar_args_lote(self.caminho_planilha)
            executar_lote(api, args, None, TEXTO_INICIAL)
            self.log.log("Automacao concluida com sucesso.")

        except SystemExit:
            # O backend usa sys.exit() para abortar com mensagem (ex.: processo
            # NAO VISUALIZADO, coluna faltando). Dentro da thread isso nao
            # fecha a interface: registramos e encerramos so a execucao.
            self.log.log("Operacao interrompida pelo sistema (ver mensagem acima).")
        except Exception as erro:  # noqa: BLE001 - erro vira log, nunca traceback cru
            self.log.log(f"ERRO: {erro}")
            self.log.log(traceback.format_exc(limit=3))
        finally:
            sys.stdout = self.log._original
            self.after(0, self._finalizar_execucao)

    def _finalizar_execucao(self):
        """Volta a thread principal para reabilitar a interface."""
        self.executando = False
        self.botao_iniciar.configure(state="normal", text="Iniciar Automacao")
        self.botao_planilha.configure(state="normal")
        self.entrada_usuario.configure(state="normal")
        self.entrada_senha.configure(state="normal")
        self.entrada_bloco.configure(state="normal")
        self.log.log("-" * 66)


def main():
    app = AppAutomacaoSEI()
    app.mainloop()


if __name__ == "__main__":
    main()
