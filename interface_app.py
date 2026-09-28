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
argparse.Namespace que ele espera. As credenciais da tela sao repassadas
EXPLICITAS ao construtor da API, e tambem injetadas em os.environ
(SEI_USUARIO/SEI_SENHA) ANTES e DEPOIS do import - assim o login e sempre o de
quem esta na tela, exista ou nao um arquivo de configuracao na maquina.

COMO GRAVAR DE VERDADE
    A constante SIMULAR, no topo deste arquivo, controla se a tela grava. Com
    SIMULAR = True ela faz login, troca de unidade, le a planilha e resolve os
    processos, mas PARA ANTES de criar ou editar qualquer documento (o "--plano"
    da linha de comando) - util para conferences antes de gravar. Com False,
    cada linha da planilha cria um documento no SEI, no nome de quem esta na
    tela. O modo em que a tela esta fica escrito na faixa colorida e na
    pergunta de confirmacao.

LGPD - POR QUE A SENHA E PEDIDA A CADA USO
    Nada e gravado no computador: nem usuario, nem senha, nem arquivo de
    configuracao. O executavel nao leva credencial embutida e a tela nao le
    o arquivo de configuracao do projeto. Cada operador digita o proprio
    login porque o SEI registra o AUTOR de cada documento criado - se o
    login viesse de um arquivo embutido, os termos sairiam no nome errado.
    Os logs de auditoria (que contem IDs de processo) vao para a pasta do
    usuario no Windows, fora do executavel e fora da pasta do projeto.

Uso:  python interface_app.py
"""

import argparse
import functools
import os
import sys
import threading
import traceback

import customtkinter as ctk
from tkinter import TclError, filedialog, messagebox

# =============================================================================
#  Parametros do SEI - ajuste aqui quando a proxima etapa chegar
# =============================================================================

# Liga/desliga a gravacao. Com SIMULAR = True a tela faz login, troca de
# unidade, le a planilha e resolve os processos, mas PARA ANTES de gravar:
# nenhum documento e criado ou editado no SEI. É o mesmo "plano" que a CLI
# oferece com --plano, exposto aqui como uma constante para nao depender de
# digitacao.
#
# ATENCAO: com SIMULAR = False, CADA LINHA da planilha cria um documento de
# verdade no SEI, no nome do usuario digitado na tela. Confira a planilha e o
# Bloco de Assinatura antes de confirmar. Para ensaiar sem risco, ponha
# True novamente.
SIMULAR = False

# Grava o HTML de cada etapa em ruas_inclusao/ (so diagnostico). False = nao
# deixa arquivos no disco do usuario; ligue True se algo falhar e precisar
# ver a tela que o SEI devolveu.
SALVAR_TELAS = False

# Modos possiveis do botao do bloco. "Incluir e disponibilizar" so funciona
# se o bloco tiver unidades/assinantes configurados no SEI; sem isso o SEI
# recusa. "So incluir" nao exige assinante.
# Paleta unica dos botoes (mesma do tema "blue" do customtkinter), para
# a tela nao misturar cores: AZUL_CLARO e o estado normal, AZUL_ESCURO o
# estado quando o botao esta escolhido, e AZUL_APAGADO o nao escolhido.
AZUL_CLARO = "#3B8ED0"
AZUL_ESCURO = "#1F6AA5"
AZUL_APAGADO = "#2E5F84"
HOVER_CLARO = "#36719F"
HOVER_ESCURO = "#144870"
HOVER_APAGADO = "#25506E"
GRAY_APAGADO = "#6B7785"
TEXTO_PADRAO = "#DCE4EE"  # cor normal de texto no tema escuro

# Terceiro caminho: cria e mescla o documento, mas NAO entra em bloco
# nenhum - util quando o bloco e configurado depois, na mao, no SEI.
MODO_SEM_BLOCO = "sem_bloco"
MODO_INCLUIR = "incluir"
MODO_DISPONIBILIZAR = "disponibilizar"

SERIE = "4110"                      # tipo: Cessao Especial Onus Cedente
PROTOCOLO_MODELO = "148410511"      # protocolo do "Documento Modelo"
UNIDADE = "110031457"               # unidade onde os processos estao
HIPOTESE_LEGAL = "4"                # nivel Restrito
TEXTO_INICIAL = "D"                 # D = usa o Documento Modelo

# Etiquetas da interface
TITULO = "Inclusão Automatizada de Documentos SEI"
LARGURA, ALTURA = 820, 1090

# =============================================================================
#  Onde ficam os logs (LGPD)
# =============================================================================
# O backend abre os logs de auditoria por caminho RELATIVO (log_auditoria.txt,
# log_auditoria_escrita.txt e a pasta ruas_inclusao/). Rodando a automacao com
# a pasta do proprio usuario como diretorio atual, nada disso aparece dentro
# da pasta do executavel - e o .gitignore continua protegendo o projeto.
APP_DIR = "AutomacaoSEI"


def pasta_dados() -> str:
    """%APPDATA%\\AutomacaoSEI - fora do projeto e fora do .exe."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR)


