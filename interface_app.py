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

import tkinter

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
SALVAR_TELAS = True

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
TEXTO_PADRAO = "#DCE4EE"     # cor normal de texto no tema escuro
TEXTO_SECUNDARIO = "#9AA7B5"  # dicas e legendas, um tom abaixo do titulo
FUNDO_TELA = "#242424"     # fundo do app e do canvas de rolagem
COR_CARTAO = "#2E2E2E"     # fundo das secoes, um tom acima do app

# Cor de fundo ORIGINAL de um CTkEntry, lida do tema ativo. E o que devolve o
# campo a cor normal depois do modo "sem bloco" ter cinzado ele: passar uma
# cor fixa aqui quebraria assim que o tema ou o modo de aparencia mudasse.
COR_CAMPO = ctk.ThemeManager.theme["CTkEntry"]["fg_color"]

# Terceiro caminho: cria e mescla o documento, mas NAO entra em bloco
# nenhum - util quando o bloco e configurado depois, na mao, no SEI.
MODO_SEM_BLOCO = "sem_bloco"
MODO_INCLUIR = "incluir"
MODO_DISPONIBILIZAR = "disponibilizar"

# A UNIDADE e descoberta no login (o proprio SEI diz em qual unidade o usuario
# esta) e pode ser alterada na tela. Os outros dois NAO tem valor fixo: cada
# unidade tem os seus, entao ficam VAZIOS e sao obrigatorios. Sem modelo o SEI
# criaria um documento em branco - por isso a tela valida antes de gravar.
UNIDADE_PADRAO = ""                 # preenchida pela deteccao apos o login
TIPO_PADRAO = ""                    # id_serie  (ex.: 4110 na unidade atual)
MODELO_PADRAO = ""                  # protocolo do Documento Modelo
HIPOTESE_LEGAL = "4"                # nivel Restrito
TEXTO_INICIAL = "D"                 # D = usa o Documento Modelo

# Etiquetas da interface
TITULO = "Inclusão Automatizada de Documentos SEI"
SUBTITULO = "Cessao Especial - Mail Merge via planilha Excel"
LARGURA, ALTURA = 860, 920

# Grade da janela principal (column=0). As linhas sao faixas: cabecalho fixo,
# corpo rolavel, log e rodape fixo. Os PESOS sao o que faz a tela crescer de
# forma proporcional quando o operador maximiza - sem eles o conteudo fica
# colado no topo e sobra um vao vazio embaixo.
#
# O corpo pesa MAIS que o log de proposito: e a area que precisa de espaco
# (sao 4 cartoes empilhados) enquanto o log, com poucas linhas, sempre tem
# sobra. Invertido, o operador rolaria a tela inteira para ler duas linhas.
PESO_CORPO = 5
PESO_LOG = 2

# Medidas de respiro. Um unico valor para tudo e o que da o ar de "acabado":
# nada de um cartao com 8px e o vizinho com 20.
PAD_CARTAO = 22        # espaco externo entre um cartao e o proximo
PEX_LINHA = 16         # espaco interno do cartao, nas laterais
PEX_CAMPO = 14         # espaco interno do cartao, em cima/baixo
LARG_CAMPO = 230       # largura de um campo de entrada (a das colunas duplas)
ALT_CAMPO = 34         # altura de TODOS os campos de entrada
ALT_BOTAO = 40         # altura dos botoes de acao (bloco e planilha)

