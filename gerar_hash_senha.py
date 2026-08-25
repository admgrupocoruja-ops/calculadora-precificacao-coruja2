# -*- coding: utf-8 -*-
"""
Gerador de hash de senha — Calculadora de Precificação (Grupo Coruja)
======================================================================
Use este script para cadastrar ou trocar a senha de uma pessoa na
calculadora. Ele NUNCA salva nada sozinho — ele só gera o texto que você
copia e cola manualmente na seção [usuarios] dos Secrets do Streamlit
Community Cloud (share.streamlit.io -> seu app -> Settings -> Secrets).

Como usar:
    python3 gerar_hash_senha.py

O script pergunta a senha (digitação fica oculta) e devolve uma linha
pronta para colar em Secrets, por exemplo:

    senha_hash = "3f9a1c...:8b2e77..."

A senha em si NUNCA fica salva em lugar nenhum — nem neste script, nem em
Secrets. Só o hash (irreversível) fica guardado. Cada vez que você roda o
script, um "salt" novo e aleatório é usado, então rodar duas vezes com a
mesma senha gera dois hashes diferentes — isso é esperado e não é bug.
"""

import getpass
import hashlib
import os


def gerar_hash(senha: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return salt.hex() + ":" + h.hex()


def main():
    print("=" * 70)
    print("Gerador de hash de senha — Calculadora de Precificação")
    print("=" * 70)
    print()
    print("A senha digitada aqui NÃO fica salva em lugar nenhum — só o hash")
    print("(irreversível) gerado a partir dela. Ela não aparece na tela.")
    print()

    senha1 = getpass.getpass("Digite a nova senha: ")
    if not senha1:
        print("\nSenha vazia — operação cancelada.")
        return
    senha2 = getpass.getpass("Digite novamente para confirmar: ")

    if senha1 != senha2:
        print("\nAs duas senhas digitadas são diferentes. Tente de novo.")
        return

    if len(senha1) < 8:
        print(
            "\nAviso: essa senha tem menos de 8 caracteres. Recomenda-se pelo "
            "menos 8, misturando letras, números e símbolos. Gerando o hash "
            "mesmo assim."
        )

    hash_final = gerar_hash(senha1)

    print()
    print("-" * 70)
    print("Copie a linha abaixo e cole em Secrets, dentro do bloco da pessoa")
    print("correspondente em [usuarios] (veja secrets_template.toml):")
    print("-" * 70)
    print()
    print(f'senha_hash = "{hash_final}"')
    print()
    print("-" * 70)


if __name__ == "__main__":
    main()
