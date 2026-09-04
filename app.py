"""
Login automatizado no SEI-MG (GOVMG) via biblioteca `requests`.

Campos do formulário de login identificados na página real:
    txtUsuario  -> usuário
    pwdSenha    -> senha
    selOrgao    -> órgão (lista suspensa - PRECISA do "value" exato da opção)
    hdnAcao     -> campo oculto com ação fixa (PRECISA do "value" exato)

pip install requests
"""

import os

import requests


def _carregar_credenciais():
    """Lê SEI_USUARIO e SEI_SENHA do ambiente ou do arquivo .env.

    O arquivo .env fica ao lado do script e NÃO é versionado no Git
    (ver .gitignore).
    """
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


# =========================
# CONFIGURAÇÃO
# =========================
LOGIN_URL = "https://www.sei.mg.gov.br/sip/login.php?sigla_orgao_sistema=GOVMG&sigla_sistema=SEI"

USUARIO, SENHA = _carregar_credenciais()  # credenciais vêm do .env / ambiente

# Estes dois valores precisam ser copiados do HTML da página de login
# (inspecionar elemento). Veja instruções no final do arquivo.
VALUE_SEL_ORGAO = "52"  # FHEMIG
VALUE_HDN_ACAO = "2"  # valor real enviado pelo JS (acaoLogin(2)) ao clicar em ACESSAR


class SEISessao:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"
        })

    def login(self):
        # 1) Acessa a página de login primeiro para receber os cookies
        #    iniciais (PHPSESSID, ROUTEID etc.) — sem isso o POST seguinte
        #    pode ser rejeitado pelo servidor.
        self.session.get(LOGIN_URL)

        # 1.1) O JavaScript da página grava este cookie com o código do
        #    órgão ANTES de enviar o formulário (visto em
        #    infraCriarCookie('SIP_U_GOVMG_SEI', ...)). Como não rodamos
        #    JS, precisamos gravar esse cookie manualmente.
        self.session.cookies.set("SIP_U_GOVMG_SEI", VALUE_SEL_ORGAO)

        # 2) Envia o formulário de login com os campos reais
        dados_login = {
            "txtUsuario": USUARIO,
            "pwdSenha": SENHA,
            "selOrgao": VALUE_SEL_ORGAO,
            "hdnAcao": VALUE_HDN_ACAO,
        }

        resposta = self.session.post(LOGIN_URL, data=dados_login)

        # 3) Verificação simples de sucesso.
        #    Ajuste "erro" e "sair.php" conforme o que você observar:
        #    - Se o login falhar, o SEI normalmente devolve a própria
        #      página de login de novo, às vezes com uma mensagem de erro.
        #    - Se o login funcionar, geralmente aparece um link/ação de
        #      logout (ex: "sair.php") em algum lugar do HTML.
        if "sair.php" in resposta.text or "Sair" in resposta.text:
            print("Login realizado com sucesso.")
        else:
            print("Login pode ter falhado. Confira o HTML retornado abaixo:")
            print(resposta.text[:1000])  # primeiros 1000 caracteres, para inspecionar

        return resposta


if __name__ == "__main__":
    sei = SEISessao()
    resposta = sei.login()

    # Dica: para depurar, salve a resposta em um arquivo HTML e abra no
    # navegador para ver exatamente o que o servidor devolveu:
    with open("resposta_login.html", "w", encoding="utf-8") as f:
        f.write(resposta.text)