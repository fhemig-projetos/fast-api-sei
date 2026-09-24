import os
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup


def _carregar_credenciais():
    caminho_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(caminho_env):
        with open(caminho_env, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, _, valor = linha.partition("=")
                os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))

    usuario = os.environ.get("SEI_USUARIO")
    senha = os.environ.get("SEI_SENHA")
    if not usuario or not senha:
        raise RuntimeError(
            "Credenciais do SEI não encontradas. Crie o arquivo .env ao lado do "
            "script com:\n  SEI_USUARIO=<seu login>\n  SEI_SENHA=<sua senha>"
        )
    return usuario, senha

LOGIN_URL = "https://www.sei.mg.gov.br/sip/login.php?sigla_orgao_sistema=GOVMG&sigla_sistema=SEI"

USUARIO, SENHA = _carregar_credenciais()
VALUE_SEL_ORGAO = "52"  
VALUE_HDN_ACAO = "2"    

class APIExtratorSEI:
    def __init__(self, usuario, senha):
        self.usuario = usuario
        self.senha = senha
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.html_home = ""

    def autenticar(self):
        print("Trancando credenciais e conectando ao SEI...")
        self.session.get(LOGIN_URL)
        self.session.cookies.set("SIP_U_GOVMG_SEI", VALUE_SEL_ORGAO)

        dados_login = {
            "txtUsuario": self.usuario, "pwdSenha": self.senha,
            "selOrgao": VALUE_SEL_ORGAO, "hdnAcao": VALUE_HDN_ACAO,
        }

        resposta = self.session.post(LOGIN_URL, data=dados_login)
        if "sair.php" in resposta.text or "Sair" in resposta.text:
            print("✅ Login realizado com sucesso!")
            self.html_home = resposta.text
            return True
            
        print("❌ Login falhou. Verifique as credenciais.")
        return False

    def trocar_unidade(self):
        print("🔄 Solicitando troca para a unidade alvo (110007141)...")

        # AVISO DIDATICO (nao copie para producao): este script e a versao ANTIGA
        # do projeto e usa um infra_hash FIXO no lugar de extrai-lo da tela. E
        # exatamente o que a regra de ouro proibe - o hash pertence a uma sessao
        # especifica e morre. Serve apenas para estudar o fluxo. O app3.py (leitura)
        # e o app4_inclusao.py (escrita) extraem o hash da tela.
        url_troca = (
            "https://www.sei.mg.gov.br/sei/controlador.php?"
            "acao=infra_trocar_unidade&"
            "acao_origem=infra_trocar_unidade&"
            "infra_sistema=100000100&"
            "infra_unidade_atual=110007141&"
            "infra_hash=HASH_DIDATICO_NAO_FUNCIONA_FORA_DESTA_SESSAO"
        )
        
        payload = {
            "hdnInfraTipoPagina": "1",
            "txtInfraSiglaUnidade": "",
            "txtInfraDescricaoUnidade": "",
            "selInfraOrgaoUnidade": "",
            "chkInfraItem": "110007141",
            "hdnInfraNroItens": "3",
            "hdnInfraItemId": "",
            "hdnInfraItens": "110006339,110007141,110031457"
        }
        
        try:
            self.session.post(url_troca, data=payload, allow_redirects=False)
        except UnicodeDecodeError:
            pass
            
        print("✅ Comando de troca enviado ao servidor.")
        return True

    def navegar_para_processos(self):
        print("🔍 Acessando a raiz do sistema para forçar a geração de um novo infra_hash...")
        
        res_inicial = self.session.get("https://www.sei.mg.gov.br/sei/inicializar.php")
        
        if 'tblProcessos' in res_inicial.text or 'infraTable' in res_inicial.text:
            return res_inicial.text
            
        sopa = BeautifulSoup(res_inicial.text, 'html.parser')
        link_controle = sopa.find('a', href=re.compile(r'acao=procedimento_controlar'))
        
        if link_controle:
            url_dinamica = link_controle['href']
            if not url_dinamica.startswith('http'):
                url_dinamica = "https://www.sei.mg.gov.br/sei/" + url_dinamica
                
            print("🔗 Novo hash capturado com sucesso! Abrindo a tabela...")
            resposta_final = self.session.get(url_dinamica)
            return resposta_final.text
            
        print("⚠️ Link do Controle de Processos não encontrado na tela inicial.")
        return res_inicial.text

    def raspar_pagina_atual(self, html):
        sopa = BeautifulSoup(html, 'html.parser')
        tabela = sopa.find('table', {'id': 'tblProcessos'}) or sopa.find('table', class_='infraTable')
        
        dados = []
        tem_proxima = False
        action_url = ""
        form_data = {}

        if not tabela:
            return dados, tem_proxima, action_url, form_data

        headers = [th.text.strip() for th in tabela.find_all('th')]
        idx_proc = next((i for i, h in enumerate(headers) if 'Processo' in h), -1)
        idx_atrib = next((i for i, h in enumerate(headers) if 'Atribuição' in h), -1)
        idx_tipo = next((i for i, h in enumerate(headers) if 'Tipo' in h), -1)
        idx_inter = next((i for i, h in enumerate(headers) if 'Interessados' in h), -1)

        linhas = tabela.find_all('tr')[1:]
        for linha in linhas:
            colunas = linha.find_all('td')
            if len(colunas) < 4: continue
            
            try:
                proc = colunas[idx_proc].text.strip() if idx_proc != -1 else ""
                atrib = colunas[idx_atrib].text.strip() if idx_atrib != -1 else ""
                tipo = colunas[idx_tipo].text.strip() if idx_tipo != -1 else ""
                inter = colunas[idx_inter].text.strip() if idx_inter != -1 else ""
                
                proc = re.sub(r'\s+', ' ', proc)
                atrib = re.sub(r'\s+', ' ', atrib)
                tipo = re.sub(r'\s+', ' ', tipo)
                inter = re.sub(r'\s+', ' ', inter)

                if proc:
                    dados.append({
                        "Processo": proc,
                        "Atribuição": atrib,
                        "Tipo": tipo,
                        "Interessados": inter
                    })
            except IndexError:
                continue

        form = sopa.find('form', id='frmProcedimentoControlar')
        if form:
            action_url = form.get('action', '')
            if not action_url.startswith('http'):
                action_url = "https://www.sei.mg.gov.br/sei/" + action_url
                
            inputs = form.find_all('input', type='hidden')
            for inp in inputs:
                nome_campo = inp.get('name')
                if nome_campo: 
                    form_data[nome_campo] = inp.get('value', '')
                    
            btn_proxima = sopa.find('img', alt=re.compile(r'Próxima Página', re.IGNORECASE))
            if btn_proxima:
                tem_proxima = True
                pagina_atual = int(form_data.get('hdnDetalhadoPaginaAtual', 0))
                form_data['hdnDetalhadoPaginaAtual'] = str(pagina_atual + 1)
                
        return dados, tem_proxima, action_url, form_data

    def extrair_todas_as_paginas(self, limite_paginas=5):
        html = self.navegar_para_processos()
        if not html: return []
        
        todos_dados = []
        pagina_atual = 1
        
        while html and pagina_atual <= limite_paginas:
            print(f"📄 Varrendo a página {pagina_atual} (Limite: {limite_paginas})...")
            dados_pagina, tem_proxima, url_post, form_data = self.raspar_pagina_atual(html)
            
            if not dados_pagina:
                print("⚠️ A tabela veio vazia. O infra_hash do Controle de Processos pode ter expirado ou a fila está vazia.")
                break
                
            todos_dados.extend(dados_pagina)
            print(f"   ↳ {len(dados_pagina)} processos registrados visualmente nesta página.")
            
            if tem_proxima and url_post and pagina_atual < limite_paginas:
                pagina_atual += 1
                resposta = self.session.post(url_post, data=form_data)
                html = resposta.text
            elif pagina_atual >= limite_paginas:
                print(f"🛑 Limite de {limite_paginas} páginas atingido. Parando a raspagem.")
                break
            else:
                print("✅ Fim da fila alcançado antes do limite.")
                break
                
        return todos_dados