def pasta_logs() -> str:
    """Pasta dos logs desta maquina, criada sob demanda."""
    destino = os.path.join(pasta_dados(), "logs")
    os.makedirs(destino, exist_ok=True)
    return destino

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

    # --- O que o print() e a biblioteca pandas/requests esperam de um arquivo --
    def flush(self):
        if self._linha:
            self._mostrar(self._linha)
            self._linha = ""

    def isatty(self):
        """Diz que isto NAO e um terminal. Sem isso, bibliotecas acham que ha
        cores/ANSI e poluem o console da tela com codigos invisiveis."""
        return False

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return not self.fechado

    def write(self, texto):
        """Recebe o que o print() escreveria. Reconstitui as linhas."""
        if self.fechado:
            return
        try:
            # O Windows pode mandar \r\n; normalizamos para \n antes de quebrar.
            self._linha += (texto or "").replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in self._linha:
                linha, self._linha = self._linha.split("\n", 1)
                self._mostrar(linha)
        except Exception:  # noqa: BLE001 - nunca derrubar o log por causa dele
            pass

    def _mostrar(self, linha):
        # after(0, ...) = agenda para a thread principal. show="center" faz
        # aparecer de primeira.
        self.widget.after(0, self._inserir, linha)

    def _inserir(self, linha):
        try:
            self.widget.configure(state="normal")
            self.widget.insert("end", linha + "\n")
            self.widget.see("end")
            self.widget.configure(state="disabled")
        except TclError:
            # A janela foi fechada enquanto a thread ainda escrevia no console.
            # Fechamos o redirecionador em vez de derrubar a automacao.
            self.fechado = True

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