# Alturas MINIMAS de corpo e log. O CustomTkinter cria um CTkTextbox com 200px
# e um canvas vazio com ~270px: se deixassemos esses defaults, cada faixa
# ficaria "presa" nesse piso e o PESO da grade nao dividiria o espaco extra -
# era o que deixava o log quase do tamanho do corpo. Pedir menos aqui deixa o
# peso fazer o trabalho: o corpo cresce primeiro, o log recebe a sobra.
ALT_CORPO = 200
ALT_LOG = 90

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
                     modo: str = MODO_DISPONIBILIZAR,
                     unidade: str = "", tipo: str = "", modelo: str = "",
                     mesmo_assim: bool = False):
    """
    Monta o argparse.Namespace que o executar_lote() do app4_inclusao.py espera.

    Nao alteramos o backend: os mesmos nomes de flag da CLI sao reproduzidos
    aqui como atributos. O teste scripts_write/testa_interface_gui.py confere
    que TODOS os atributos lidos pelo backend existem neste Namespace.

    mesmo_assim: equivale ao --mesmo-assim da linha de comando. Vem DESLIGADO
    por padrao de proposito: a regra de ouro n. 1 aborta processo "NAO
    VISUALIZADO" (o vermelho da caixa) porque abrir a URL principal dele
    dispara a contagem de prazo no SEI. Só a tela liga isso, e só quando o
    operador marca a caixa e confirma o aviso.
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
        # Furador da trava de prazo, ligado pelo checkbox da tela.
        mesmo_assim=bool(mesmo_assim),
        editar_documento=None,
        processo=None,
        id_procedimento=None,
        serie=(tipo or "").strip(),
        hipotese_legal=HIPOTESE_LEGAL,
        texto_inicial=TEXTO_INICIAL,
        protocolo_modelo=(modelo or "").strip(),
        id_documento_modelo=None,
        sem_consultar_modelo=False,
        unidade=(unidade or "").strip(),
        # Repassa a constante SALVAR_TELAS. Antes vinha False fixo e isso
        # sobrescrevia o api.salvar_telas = SALVAR_TELAS feito no login:
        # dentro de incluir_documento_interno o self.salvar_telas e reatribuido
        # a partir deste argumento, entao o lote desligava o diagnostico e
        # NENHUM HTML do editor era salvo - sem o 4_editor_montar nao ha como
        # ver por que o merge nao substitui os marcadores no modelo real.
        salvar_telas=SALVAR_TELAS,
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
        # A geometria final (tamanho + posicao centralizada) so pode ser
        # calculada DEPOIS que a janela existir, senao o Tk ainda nao sabe as
        # medidas da tela. _centrar_janela() faz isso no primeiro "_" do laco.
        self._centrar_janela()
        # MEXIVEL: se o monitor for baixo, o operador arrasta e alcanca o
        # botao. Nao ha minsize porque ele IMPOS a altura e anula o geometry.
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
    def _centrar_janela(self):
        """
        Abre a janela no CENTRO da tela, e nunca maior que o monitor.

        Sem isso o .exe abre sempre no canto superior esquerdo.

        CUIDADO COM O DPI. O CustomTkinter sobrescreve geometry() e multiplica
        a largura e a altura pelo fator de escala da tela (125% num monitor
        Full HD), mas NAO multiplica a posicao x/y. Misturar as duas regras da
        em contas erradas: a janela abre maior que a tela ou desalinhada.

        Por isso aqui chamamos o geometry() do TK puro, que nao aplica escala
        nenhuma, e fazemos toda a conta em PIXELS REAIS de tela:

            largura = min(LARGURA x fator, tela - margem)

        Assim o CustomTkinter continua dimensionando os widgets internos pelo
        fator (letras e botoes do tamanho certo) e a janela em si abre
        exatamente do tamanho que pedimos, centralizada, sem passar da tela.
        """
        self.update_idletasks()
        fator = ctk.ScalingTracker.get_window_scaling(self)
        tela_l = self.winfo_screenwidth()   # pixels reais da area de trabalho
        tela_a = self.winfo_screenheight()
        # 60px de folga: sobram as bordas e a barra de tarefas.
        largura = max(760, min(LARGURA * fator, tela_l - 60))
        altura = max(600, min(ALTURA * fator, tela_a - 60))
        x = int((tela_l - largura) / 2)
        y = int((tela_a - altura) / 2)
        # tkinter.Tk.geometry e o metodo ORIGINAL: nao passa pela regra de
        # escala do CustomTkinter, entao x/y batem com a conta acima.
        tkinter.Tk.geometry(self, f"{int(largura)}x{int(altura)}+{x}+{y}")

    def _criar_area_rolavel(self):
        """
        Area rolavel para o conteudo da tela.

        O conteudo completo tem mais de 700px, e nem todo monitor tem isso. Em
        vez de cortar campos ou encolher tudo ate ficar ilegivel, o conteudo
        rola - e a barra aparece SO quando e preciso. O rodape (faixa de modo +
        botao Iniciar) fica FORA da rolagem, sempre visivel: e o unico jeito de
        o operador nao ficar preso sem conseguir apertar Iniciar.
        """
        # container: canvas + scrollbar, com a mesma largura
        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.grid(row=1, column=0, padx=PAD_CARTAO, pady=(0, 6),
                             sticky="nsew")
        # A area rolavel ocupa a faixa do meio e CRESCE com a janela: e o peso
        # da linha que distribui o espaco extra quando o operador maximiza.
        # Sem este weight o canvas ficaria com a altura do conteudo e sobraria
        # um vao vazio entre ele e o log.
        self.grid_rowconfigure(1, weight=PESO_CORPO, minsize=ALT_CORPO)
        self._container.grid_rowconfigure(0, weight=1)
        self._container.grid_columnconfigure(0, weight=1)

        self._canvas = tkinter.Canvas(
            self._container, highlightthickness=0, bd=0,
            background=FUNDO_TELA, height=ALT_CORPO,
        )
        self._scroll = ctk.CTkScrollbar(
            self._container, command=self._canvas.yview
        )
        self._scroll.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._container.grid_columnconfigure(0, weight=1)

        # o "pai" de todas as secoes passa a ser o canvas
        self._conteudo = ctk.CTkFrame(self._canvas, fg_color="transparent")
        # O conteudo e um frame comum: sem peso na coluna 0 ele ficaria com a
        # largura do CACHORRO (o texto mais longo) e os cartoes nao acompanhariam
        # o canvas ao redimensionar - foi o que deixou a tela com uma coluna
        # estreita a esquerda e um vao vazio a direita.
        self._conteudo.grid_columnconfigure(0, weight=1)
        self._janela = self._canvas.create_window(
            (0, 0), window=self._conteudo, anchor="nw"
        )
        self._conteudo.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._janela, width=e.width),
        )
        # roda com a roda do mouse quando o cursor esta sobre o conteudo
        self._canvas.bind_all("<MouseWheel>", self._rolar_com_roda, add="+")
        # Centraliza depois que o layout existir: so entao o Tk conhece as
        # medidas reais da tela e o tamanho minimo do conteudo.
        self.after(0, self._centrar_janela)
        return self._conteudo

    def _rolar_com_roda(self, evento):
        """
        Rola a area de campos com a roda do mouse.

        O log tem a PRÓPRIA barra de rolagem, entao a roda sobre ele precisa
        rolar o log e nao o corpo - senao o operador tentando ler o log acaba
        deslocando a tela de cima, sem entender por que os campos mexeram.
        Por isso o evento so e tratado quando o cursor esta sobre o corpo.
        """
        if not (self._canvas.winfo_exists() and self._canvas.winfo_ismapped()):
            return
        if self._cursor_sobre_o_log(evento):
            return
        self._canvas.yview_scroll(int(-1 * (evento.delta / 120)), "units")

    def _cursor_sobre_o_log(self, evento):
        """True quando o ponteiro do mouse esta em cima do console."""
        try:
            alvo = self.winfo_containing(evento.x_root, evento.y_root)
        except (KeyError, TclError):
            return False
        # Sobe a arvore de pais: o ponteiro pode estar no texto interno do
        # CTkTextbox, e nao no console em si.
        while alvo is not None and alvo is not self:
            if alvo is self.console:
                return True
            alvo = getattr(alvo, "master", None)
        return False

    def _montar_layout(self):
        """
        Monta a tela em quatro faixas verticais (uma linha da grade cada).

        row 0  cabecalho  - titulo e subtitulo, empilhados e centralizados
        row 1  corpo      - campos, dentro da area rolavel (cresce com a janela)
        row 2  log        - console, ocupa toda a largura e cresce ao maximizar
        row 3  faixa      - aviso do modo de gravacao
        row 4  rodape     - botao Iniciar, sempre visivel

        Os PESOS so ficam nas linhas 1 e 2: sao elas que dividem o espaco
        extra quando o operador maximiza. Titulo, faixa e botao tem peso 0
        porque nao devem esticar - so precisam ficar centralizados.
        """
        self.grid_columnconfigure(0, weight=1)
        self._secao_cabecalho()

        # Conteudo numa area rolavel: a barra so aparece quando e preciso. E no
        # rodape, FORA da rolagem, ficam a faixa de modo e o botao Iniciar -
        # e o que garante que o operador sempre consegue apertar Iniciar.
        pai = self._criar_area_rolavel()
        for nome in ("_secao_credenciais", "_secao_unidade", "_secao_planilha",
                     "_secao_bloco"):
            getattr(self, nome)(pai)
        self._secao_console()
        self._secao_botao()

    def _secao_cabecalho(self):
        """
        Titulo e subtitulo.

        Os dois ficam num frame proprio, em LINHAS SEPARADAS e centralizados.
        Antes eles disputavam a mesma celula da grade (um colado a esquerda com
        sticky="w", o outro a direita com sticky="e") e, quando a janela
        ficava estreita, o subtitulo subia por cima do titulo. Empilhados em
        linhas diferentes, um nunca empurra o outro.
        """
        cabecalho = ctk.CTkFrame(self, fg_color="transparent")
        cabecalho.grid(row=0, column=0, padx=PAD_CARTAO, pady=(16, 2), sticky="ew")
        cabecalho.grid_columnconfigure(0, weight=1)

        titulo = ctk.CTkLabel(
            cabecalho,
            text=TITULO,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        titulo.grid(row=0, column=0, sticky="ew")

        sub = ctk.CTkLabel(
            cabecalho,
            text=SUBTITULO,
            font=ctk.CTkFont(size=13),
            text_color=TEXTO_SECUNDARIO,
        )
        sub.grid(row=1, column=0, pady=(2, 0), sticky="ew")

    def _novo_cartao(self, pai, linha, titulo_texto):
        """
        Cria um cartao de secao ja posicionado e devolve o quadro interno.

        Todo bloco da tela nasce daqui, e e o que mantem o respiro e o
        alinhamento iguais em todas as secoes: mesma borda, mesmo espaco entre
        um cartao e o seguinte, mesma margem interna. As duas colunas internas
        tem o mesmo peso E o mesmo "uniform", o que faz os campos de um par
        (Usuario/Senha, Tipo/Modelo, Bloco 1/Bloco 2) ficarem com larguras
        IGUAIS, mesmo que o texto de um deles seja maior que o do outro.
        """
        cartao = ctk.CTkFrame(pai, corner_radius=12, fg_color=COR_CARTAO)
        cartao.grid(row=linha, column=0, padx=PAD_CARTAO, pady=(0, PAD_CARTAO),
                    sticky="ew")
        cartao.grid_columnconfigure((0, 1), weight=1, uniform="par")

        if titulo_texto:
            rotulo = ctk.CTkLabel(
                cartao, text=titulo_texto, font=ctk.CTkFont(weight="bold"),
            )
            rotulo.grid(row=0, column=0, columnspan=2, padx=PEX_LINHA,
                        pady=(PEX_CAMPO, 6), sticky="w")

        quadro = ctk.CTkFrame(cartao, fg_color="transparent")
        quadro.grid(row=1, column=0, columnspan=2, padx=PEX_LINHA,
                    pady=(0, PEX_CAMPO), sticky="ew")
        quadro.grid_columnconfigure((0, 1), weight=1, uniform="par")
        return quadro

    def _rotulo(self, pai, texto):
        """
        Rotulo de campo, com o mesmo peso e a mesma cor em toda a tela.

        Passa o texto como "text=" de proposito: o primeiro argumento do
        CTkLabel e o "master", e escrever ctk.CTkLabel("Unidade:") colocaria
        a string no lugar do pai e a tela nem subiria.
        """
        return ctk.CTkLabel(
            pai, text=texto, font=ctk.CTkFont(weight="bold"), anchor="w",
        )

    def _secao_credenciais(self, pai):
        quadro = self._novo_cartao(pai, 0, "Credenciais do SEI")

        # Usuario e Senha lado a lado: sao a mesma coisa (o par login/senha), o
        # operador preenche um depois do outro, entao ficam na mesma linha e
        # com a mesma largura.
        rotulo = self._rotulo(quadro, "Usuario do SEI:")
        rotulo.grid(row=0, column=0, pady=(0, 4), sticky="w")
        self.entrada_usuario = ctk.CTkEntry(
            quadro, placeholder_text="SEI_USUARIO", height=ALT_CAMPO,
        )
        self.entrada_usuario.grid(row=1, column=0, padx=(0, 6), sticky="ew")
        self.entrada_usuario.bind("<Return>", lambda _e: self.entrada_senha.focus_set())

        rotulo = self._rotulo(quadro, "Senha do SEI:")
        rotulo.grid(row=0, column=1, padx=(6, 0), pady=(0, 4), sticky="w")
        # show="*": a senha nunca aparece na tela nem em print/print_rastreio.
        self.entrada_senha = ctk.CTkEntry(
            quadro, show="*", placeholder_text="Senha do SEI", height=ALT_CAMPO,
        )
        # O customtkinter 6.0.0 perde o "show" durante o proprio __init__ do
        # CTkEntry (o Entry interno ja nasce sem ele). Sem esta reaplicacao a
        # senha apareceria em letras na tela - por isso conferimos de novo.
        self.entrada_senha._entry.configure(show="*")
        self.entrada_senha.grid(row=1, column=1, padx=(6, 0), sticky="ew")

        # Ao sair da senha, detecta a unidade: caminho mais natural para o
        # usuario - digita usuario, senha, e a unidade aparece sozinha.
        self.entrada_senha.bind("<FocusOut>", lambda _e: self._detectar_unidade())

    def _secao_unidade(self, pai):
        """
        Unidade (detectada no login) + Tipo e Modelo do documento.

        Unidade e preenchida sozinha: o proprio SEI diz, no login, em qual
        unidade o usuario esta. Os dois campos de baixo NAO tem padrao porque
        cada unidade tem os seus - e sao obrigatorios, ja que sem Tipo nao ha
        qual serie criar e sem Modelo o SEI abriria um documento em branco.
        """
        quadro = self._novo_cartao(pai, 1, "Unidade e tipo do documento")

        # Unidade ocupa a linha INTEIRA: e o unico campo desta secao cujo texto
        # e longo e imprevisivel ("DSGDAT/SEDI/DSGCONT/..."), entao espremer
        # numa metade da linha so criaria rolagem dentro do campo.
        rot = self._rotulo(quadro, "Unidade:")
        rot.grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="w")
        self.entrada_unidade = ctk.CTkEntry(
            quadro, height=ALT_CAMPO,
        )
        # Ja nasce com o ALVO FIXO (UNIDADE_ALVO), e nao com a unidade em que
        # o login caiu. A deteccao no login e sobrescrita aqui de proposito:
        # o SEI devolve a ultima unidade acessada, que muda de sessao para
        # sessao, e usar esse valor como destino fazia o programa gravar na
        # unidade errada sem nenhum aviso.
        from scripts_read.app3 import UNIDADE_ALVO
        self.entrada_unidade.insert(0, UNIDADE_ALVO)
        self.entrada_unidade.grid(row=1, column=0, columnspan=2,
                                  pady=(0, 12), sticky="ew")

        # Tipo e Modelo sao o par "que documento criar" - mesma linha, mesma
        # largura, preenchidos no mesmo momento.
        rot = self._rotulo(quadro, "Tipo:")
        rot.grid(row=2, column=0, pady=(0, 4), sticky="w")
        self.entrada_tipo = ctk.CTkEntry(
            quadro, placeholder_text="id do tipo (ex.: 4110)", height=ALT_CAMPO,
        )
        self.entrada_tipo.grid(row=3, column=0, padx=(0, 6), sticky="ew")

        rot = self._rotulo(quadro, "Modelo:")
        rot.grid(row=2, column=1, padx=(6, 0), pady=(0, 4), sticky="w")
        self.entrada_modelo = ctk.CTkEntry(
            quadro, placeholder_text="protocolo do Documento Modelo",
            height=ALT_CAMPO,
        )
        self.entrada_modelo.grid(row=3, column=1, padx=(6, 0), sticky="ew")

        dica = ctk.CTkLabel(
            quadro,
            text="Tipo: no SEI, em Incluir Documento, veja o numero do tipo.\n"
                 "Modelo: abra um documento do tipo e copie o protocolo "
                 "(ex.: 0000.00.0000000/0000-00).",
            anchor="w", justify="left", font=ctk.CTkFont(size=11),
            text_color=TEXTO_SECUNDARIO,
        )
        dica.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="w")

        # SEM validacao ao sair do campo (de proposito): ela reclamava de Tipo
        # e Modelo vazios no instante em que o operador saia de um campo para
        # preencher o outro, antes de ele ter chance de digitar. Hoje a
        # conferencia acontece so em iniciar_automacao, via
        # _pedir_unidade_tipo_modelo(), que mostra os tres campos que faltam
        # de uma vez - em vez de tres erros soltos durante o preenchimento.

    def _secao_planilha(self, pai):
        quadro = self._novo_cartao(pai, 2, "Planilha (mail merge)")

        # Botao e nome do arquivo na MESMA linha, com o nome encostado no
        # botao e ocupando todo o resto. Isso exige um quadro proprio: o
        # cartao divide a linha em duas METADES iguais (o "uniform" das
        # seções com pares de campos), o que deixaria o nome flutuando no meio
        # da tela, a uns 200px do botao. Aqui a coluna 0 tem a largura do botao
        # e a coluna 1 fica com o que sobrar.
        linha = ctk.CTkFrame(quadro, fg_color="transparent")
        linha.grid(row=0, column=0, columnspan=2, sticky="ew")
        linha.grid_columnconfigure(1, weight=1)

        self.botao_planilha = ctk.CTkButton(
            linha, text="Selecionar Planilha Excel", width=LARG_CAMPO,
            height=ALT_BOTAO, fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
            command=self.selecionar_planilha,
        )
        self.botao_planilha.grid(row=0, column=0, padx=(0, 12), sticky="w")
        self.rotulo_planilha = ctk.CTkLabel(
            linha, text="Nenhuma planilha selecionada", anchor="w",
            justify="left", text_color=TEXTO_SECUNDARIO,
        )
        self.rotulo_planilha.grid(row=0, column=1, pady=(0, 10), sticky="ew")

        self.rotulo_colunas = ctk.CTkLabel(
            quadro, text="", anchor="w", font=ctk.CTkFont(size=11), justify="left",
            text_color=TEXTO_SECUNDARIO,
        )
        # O wraplength acompanha a largura real do cartao a cada
        # redimensionamento: fixo em 680, o texto cortava numa janela larga e
        # sobrava linha vazia numa estreita.
        self.rotulo_colunas.bind(
            "<Configure>",
            lambda e: self.rotulo_colunas.configure(wraplength=max(280, e.width - 4)),
        )
        self.rotulo_colunas.grid(row=1, column=0, columnspan=2, sticky="w")

        # Furador da trava de NAO VISUALIZADO (o "mesmo_assim" da linha de
        # comando).
        #
        # O SEI pinta de VERMELHO o processo que este usuario ainda NAO abriu,
        # e abrir a URL principal dele INICIA A CONTAGEM DE PRAZO no servidor.
        # A regra de ouro n. 1 aborta esses processos justamente por isso. Este
        # checkbox existe para os casos em que abrir e legitimo e a pessoa
        # assume o risco: por isso fica DESLIGADO por default, mostra o aviso
        # em vermelho embaixo, e a confirmacao final repete a advertencia.
        self.check_abrir_nao_visualizado = ctk.CTkCheckBox(
            quadro,
            text="Abrir processo NAO VISUALIZADO (vermelho)",
            font=ctk.CTkFont(size=12, weight="bold"),
            checkbox_width=20, checkbox_height=20, corner_radius=6,
            fg_color=AZUL_APAGADO, hover_color=HOVER_APAGADO,
            border_color=GRAY_APAGADO, text_color=TEXTO_PADRAO,
            command=self._pintar_aviso_prazo,
        )
        self.check_abrir_nao_visualizado.grid(
            row=2, column=0, columnspan=2, pady=(14, 0), sticky="w",
        )
        self.rotulo_aviso_prazo = ctk.CTkLabel(
            quadro, text="", anchor="w", justify="left",
            font=ctk.CTkFont(size=11), text_color="#e8a0a0",
        )
        self.rotulo_aviso_prazo.grid(
            row=3, column=0, columnspan=2, pady=(4, 0), sticky="w",
        )
        self._pintar_aviso_prazo()

    def _pintar_aviso_prazo(self):
        """
        Mostra ou esconde o alerta de prazo, conforme o checkbox.

        O aviso so aparece com o checkbox LIGADO, e diz exatamente o que o
        programa vai fazer: abrir processo que ninguem leu e disparar a
        contagem de prazo no SEI, sem o responsavel pelo processo saber.
        """
        caixa = getattr(self, "check_abrir_nao_visualizado", None)
        if caixa is None:
            return
        if caixa.get():
            self.rotulo_aviso_prazo.configure(
                text="ATENCAO: abrir um processo NAO VISUALIZADO (vermelho) "
                     "INICIA A CONTAGEM DE PRAZO no SEI e nao da para desfazer. "
                     "No lote, isso vale para TODOS os processos vermelhos da "
                     "planilha."
            )
        else:
            self.rotulo_aviso_prazo.configure(text="")

    def _secao_bloco(self, pai):
        # Row 5: a planilha ocupa a 4. Este bloco usava a MESMA row e o Tkinter
        # empilhava os dois - o bloco cobria o botao de escolher o Excel.
        quadro = self._novo_cartao(pai, 3, "Bloco de assinatura")

        # Bloco 1 e Bloco 2 sao o mesmo par: mesma linha, mesma largura (o
        # "uniform" do cartao divide a linha em duas metades iguais).
        rotulo = self._rotulo(quadro, "Bloco 1:")
        rotulo.grid(row=0, column=0, pady=(0, 4), sticky="w")
        self.entrada_bloco = ctk.CTkEntry(
            quadro, placeholder_text="ID do Bloco 1", height=ALT_CAMPO,
        )
        self.entrada_bloco.grid(row=1, column=0, padx=(0, 6), pady=(0, 10), sticky="ew")

        rotulo = self._rotulo(quadro, "Bloco 2:")
        rotulo.grid(row=0, column=1, padx=(6, 0), pady=(0, 4), sticky="w")
        self.entrada_bloco2 = ctk.CTkEntry(
            quadro, placeholder_text="ID do Bloco 2", height=ALT_CAMPO,
        )
        self.entrada_bloco2.grid(row=1, column=1, padx=(6, 0), pady=(0, 10), sticky="ew")

        # O valor e o ID interno do bloco (o "value" da opcao na tela do SEI,
        # ex.: 7712033), nao o numero de exibicao. O backend confere contra a
        # lista real do processo e aborta se o numero nao existir - por isso o
        # campo e livre, mas o erro mostra o que existe.
        dica = ctk.CTkLabel(
            quadro,
            text="ID interno do bloco, como aparece na tela do SEI. "
                 "Deixe vazio para nao incluir.",
            anchor="w", justify="left", font=ctk.CTkFont(size=11),
            text_color=TEXTO_SECUNDARIO,
        )
        dica.grid(row=2, column=0, columnspan=2, pady=(0, 14), sticky="w")

        for entrada in (self.entrada_bloco, self.entrada_bloco2):
            entrada.bind("<Return>", lambda _e: self._registrar_bloco())
            entrada.bind("<FocusOut>", lambda _e: self._registrar_bloco())

        # Dois botoes porque sao duas acoes diferentes do SEI: "Incluir" so
        # coloca o documento no bloco; "Incluir e disponibilizar" tambem envia
        # para assinatura e REQUER que o bloco tenha unidades configuradas.
        # "uniform" garante a mesma largura: o texto do primeiro e bem mais
        # curto que o do segundo, e sem isso o botao ficava visivelmente menor.
        acoes = ctk.CTkFrame(quadro, fg_color="transparent")
        acoes.grid(row=3, column=0, columnspan=2, pady=(0, 8), sticky="ew")
        acoes.grid_columnconfigure((0, 1), weight=1, uniform="acao")
        self.botao_incluir = ctk.CTkButton(
            acoes,
            text="Incluir documento",
            height=ALT_BOTAO,
            command=lambda: self._escolher_modo(MODO_INCLUIR),
            font=ctk.CTkFont(weight="bold"),
        )
        self.botao_incluir.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.botao_disponibilizar = ctk.CTkButton(
            acoes,
            text="Incluir e disponibilizar",
            height=ALT_BOTAO,
            command=lambda: self._escolher_modo(MODO_DISPONIBILIZAR),
            font=ctk.CTkFont(weight="bold"),
            fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
        )
        self.botao_disponibilizar.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # Terceiro caminho: nenhum bloco. Os campos ficam cinzas e travados,
        # para ninguem acreditar que o numero digitado sera usado.
        self.botao_sem_bloco = ctk.CTkButton(
            quadro,
            text="Nao incluir em bloco de assinatura",
            height=ALT_BOTAO,
            command=lambda: self._escolher_modo(MODO_SEM_BLOCO),
            font=ctk.CTkFont(),
        )
        self.botao_sem_bloco.grid(row=4, column=0, columnspan=2, sticky="ew")
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
            # Os CAMPOS nao sao botoes e nao aceitam "hover_color": passar o
            # mesmo argumento para os dois tipos levanta ValueError e a tela
            # trava no clique. Cada grupo recebe so o que o seu widget aceita -
            # o efeito visual (cinza e travado) e o mesmo.
            for botao in (self.botao_incluir, self.botao_disponibilizar,
                          self.botao_sem_bloco):
                botao.configure(fg_color=AZUL_APAGADO, hover_color=HOVER_APAGADO,
                                border_width=0, state="disabled")
            for entrada in (self.entrada_unidade, self.entrada_tipo,
                            self.entrada_modelo):
                entrada.configure(fg_color=AZUL_APAGADO, state="disabled")
            return

        self.botao_sem_bloco.configure(
            fg_color=AZUL_APAGADO, hover_color=HOVER_APAGADO, border_width=0
        )
        for entrada in (self.entrada_bloco, self.entrada_bloco2):
            entrada.configure(state="normal", text_color=TEXTO_PADRAO)
        for botao in (self.botao_incluir, self.botao_disponibilizar,
                      self.botao_sem_bloco):
            botao.configure(state="normal")
        for entrada in (self.entrada_unidade, self.entrada_tipo,
                        self.entrada_modelo):
            entrada.configure(state="normal", fg_color=COR_CAMPO)
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
        """
        Log da execucao, na FAIXA 2 da janela (fora da area rolavel).

        Fica entre os campos e o rodape porque e o que o operador olha o tempo
        todo: sempre visivel, sem precisar rolar. Ocupa a largura inteira e
        CRESCE junto com a janela - o peso da linha 2 faz a log take o espaco
        extra quando o operador maximiza, em vez de deixar um vao vazio. O
        "minsize" garante uma altura util mesmo na menor janela possivel.
        """
        self.grid_rowconfigure(2, weight=PESO_LOG, minsize=ALT_LOG)
        self.console = ctk.CTkTextbox(
            self, wrap="word", activate_scrollbars=True,
            fg_color=COR_CARTAO, corner_radius=12, height=ALT_LOG,
        )
        self.console.grid(
            row=2, column=0, padx=PAD_CARTAO, pady=(0, PAD_CARTAO), sticky="nsew",
        )
        self.console.configure(state="disabled")

    def _secao_botao(self):
        """
        Rodape FIXO: faixa de aviso do modo e o botao Iniciar.

        Ocupa as faixas 3 e 4 da janela, fora da area rolavel: e o que garante
        que o operador sempre alcanca o botao Iniciar, mesmo com o conteudo
        rolado. Ambas as linhas tem peso 0 - elas nao esticam quando a janela
        cresce, porque um aviso e um botao nao devem virar faixas de meio
        palmo de altura.
        """
        # Faixa de modo: deixa obvio, ANTES de clicar, se o programa vai
        # apenas simular ou realmente gravar documentos no SEI. Fica logo
        # acima do botao, no fim da tela, para nao atrapalhar o grid das
        # secoes (que ocupam a area rolavel da faixa 1).
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
            height=32,
        )
        # Mesma largura e mesma margem do log e do corpo: as tres faixas ficam
        # alinhadas pelas bordas, e a tela para de parecer "colada num canto".
        self.etiqueta_modo.grid(
            row=3, column=0, padx=PAD_CARTAO, pady=(0, 10), sticky="ew",
        )
        self.botao_iniciar = ctk.CTkButton(
            self,
            text="Iniciar Automacao",
            height=54,
            corner_radius=10,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color=AZUL_CLARO, hover_color=HOVER_CLARO,
            command=self.iniciar_automacao,
        )
        self.botao_iniciar.grid(
            row=4, column=0, padx=PAD_CARTAO, pady=(0, 16), sticky="ew",
        )

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

    # ------------------------------------------------- unidade, tipo e modelo
    def _detectar_unidade(self):
        """
        Loga no SEI so para descobrir em que unidade o usuario esta.

        O proprio SEI devolve essa informacao no login (o app3 ja le em
        _detectar_unidade_atual). A sessao fica guardada e e REAPROVEITADA
        quando o usuario clicar em Iniciar - nao ha segundo login.

        Silenciosa: se falhar, nao atrapalha. O campo Unidade continua editavel
        e o Iniciar valida de novo, com mensagem clara.
        """
        usuario = self.entrada_usuario.get().strip()
        senha = self.entrada_senha.get()
        if not usuario or not senha:
            return
        if self.entrada_unidade.get().strip():
            # Ja preenchida (padrao UNIDADE_ALVO ou o que o usuario digitou).
            # Ainda assim registramos onde o login caiu, para o log deixar
            # claro se vai haver troca de unidade ou nao.
            self._comparar_unidade_login()
            return
        try:
            self._injetar_credenciais(usuario, senha)
            from app4_inclusao import InclusaoDocumentoSEI
            api = InclusaoDocumentoSEI(usuario, senha, unidade_alvo=None)
            if not api.autenticar():
                return
            detectada = (api.unidade_atual or "").strip()
            if detectada:
                self._api = api
                self._comparar_unidade_login(detectada)
        except Exception:  # noqa: BLE001 - deteccao e conveniência, nao essencial
            return

    def _comparar_unidade_login(self, detectada=None):
        """
        Registra onde o login caiu e se isso exige troca de unidade.

        Este metodo NAO escreve no campo Unidade. O destino e o alvo fixo
        (UNIDADE_ALVO); a unidade do login e so informacao de contexto. Confundir
        as duas ja fez o programa mirar na unidade errada: o SEI devolve a
        ultima unidade acessada, que varia entre sessoes.
        """
        alvo = self.entrada_unidade.get().strip()
        if detectada is None:
            detectada = getattr(self._api, "unidade_atual", "") or "(desconhecida)"
        if detectada == alvo:
            self.log.log(f"Login caiu na unidade alvo ({alvo}): sem troca.")
        else:
            self.log.log(
                f"Login caiu em {detectada}, mas o alvo e {alvo}: "
                "a automacao vai trocar de unidade antes de gravar."
            )

    def _validar_documento(self):
        """
        Unidade e obrigatoria; Tipo e Modelo tambem, e VAZIOS de proposito.

        Sem Tipo nao ha qual serie criar. Sem Modelo o SEI abriria um documento
        em branco (texto_inicial "N" em vez de "D"), que parece sucesso e nao
        e - por isso a tela barra antes de qualquer gravacao.
        """
        if not self.entrada_unidade.get().strip():
            self.log.log("ERRO: informe a unidade (o id interno do SEI).")
            return False
        if not self.entrada_tipo.get().strip():
            self.log.log("ERRO: o campo Tipo e obrigatorio.")
            return False
        if not self.entrada_modelo.get().strip():
            self.log.log("ERRO: o campo Modelo e obrigatorio.")
            return False
        return True

    def _pedir_unidade_tipo_modelo(self):
        """Mensagem unica para os tres campos que faltam. True se estiver tudo certo."""
        faltando = []
        if not self.entrada_unidade.get().strip():
            faltando.append("UNIDADE (o id interno, no numero da URL do SEI)")
        if not self.entrada_tipo.get().strip():
            faltando.append("TIPO (em Incluir Documento, veja o numero do tipo)")
        if not self.entrada_modelo.get().strip():
            faltando.append(
                "MODELO (protocolo do Documento Modelo, no documento ja criado)"
            )
        if not faltando:
            return True
        self.log.log("ERRO: faltam campos: " + ", ".join(faltando))
        messagebox.showwarning(
            "Faltam dados do documento",
            "Preencha antes de iniciar:\n\n  -  " + "\n  -  ".join(faltando)
            + "\n\nCada unidade tem os seus valores; o de Tipo e Modelo vem de "
              "la. Sem Modelo o SEI criaria um documento em branco.",
            parent=self,
        )
        (self.entrada_unidade if not self.entrada_unidade.get().strip()
         else self.entrada_tipo if not self.entrada_tipo.get().strip()
         else self.entrada_modelo).focus_set()
        return False

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

        if not self._pedir_unidade_tipo_modelo():
            return
        unidade = self.entrada_unidade.get().strip()
        tipo = self.entrada_tipo.get().strip()
        modelo = self.entrada_modelo.get().strip()

        bloco1, bloco2 = self._registrar_bloco()
        # Furador da trava de NAO VISUALIZADO: so vem True se o operador
        # marcou a caixa E confirmou o aviso na janela de confirmacao.
        # O .get() e lido AQUI, no clique, e nao guardado em variavel: a tela
        # nao depende de nenhum "_pintar_aviso_prazo" ter rodado antes. Assim
        # o valor e sempre o que esta marcado na hora, e nao o que sobrou de
        # um estado anterior.
        abrir_nao_visualizado = bool(self.check_abrir_nao_visualizado.get())
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
                f"Unidade: {unidade}\n"
                f"Tipo: {tipo}   |   Modelo: {modelo}\n"
                f"Bloco 1: {bloco1 or '(nao informado)'}  |  "
                f"Bloco 2: {bloco2 or '(nao informado)'}\n"
                f"Acao no bloco: {acao_bloco}\n\n"
                "Cada linha da planilha cria UM documento, no SEU nome."
                + (
                    "\n\n*** ATENCAO ***\nVoce marcou abrir processo NAO VISUALIZADO.\n"
                    "Abrir um processo vermelho (nao lido) INICIA A CONTAGEM DE\n"
                    "PRAZO no SEI e nao da para desfazer. Vale para TODOS os\n"
                    "vermelhos da planilha, e o responsavel nao e avisado.\n"
                    if abrir_nao_visualizado else ""
                )
                + "\n\nConfirma?"
            )
        # Sincroniza o aviso com a caixa antes de perguntar: se o comando do
        # checkbox nao tiver rodado, o texto do aviso ainda seria o antigo.
        self._pintar_aviso_prazo()
        if not messagebox.askyesno("Confirmar", pergunta, parent=self):
            self.log.log("Operacao cancelada pelo usuario.")
            return

        self.executando = True
        for widget in (self.botao_iniciar, self.botao_planilha,
                       self.entrada_usuario, self.entrada_senha,
                       self.entrada_bloco, self.entrada_bloco2,
                       self.botao_incluir, self.botao_disponibilizar,
                       self.botao_sem_bloco, self.entrada_unidade,
                       self.entrada_tipo, self.entrada_modelo,
                       self.check_abrir_nao_visualizado):
            widget.configure(state="disabled")
        self.botao_iniciar.configure(text="Executando...")

        # daemon=True: se a janela fechar no meio, a thread nao segura o
        # processo do Python. O log avisa que o lote pode ficar incompleto.
        thread = threading.Thread(
            target=self._thread_automacao,
            args=(
                usuario, senha, self.caminho_planilha, bloco1, bloco2,
                self.modo_bloco, unidade, tipo, modelo,
                abrir_nao_visualizado,
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
        self, usuario, senha, planilha, bloco1, bloco2, modo_bloco,
        unidade, tipo, modelo, abrir_nao_visualizado=False,
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
            if abrir_nao_visualizado:
                self.log.log(
                    "ATENCAO: abrindo processo NAO VISUALIZADO (vermelho) - "
                    "isso INICIA A CONTAGEM DE PRAZO no SEI."
                )
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
            api = InclusaoDocumentoSEI(usuario, senha, unidade_alvo=unidade)
            api.salvar_telas = SALVAR_TELAS
            self._api = api
            if not api.autenticar():
                raise RuntimeError("Falha no login. Verifique usuario e senha.")
            # Rastreabilidade antes da troca: o login sempre cai na unidade
            # padrao do operador (110031718), e e para a unidade ALVO que o
            # lote precisa ir. Sem esta linha, uma troca falhada deixaria o
            # programa operando na unidade errada sem ninguem perceber.
            self.log.log(f"Unidade alvo: {unidade or '(vazia)'} | "
                         f"unidade apos login: {getattr(api, 'unidade_atual', '') or '(desconhecida)'}")
            if not unidade.strip():
                raise RuntimeError(
                    "Unidade vazia: a automacao nao pode seguir sem saber em qual "
                    "unidade gravar. Preencha o campo Unidade."
                )
            ok = api.trocar_unidade()
            if not ok:
                raise RuntimeError(
                    f"O SEI recusou a troca para a unidade {unidade}. "
                    "Verifique se este usuario tem permissao nela."
                )
            self.log.log(f"Unidade apos troca: {getattr(api, 'unidade_atual', '') or '(desconhecida)'}")

            args = montar_args_lote(
                planilha, plano=SIMULAR, bloco1=bloco1, bloco2=bloco2,
                modo=modo_bloco, unidade=unidade, tipo=tipo, modelo=modelo,
                mesmo_assim=abrir_nao_visualizado,
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

        except SystemExit as erro:
            # O backend usa sys.exit("texto") para abortar com motivo (ex.:
            # coluna faltando na planilha, processo NAO VISUALIZADO). O texto
            # NAO vai para o stdout: o sys.exit() guarda a mensagem em
            # erro.code e sai em silencio. Antes este handler ignorava esse
            # campo e escrevia "ver mensagem acima", dejando o operador sem
            # nenhuma pista do motivo real. Sem esta linha, uma coluna
            # faltando na planilha aparecia como "operacao interrompida" e
            # nada mais.
            motivo = getattr(erro, "code", None)
            if motivo is None:
                self.log.log("Operacao interrompida sem mensagem de motivo.")
            elif str(motivo).strip() in ("0", ""):
                self.log.log("Operacao interrompida (saida com codigo 0).")
            else:
                self.log.log(f"Operacao interrompida: {motivo}")
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
                       self.botao_sem_bloco, self.entrada_unidade,
                       self.entrada_tipo, self.entrada_modelo):
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