def gerar_planilha_filtrada(dados_extraidos):
    if not dados_extraidos: 
        print("⚠️ Operação cancelada por falta de dados.")
        return
        
    df = pd.DataFrame(dados_extraidos)
    total_geral = len(df)
    
    print(f"\n📊 Analisando um total de {total_geral} processos na amostragem...")
    
    df_1080 = df[df['Processo'].str.startswith('1080', na=False)].copy()
    
    if df_1080.empty:
        print("⚖️ Nenhuma demanda judicial (1080) encontrada nestas primeiras páginas.")
        return
        
    print(f"⚖️ Filtro aplicado: {len(df_1080)} processos judiciais (1080) encontrados na amostra.")
    
    nome_arquivo = "amostra_judiciais_1080_nova_unidade.xlsx"
    try:
        df_1080.to_excel(nome_arquivo, index=False)
        print(f"📁 Planilha de amostragem '{nome_arquivo}' gerada com sucesso!")
    except PermissionError:
        print(f"❌ ERRO: O arquivo '{nome_arquivo}' está aberto no Excel. Feche-o e rode o script novamente.")

if __name__ == "__main__":
    api_sei = APIExtratorSEI(USUARIO, SENHA)
    
    if api_sei.autenticar():
        if api_sei.trocar_unidade():
            dados_macro = api_sei.extrair_todas_as_paginas(limite_paginas=5)
            gerar_planilha_filtrada(dados_macro)