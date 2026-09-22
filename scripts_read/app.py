import os

import requests


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


class SEISessao:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"
        })

    def login(self):
        self.session.get(LOGIN_URL)

        self.session.cookies.set("SIP_U_GOVMG_SEI", VALUE_SEL_ORGAO)

        dados_login = {
            "txtUsuario": USUARIO,
            "pwdSenha": SENHA,
            "selOrgao": VALUE_SEL_ORGAO,
            "hdnAcao": VALUE_HDN_ACAO,
        }

        resposta = self.session.post(LOGIN_URL, data=dados_login)

        if "sair.php" in resposta.text or "Sair" in resposta.text:
            print("Login realizado com sucesso.")
        else:
            print("Login pode ter falhado. Confira o HTML retornado abaixo:")
            print(resposta.text[:1000])

        return resposta


if __name__ == "__main__":
    sei = SEISessao()
    resposta = sei.login()

    with open("resposta_login.html", "w", encoding="utf-8") as f:
        f.write(resposta.text)