def montar_args_lote(planilha: str, linha_inicio: int = 0, plano: bool = False,
                     bloco1: str = "", bloco2: str = "",
                     modo: str = MODO_DISPONIBILIZAR):
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
        # Blocos de assinatura: IDs internos lidos da tela. Vazio = nao incluir.
        bloco1=(bloco1 or "").strip(),
        bloco2=(bloco2 or "").strip(),
        # botao do SEI usado na confirmacao (ver _secao_bloco).
        disponibilizar_bloco=(modo == MODO_DISPONIBILIZAR),
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
        # Redimensionavel de proposito: o console inferior e o lugar onde o
        # usuario acompanha o progresso de cada linha da planilha, e ele
        # precisa de espaco quando o lote e grande.
        self.minsize(LARGURA, ALTURA)
        # O tema tem que vir ANTES de qualquer widget: definido depois, o
        # CustomTkinter redesenha a tela e o Entry da senha perde o "show".
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Estado
        self.caminho_planilha = ""
        self.bloco_assinatura = ""
        self.bloco_assinatura2 = ""
        self.modo_bloco = MODO_DISPONIBILIZAR
        self.executando = False
        self._api = None
        self._caminho_logs = None

        self._montar_layout()
        # O console e criado no layout, entao o redirecionador so pode existir
        # depois dele - e antes de qualquer metodo que ja queria escrever.
        self.log = RedirecionadorConsole(self.console)
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.log.log("Interface pronta. Informe o seu usuario do SEI e a planilha.")
        self.log.log("Sua senha e usada so nesta execucao: nada e gravado no PC.")

    # ------------------------------------------------------------------ layout
    def _montar_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Inclusão Automatizada de Documentos SEI",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        titulo.grid(row=0, column=0, padx=24, pady=(24, 0), sticky="w")
        sub = ctk.CTkLabel(self, text="Cessao Especial — Mail Merge via planilha Excel")
        sub.grid(row=1, column=0, padx=24, pady=(0, 18), sticky="w")

        self._secao_credenciais()   # 2
        self._secao_planilha()     # 3
        self._secao_bloco()        # 4
        self._secao_console()      # 5
        self._secao_botao()        # 6 e 7

    def _secao_credenciais(self):
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=2, column=0, padx=24, pady=(0, 12), sticky="ew")
        quadro.grid_columnconfigure(1, weight=1)

        rotulo = ctk.CTkLabel(quadro, text="Usuario do SEI:", font=ctk.CTkFont(weight="bold"))
        rotulo.grid(row=0, column=0, padx=(14, 8), pady=(12, 4), sticky="w")
        self.entrada_usuario = ctk.CTkEntry(quadro, placeholder_text="SEI_USUARIO")
        self.entrada_usuario.grid(row=0, column=1, padx=(0, 14), pady=(12, 4), sticky="ew")
        self.entrada_usuario.bind("<Return>", lambda _e: self.entrada_senha.focus_set())

        rotulo = ctk.CTkLabel(quadro, text="Senha do SEI:", font=ctk.CTkFont(weight="bold"))
        rotulo.grid(row=1, column=0, padx=(14, 8), pady=(0, 4), sticky="w")
        # show="*": a senha nunca aparece na tela nem em print/print_rastreio.
        self.entrada_senha = ctk.CTkEntry(
            quadro, show="*", placeholder_text="Senha do SEI"
        )
        # O customtkinter 6.0.0 perde o "show" durante o proprio __init__ do
        # CTkEntry (o Entry interno ja nasce sem ele). Sem esta reaplicacao a
        # senha apareceria em letras na tela - por isso conferimos de novo.
        self.entrada_senha._entry.configure(show="*")
        self.entrada_senha.grid(row=1, column=1, padx=(0, 14), pady=(0, 12), sticky="ew")

    def _secao_planilha(self):
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=3, column=0, padx=24, pady=(0, 12), sticky="ew")
        quadro.grid_columnconfigure(0, weight=1)

        linha = ctk.CTkFrame(quadro, fg_color="transparent")
        linha.grid(row=0, column=0, padx=14, pady=(12, 6), sticky="ew")
        linha.grid_columnconfigure(0, weight=1)

        self.botao_planilha = ctk.CTkButton(
            linha, text="Selecionar Planilha Excel", width=210, height=34,
            fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
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
            quadro, text="Bloco 1:", font=ctk.CTkFont(weight="bold")
        )
        rotulo.grid(row=0, column=0, padx=(14, 8), pady=(12, 6), sticky="w")
        self.entrada_bloco = ctk.CTkEntry(quadro, placeholder_text="ID do Bloco 1")
        self.entrada_bloco.grid(row=0, column=1, padx=(0, 14), pady=(12, 6), sticky="ew")

        rotulo = ctk.CTkLabel(
            quadro, text="Bloco 2:", font=ctk.CTkFont(weight="bold")
        )
        rotulo.grid(row=1, column=0, padx=(14, 8), pady=(0, 8), sticky="w")
        self.entrada_bloco2 = ctk.CTkEntry(quadro, placeholder_text="ID do Bloco 2")
        self.entrada_bloco2.grid(row=1, column=1, padx=(0, 14), pady=(0, 8), sticky="ew")

        # O valor e o ID interno do bloco (o "value" da opcao na tela do SEI,
        # ex.: 7712033), nao o numero de exibicao. O backend confere contra a
        # lista real do processo e aborta se o numero nao existir - por isso o
        # campo e livre, mas o erro mostra o que existe.
        dica = ctk.CTkLabel(
            quadro,
            text="ID interno do bloco, como aparece na tela do SEI. "
                 "Deixe vazio para nao incluir.",
            anchor="w", font=ctk.CTkFont(size=11),
        )
        dica.grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="w")

        for entrada in (self.entrada_bloco, self.entrada_bloco2):
            entrada.bind("<Return>", lambda _e: self._registrar_bloco())
            entrada.bind("<FocusOut>", lambda _e: self._registrar_bloco())

        # Dois botoes porque sao duas acoes diferentes do SEI: "Incluir" so
        # coloca o documento no bloco; "Incluir e disponibilizar" tambem envia
        # para assinatura e REQUER que o bloco tenha unidades configuradas.
        acoes = ctk.CTkFrame(quadro, fg_color="transparent")
        acoes.grid(row=3, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="ew")
        acoes.grid_columnconfigure((0, 1), weight=1)
        self.botao_incluir = ctk.CTkButton(
            acoes,
            text="Incluir documento",
            command=lambda: self._escolher_modo(MODO_INCLUIR),
            font=ctk.CTkFont(weight="bold"),
        )
        self.botao_incluir.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.botao_disponibilizar = ctk.CTkButton(
            acoes,
            text="Incluir e disponibilizar",
            command=lambda: self._escolher_modo(MODO_DISPONIBILIZAR),
            font=ctk.CTkFont(weight="bold"),
            fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
        )
        self.botao_disponibilizar.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # Terceiro caminho: nenhum bloco. Os campos ficam cinzas e travados,
        # para ninguem acreditar que o numero digitado sera usado.
        self.botao_sem_bloco = ctk.CTkButton(
            quadro,
            text="Nao incluir em bloco de assinatura",
            command=lambda: self._escolher_modo(MODO_SEM_BLOCO),
            font=ctk.CTkFont(),
        )
        self.botao_sem_bloco.grid(
            row=4, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="ew"
        )
        self.modo_bloco = MODO_DISPONIBILIZAR
        self._pintar_modo()

    def _escolher_modo(self, modo):
        """Escolhe o botao do bloco e guarda a opcao para a thread."""
        self.modo_bloco = modo
        self._pintar_modo()
        self.log.log(
            "Modo do bloco: "
            + ("incluir e disponibilizar" if modo == MODO_DISPONIBILIZAR
               else "so incluir (nao precisa de assinante)")
        )

    def _pintar_modo(self):
        """Destaca o botao escolhido, para nao haver duvida na hora de gravar."""
        if self.modo_bloco == MODO_SEM_BLOCO:
            self.botao_sem_bloco.configure(
                fg_color=AZUL_ESCURO, hover_color=HOVER_ESCURO,
                border_width=3, border_color=AZUL_CLARO,
            )
            # Campos cinzas e travados: a tela mostra que o numero que estiver
            # escrito ali nao sera usado.
            for entrada in (self.entrada_bloco, self.entrada_bloco2):
                entrada.configure(state="disabled", text_color=GRAY_APAGADO)
            for botao in (self.botao_incluir, self.botao_disponibilizar,
                       self.botao_sem_bloco):
                botao.configure(fg_color=AZUL_APAGADO, hover_color=HOVER_APAGADO,
                                border_width=0, state="disabled")
            return

        self.botao_sem_bloco.configure(
            fg_color=AZUL_APAGADO, hover_color=HOVER_APAGADO, border_width=0
        )
        for entrada in (self.entrada_bloco, self.entrada_bloco2):
            entrada.configure(state="normal", text_color=TEXTO_PADRAO)
        for botao in (self.botao_incluir, self.botao_disponibilizar,
                       self.botao_sem_bloco):
            botao.configure(state="normal")
        escolhido, outro = (
            (self.botao_disponibilizar, self.botao_incluir)
            if self.modo_bloco == MODO_DISPONIBILIZAR
            else (self.botao_incluir, self.botao_disponibilizar)
        )
        escolhido.configure(
            fg_color=AZUL_ESCURO,
            hover_color=HOVER_ESCURO,
            border_width=3,
            border_color=AZUL_CLARO,
        )
        outro.configure(
            fg_color=AZUL_APAGADO,
            hover_color=HOVER_APAGADO,
            border_width=0,
        )

    def _secao_console(self):
        self.console = ctk.CTkTextbox(self, wrap="word", activate_scrollbars=True)
        self.console.grid(row=5, column=0, padx=24, pady=(0, 12), sticky="nsew")
        self.console.configure(state="disabled")

    def _secao_botao(self):

        # Faixa de modo: deixa obvio, ANTES de clicar, se o programa vai
        # apenas simular ou realmente gravar documentos no SEI. Fica logo
        # acima do botao, no fim da tela, para nao atrapalhar o grid das
        # secoes (que ocupam as linhas 2 a 5).
        texto_modo = (
            "MODO SIMULACAO: le a planilha e confere os processos, sem criar nada no SEI."
            if SIMULAR else
            "MODO GRAVACAO: cada linha da planilha cria um documento no SEI."
        )
        self.etiqueta_modo = ctk.CTkLabel(
            self,
            text=texto_modo,
            corner_radius=8,
            fg_color=("#8a6100", "#5c4200") if SIMULAR else ("#8b1a1a", "#5c1010"),
            text_color="#ffffff",
            font=ctk.CTkFont(weight="bold", size=12),
            height=30,
        )
        self.etiqueta_modo.grid(row=6, column=0, padx=24, pady=(0, 12), sticky="ew")
        self.botao_iniciar = ctk.CTkButton(
            self,
            text="Iniciar Automacao",
            height=52,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
            command=self.iniciar_automacao,
        )
        self.botao_iniciar.grid(row=7, column=0, padx=24, pady=(0, 24), sticky="ew")

    def _registrar_bloco(self):
        """
        Le os dois IDs de bloco de assinatura da tela.

        Devolve (bloco1, bloco2). Se os dois forem iguais, o proprio SEI nao
        aceitaria a segunda inclusao, entao avisamos e devolvemos (vazio) para
        a execucao nao partir com um erro la no fim do lote.
        """
        # No modo "sem bloco" os campos sao IGNORADOS de proposito, mesmo que
        # ainda tenham numero escrito: o botao escolhido manda nao o bloco.
        if self.modo_bloco == MODO_SEM_BLOCO:
            return "", ""
        bloco1 = self.entrada_bloco.get().strip()
        bloco2 = self.entrada_bloco2.get().strip()
        if bloco1 and bloco1 == bloco2:
            self.log.log(f"ERRO: os dois blocos informedos sao iguais ({bloco1}).")
            messagebox.showwarning(
                "Bloco de assinatura",
                f"Os dois blocos informedos sao o mesmo ({bloco1}).\n\n"
                "Informe dois blocos diferentes, ou deixe o segundo vazio.",
                parent=self,
            )
            return "", ""
        self.bloco_assinatura = bloco1
        self.bloco_assinatura2 = bloco2
        return bloco1, bloco2

    # ------------------------------------------------------------- credenciais
    def _obter_credenciais(self):
        """
        Le usuario e senha da tela. Devolve (usuario, senha) ou None se faltar algo.

        REGRA DE OURO (LGPD): a credencial vem SEMPRE do que a pessoa digitou.
        Nao existe fallback para arquivo, variavel de ambiente herdada ou valor
        fixo - se o campo vier vazio, a execucao nao parte. E isso que impede o
        documento de ser criado no nome de outra pessoa.
        """
        usuario = self.entrada_usuario.get().strip()
        senha = self.entrada_senha.get()
        if not usuario or not senha:
            self.log.log("ERRO: preencha o usuario e a senha do SEI.")
            messagebox.showwarning(
                "Credenciais",
                "Informe o USUARIO e a SENHA do SEI para continuar.",
                parent=self,
            )
            (self.entrada_usuario if not usuario else self.entrada_senha).focus_set()
            return None
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

        Tudo que a thread precisa (planilha, usuario, senha, bloco) e lido
        NESTA thread e viaja junto como argumento. Ler um widget Tk de dentro
        da thread de trabalho e inseguro: o Tkinter so pode ser tocado pela
        thread que criou a janela.
        """
        if self.executando:
            self.log.log("Ja existe uma execucao em andamento.")
            return
        if not self.caminho_planilha or not os.path.isfile(self.caminho_planilha):
            self.log.log("Selecione uma planilha Excel valida primeiro.")
            messagebox.showwarning("Planilha", "Selecione uma planilha Excel valida.",
                                   parent=self)
            return

        credencial = self._obter_credenciais()
        if credencial is None:
            return  # o aviso ja foi mostrado dentro de _obter_credenciais
        usuario, senha = credencial

        bloco1, bloco2 = self._registrar_bloco()
        if self.modo_bloco == MODO_SEM_BLOCO:
            acao_bloco = "NAO incluir em bloco de assinatura"
        elif self.modo_bloco == MODO_DISPONIBILIZAR:
            acao_bloco = "incluir e disponibilizar"
        else:
            acao_bloco = "so incluir (sem disponibilizar)"

        # Confirmacao explicita: e o equivalente ao --confirmar da linha de
        # comando. Sem este "ok" nada e gravado no SEI.
        if SIMULAR:
            pergunta = (
                f"MODO SIMULACAO - nada sera criado no SEI.\n\n"
                f"Usuario: {usuario}\n"
                f"Planilha: {os.path.basename(self.caminho_planilha)}\n"
                f"Bloco 1: {bloco1 or '(nao informado)'}  |  Bloco 2: {bloco2 or '(nao informado)'}\n\n"
                "Vai entrar no SEI, ler a planilha e conferir os processos.\n"
                "Quer rodar a simulacao?"
            )
        else:
            pergunta = (
                f"Vai criar documentos no SEI usando o usuario:\n\n    {usuario}\n\n"
                f"Planilha: {os.path.basename(self.caminho_planilha)}\n"
                f"Bloco 1: {bloco1 or '(nao informado)'}  |  "
                f"Bloco 2: {bloco2 or '(nao informado)'}\n"
                f"Acao no bloco: {acao_bloco}\n\n"
                "Cada linha da planilha cria UM documento, no SEU nome. Confirma?"
            )
        if not messagebox.askyesno("Confirmar", pergunta, parent=self):
            self.log.log("Operacao cancelada pelo usuario.")
            return

        self.executando = True
        for widget in (self.botao_iniciar, self.botao_planilha,
                       self.entrada_usuario, self.entrada_senha,
                       self.entrada_bloco, self.entrada_bloco2,
                       self.botao_incluir, self.botao_disponibilizar,
                       self.botao_sem_bloco):
            widget.configure(state="disabled")
        self.botao_iniciar.configure(text="Executando...")

        # daemon=True: se a janela fechar no meio, a thread nao segura o
        # processo do Python. O log avisa que o lote pode ficar incompleto.
        thread = threading.Thread(
            target=self._thread_automacao,
            args=(
                usuario, senha, self.caminho_planilha, bloco1, bloco2,
                self.modo_bloco,
            ),
            daemon=True,
        )
        thread.start()

    def _injetar_credenciais(self, usuario, senha):
        """
        Deixa as credenciais DA TELA onde o backend le: os.environ.

        E chamada em DOIS momentos, e os dois sao necessarios:

        1. ANTES do import do app4_inclusao. O modulo scripts_read/app3 le as
           credenciais no import e, se nao achar nem no ambiente nem no
           arquivo de configuracao, levanta RuntimeError e aborta. Sem esta
           chamada, quem usa o .exe em um PC sem aquele arquivo nem chegaria
           a abrir a tela de login.

        2. DEPOIS do import. O proprio app4_inclusao roda _carregar_env_() no
           import e sobrescreve estas chaves com o arquivo de configuracao da
           maquina de desenvolvimento. Sem esta segunda chamada, o login seria
           o de outra pessoa e o documento sairia assinado por ela.
        """
        os.environ["SEI_USUARIO"] = usuario
        os.environ["SEI_SENHA"] = senha

    def _thread_automacao(
        self, usuario, senha, planilha, bloco1, bloco2, modo_bloco
    ):
        """
        Roda FORA da thread principal. Tudo que precisar da interface volta
        por self.after(...), e a escrita no console passa pelo Redirecionador.
        """
        pasta_anterior = None
        try:
            sys.stdout = self.log
            self.log.log("=" * 66)
            self.log.log(f"Usuario SEI: {usuario}")
            self.log.log(f"Planilha   : {planilha}")
            self.log.log(
                f"Bloco 1: {bloco1 or '(nao informado)'}  |  "
                f"Bloco 2: {bloco2 or '(nao informado)'}"
            )
            if modo_bloco == MODO_SEM_BLOCO:
                self.log.log("Acao no bloco: NAO incluir em bloco de assinatura")
            elif modo_bloco == MODO_DISPONIBILIZAR:
                self.log.log("Acao no bloco: incluir e disponibilizar")
            else:
                self.log.log("Acao no bloco: so incluir")
            self.log.log("=" * 66)

            # LGPD: os logs de auditoria do backend contem IDs de processo.
            # Rodando com a pasta do usuario como diretorio atual, eles nao
            # aparecem nem ao lado do .exe nem na pasta do projeto.
            try:
                pasta_anterior = os.getcwd()
                destino_logs = pasta_logs()
                os.chdir(destino_logs)
                self._caminho_logs = destino_logs
                self.log.log(f"Logs desta execucao em: {destino_logs}")
            except OSError as erro:
                pasta_anterior = None
                self.log.log(f"Aviso: nao foi possivel usar a pasta de logs ({erro}).")

            # 1o) Credenciais ANTES do import: o scripts_read/app3 aborta com
            # RuntimeError se, no import, nao encontrar usuario/senha nem no
            # ambiente nem em arquivo. E o que permite rodar num PC que tem
            # apenas o .exe.
            self._injetar_credenciais(usuario, senha)
            from app4_inclusao import InclusaoDocumentoSEI, executar_lote

            # 2o) Credenciais DEPOIS do import: o app4_inclusao roda
            # _carregar_env_() ao ser importado e sobrescreve o ambiente com
            # o arquivo de configuracao da maquina de desenvolvimento. Esta
            # chamada garante que o login seja o de quem esta na tela.
            self._injetar_credenciais(usuario, senha)

            self.log.log("Conectando ao SEI...")
            # As credenciais vao EXPLICITAS no construtor - e isto que
            # realmente autentica, independentemente de qualquer arquivo.
            api = InclusaoDocumentoSEI(usuario, senha, unidade_alvo=UNIDADE)
            api.salvar_telas = SALVAR_TELAS
            self._api = api
            if not api.autenticar():
                raise RuntimeError("Falha no login. Verifique usuario e senha.")
            api.trocar_unidade()

            args = montar_args_lote(
                planilha, plano=SIMULAR, bloco1=bloco1, bloco2=bloco2,
                modo=modo_bloco,
            )
            executar_lote(api, args, None, TEXTO_INICIAL)
            if SIMULAR:
                # Nenhum documento foi criado nem editado acima; o console
                # mostrou o pre-voo (processos resolvidos, linha a linha).
                self.log.log("=" * 66)
                self.log.log("SIMULACAO encerrada: NADA foi criado ou editado no SEI.")
                self.log.log("Para gravar de verdade, ponha SIMULAR = False no codigo.")
                self.log.log("=" * 66)
            else:
                self.log.log("=" * 66)
                self.log.log(f"GRAVACAO concluida com sucesso como: {usuario}")
                self.log.log("Confira os documentos criados dentro de cada processo no SEI.")
                self.log.log("=" * 66)

        except SystemExit:
            # O backend usa sys.exit() para abortar com mensagem (ex.: processo
            # NAO VISUALIZADO, coluna faltando). Dentro da thread isso nao
            # fecha a interface: registramos e encerramos so a execucao.
            self.log.log("Operacao interrompida pelo sistema (ver mensagem acima).")
        except Exception as erro:  # noqa: BLE001 - erro vira log, nunca traceback cru
            self.log.log(f"ERRO: {erro}")
            self.log.log(traceback.format_exc(limit=3))
        finally:
            if pasta_anterior:
                try:
                    os.chdir(pasta_anterior)
                except OSError:
                    pass
            self.log.restaurar()
            # Se a janela foi fechada no meio, o after() nao tem para onde ir:
            # neste caso nao ha mais interface para reabilitar, e a excecao
            # ficaria solta numa thread que ninguem ve.
            try:
                self.after(0, self._finalizar_execucao)
            except (RuntimeError, TclError):
                pass

    def _finalizar_execucao(self):
        """Volta a thread principal para reabilitar a interface."""
        self.executando = False
        self.botao_iniciar.configure(text="Iniciar Automacao")
        for widget in (self.botao_iniciar, self.botao_planilha,
                       self.entrada_usuario, self.entrada_senha,
                       self.entrada_bloco, self.entrada_bloco2,
                       self.botao_incluir, self.botao_disponibilizar,
                       self.botao_sem_bloco):
            widget.configure(state="normal")
        self.log.log("-" * 66)

    def _ao_fechar(self):
        """Nao deixa a janela morrer no meio de uma gravacao."""
        if self.executando:
            if not messagebox.askyesno(
                "Sair",
                "A automacao ainda esta rodando.\n\n"
                "Sair agora interrompe a criacao dos documentos restantes e o "
                "lote pode ficar incompleto. Quer sair mesmo assim?",
                parent=self,
            ):
                return
        self.destroy()


def main():
    app = AppAutomacaoSEI()
    app.mainloop()


if __name__ == "__main__":
    main()