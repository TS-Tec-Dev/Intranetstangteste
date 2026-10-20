"""Complemento seguro de perfis da Intranet Stang 2.0.2.

Nunca substitui Senha, Admin, Permissoes, Validade ou Filiais.
Importa somente e-mails/servidores SMTP e uma assinatura de usuário existente.
Usa uma importação criptografada separada do backup completo de contas.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pandas as pd
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import insert, select, update

import infra_persistencia as repo
from backup_contas import _validar_admin_atual

HEADER = b'STANG-PERFIL-ENC-V1\n'
LIMIT = 3_000_000
PROFILE_FIELDS = {'Usuario', 'Email_Usuario', 'Servidor_SMTP', 'Porta_SMTP'}
ALLOWED_USERS = {'kkethilyn', 'bruno jerri', 'milton'}


def _key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < 14:
        raise ValueError('Informe a senha do complemento (mínimo 14 caracteres).')
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode('utf-8'))


def encrypt_profile_data(data: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return HEADER + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, HEADER)


def _open(archive: bytes, password: str):
    if len(archive) > LIMIT or not archive.startswith(HEADER) or len(archive) < len(HEADER)+16+12+16:
        raise ValueError('Arquivo inválido; selecione o complemento .stangperfil.')
    i = len(HEADER)
    try:
        raw = AESGCM(_key(password, archive[i:i+16])).decrypt(
            archive[i+16:i+28], archive[i+28:], HEADER)
    except InvalidTag as exc:
        raise ValueError('Senha do complemento incorreta ou arquivo danificado.') from exc
    if len(raw) > LIMIT:
        raise ValueError('Complemento excede o limite de tamanho.')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) != 2 or {x.filename for x in infos} != {'manifest.json','assinatura_milton.png'}:
            raise ValueError('Complemento incompleto ou contém dados inesperados.')
        if sum(x.file_size for x in infos) > LIMIT or z.testzip() is not None:
            raise ValueError('Complemento danificado.')
        cfg = json.loads(z.read('manifest.json'))
        sig = z.read('assinatura_milton.png')
    if cfg.get('app') != 'IntranetStang' or cfg.get('tipo') != 'complemento_perfis' or cfg.get('versao') != 1:
        raise ValueError('Formato de complemento incompatível.')
    perfis = cfg.get('perfis')
    if not isinstance(perfis,list) or len(perfis) != 2:
        raise ValueError('A lista de perfis não corresponde aos dois e-mails esperados.')
    nomes = set()
    for p in perfis:
        if not isinstance(p,dict) or set(p) != PROFILE_FIELDS:
            raise ValueError('Campos de perfil inesperados.')
        login = p['Usuario'].strip().casefold()
        if login not in {'kkethilyn', 'bruno jerri'} or login in nomes:
            raise ValueError('Logins duplicados ou diferentes dos esperados.')
        nomes.add(login)
        if not isinstance(p['Email_Usuario'],str) or '@' not in p['Email_Usuario'] or len(p['Email_Usuario']) > 254:
            raise ValueError('E-mail inválido no complemento.')
        if p['Servidor_SMTP'] != 'smtp.gmail.com' or str(p['Porta_SMTP']) != '587':
            raise ValueError('Servidor SMTP inesperado no complemento.')
    if nomes != {'kkethilyn', 'bruno jerri'}:
        raise ValueError('Falta uma conta de e-mail esperada.')
    if not sig.startswith(b'\x89PNG\r\n\x1a\n') or len(sig) > 2_000_000:
        raise ValueError('Assinatura PNG inválida ou excessivamente grande.')
    from PIL import Image
    with Image.open(io.BytesIO(sig)) as im:
        if im.format != 'PNG' or im.width > 8000 or im.height > 8000:
            raise ValueError('Dimensões ou formato da assinatura inválidos.')
        im.verify()
    return perfis, sig


def visualizar(archive: bytes, password: str):
    perfis, sig = _open(archive,password)
    return {'emails':[{'Usuario':p['Usuario'], 'Email_Usuario':p['Email_Usuario']} for p in perfis],
            'assinatura_usuario':'milton','png_bytes':len(sig)}


def aplicar(archive: bytes, password: str, *, administrador: str, senha_admin: str,
           token_kkethilyn: str='', token_bruno: str=''):
    """Executa somente alteração complementar, com transação e contas existentes.

    Tokens SMTP opcionais são digitados no Streamlit, não distribuídos no arquivo.
    Tokens em branco mantêm as configurações pré-existentes.
    """
    _validar_admin_atual(administrador, senha_admin)
    perfis, signature = _open(archive, password)
    raw = repo.read_bytes('banco_usuarios.csv')
    with repo.engine().begin() as conn:
        item = conn.execute(select(repo.bancos_tb.c.conteudo,repo.bancos_tb.c.versao)
            .where((repo.bancos_tb.c.filial==repo.GLOBAL)&
                   (repo.bancos_tb.c.arquivo=='banco_usuarios.csv')).with_for_update()).first()
        if item is None:
            raise ValueError('Cadastro de usuários não encontrado no PostgreSQL.')
        users = pd.read_csv(io.BytesIO(bytes(item.conteudo)), dtype=str, keep_default_na=False)
        if not {'Usuario','Email_Usuario','Senha_App_Email','Servidor_SMTP','Porta_SMTP','Assinatura_PNG'}.issubset(users.columns):
            raise ValueError('Cadastro de usuários da aplicação está incompleto.')
        index = {str(n).strip().casefold():i for i,n in enumerate(users['Usuario'].tolist())}
        missing = [n for n in ALLOWED_USERS if n not in index]
        if missing:
            raise ValueError('Os usuários ainda não foram cadastrados: '+', '.join(sorted(missing)) +
                '. Cadastre esses logins antes de importar; não alteramos usuários ausentes.')
        tokens = {'kkethilyn':token_kkethilyn.strip(), 'bruno jerri':token_bruno.strip()}
        for profile in perfis:
            login = profile['Usuario'].strip().casefold()
            idx = index[login]
            users.at[idx,'Email_Usuario'] = profile['Email_Usuario']
            users.at[idx,'Servidor_SMTP'] = profile['Servidor_SMTP']
            users.at[idx,'Porta_SMTP'] = profile['Porta_SMTP']
            if tokens[login]:
                users.at[idx,'Senha_App_Email'] = tokens[login]
        caminho = repo.caminho_documento('assinatura_milton.png',assinatura=True)
        users.at[index['milton'],'Assinatura_PNG'] = caminho
        existing = conn.execute(select(repo.docs_tb.c.caminho).where(
            (repo.docs_tb.c.filial == repo.GLOBAL)&(repo.docs_tb.c.caminho == caminho))).first()
        if existing:
            conn.execute(update(repo.docs_tb).where((repo.docs_tb.c.filial == repo.GLOBAL)&
                (repo.docs_tb.c.caminho == caminho)).values(conteudo=signature, alterado_em=repo._now()))
        else:
            conn.execute(insert(repo.docs_tb).values(filial=repo.GLOBAL, caminho=caminho,
                conteudo=signature,alterado_em=repo._now()))
        version = repo._salvar_bytes_conn(conn,repo.GLOBAL,'banco_usuarios.csv',
            users.to_csv(index=False).encode('utf-8-sig'), esperado=int(item.versao))
    repo._revisions()[repo._revision_key(repo.GLOBAL,'banco_usuarios.csv')] = version
    try:
        f = Path(caminho)
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_bytes(signature)
    except OSError:
        pass  # Banco PostgreSQL contém uma cópia persistente.
    return {'perfis':2,'assinaturas':1,'usuarios_existentes':len(users)}
