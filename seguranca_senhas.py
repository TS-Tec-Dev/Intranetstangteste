"""Compatibilidade com senhas legadas, gravando novas senhas em PBKDF2-SHA256."""
import hashlib
import hmac
import os

PREFIXO='pbkdf2_sha256$'


def gerar_hash(senha):
    if not senha:raise ValueError('Senha não pode ser vazia')
    salt=os.urandom(16)
    iteracoes=380_000
    chave=hashlib.pbkdf2_hmac('sha256',senha.encode('utf-8'),salt,iteracoes)
    return f'{PREFIXO}{iteracoes}${salt.hex()}${chave.hex()}'


def verificar_senha(senha, armazenada):
    val=str(armazenada)
    if val.startswith(PREFIXO):
        try:
            _,iteracoes,salt,hash_esperado=val.split('$',3)
            chave=hashlib.pbkdf2_hmac('sha256',senha.encode('utf-8'),bytes.fromhex(salt),int(iteracoes))
            return hmac.compare_digest(chave,bytes.fromhex(hash_esperado))
        except (ValueError,TypeError):return False
    # Somente para migração de usuários antigos cujas senhas estavam em CSV puro.
    return hmac.compare_digest(str(senha),val)
