"""Backup e restauração criptografados dos usuários globais da Intranet Stang.

Os CSVs dos perfis podem conter senha de aplicativo SMTP; não exportar em ZIP
sem criptografia. Senhas de login PBKDF2 são preservadas como hashes (não são
reversíveis). Assinaturas PNG e filiais autorizadas acompanham cada perfil.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import pandas as pd
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import insert, select

import infra_persistencia as repo
from seguranca_senhas import PREFIXO, gerar_hash, verificar_senha

MARCADOR = b'STANG-CONTAS-ENC-V1\n'
MAX_ENCRYPTED = 125_000_000
MAX_UNCOMPRESSED = 175_000_000
MAX_FILES = 2000
MAX_DOC = 30_000_000


def _senha_backup_valida(senha):
    if not isinstance(senha, str) or len(senha) < 14 or not senha.strip():
        raise ValueError('Crie uma senha de proteção do backup com pelo menos 14 caracteres.')


def _chave(senha, sal):
    return Scrypt(salt=sal, length=32, n=2**15, r=8, p=1).derive(senha.encode('utf-8'))


def _criptografar(payload, senha):
    _senha_backup_valida(senha)
    sal = os.urandom(16)
    nonce = os.urandom(12)
    cifrado = AESGCM(_chave(senha, sal)).encrypt(nonce, payload, MARCADOR)
    return MARCADOR + sal + nonce + cifrado


def _descriptografar(blob, senha):
    if len(blob) > MAX_ENCRYPTED:
        raise ValueError('Arquivo de backup maior do que o permitido.')
    if not blob.startswith(MARCADOR) or len(blob) < len(MARCADOR) + 16 + 12 + 16:
        raise ValueError('Selecione um backup de contas .stangbak criado nesta versão.')
    _senha_backup_valida(senha)
    pos = len(MARCADOR)
    sal, nonce, cifrado = blob[pos:pos+16], blob[pos+16:pos+28], blob[pos+28:]
    try:
        return AESGCM(_chave(senha, sal)).decrypt(nonce, cifrado, MARCADOR)
    except InvalidTag as exc:
        raise ValueError('Senha de proteção incorreta ou arquivo de backup danificado.') from exc


def _csv_usuarios(data, *, migrar_legado=False):
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError('CSV de usuários inválido dentro do backup.') from exc
    req = {'Usuario','Senha','Admin','Permissoes','Filiais','Validade',
           'Email_Usuario','Senha_App_Email','Servidor_SMTP','Porta_SMTP','Assinatura_PNG'}
    if not req.issubset(set(df.columns)):
        raise ValueError('Cadastro de usuários incompleto: faltam campos de autenticação ou perfil.')
    if df.empty or len(df)>10000:
        raise ValueError('Backup de usuários vazio ou excessivamente grande.')
    usernames = df['Usuario'].str.strip()
    if (usernames.eq('') | usernames.str.len().gt(120)).any() or usernames.str.casefold().duplicated().any():
        raise ValueError('Backup contém nomes de usuários inválidos ou duplicados.')
    if not df['Admin'].isin(['Sim','Não']).all() or not (df['Admin']=='Sim').any():
        raise ValueError('Backup precisa conter ao menos um administrador.')
    if df['Senha'].eq('').any():
        raise ValueError('Há usuários sem senha no backup.')
    if migrar_legado:
        # Versões originais podiam armazenar senha de login em texto puro.
        # A cópia EXPORTADA sempre contém o hash, nunca o texto puro.
        for i, value in df['Senha'].items():
            if not value.startswith(PREFIXO):
                df.at[i,'Senha'] = gerar_hash(value)
    for h in df['Senha']:
        # Valida o formato sem calcular PBKDF2 com iterações controladas pelo backup.
        partes = str(h).split('$')
        try:
            valido = (len(partes)==4 and partes[0]=='pbkdf2_sha256' and
                      100_000 <= int(partes[1]) <= 2_000_000 and
                      len(bytes.fromhex(partes[2]))==16 and
                      len(bytes.fromhex(partes[3]))==32)
        except (ValueError,TypeError):
            valido = False
        if not valido:
            raise ValueError('Backup contém formato de hash de senha de login inválido.')
    return df


def _ler_usuarios_atuais():
    raw = repo.read_bytes('banco_usuarios.csv')
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)


def _assinatura_referenciada(valor):
    path = str(valor).strip()
    if not path or path.lower() in ('none','nan','null'):
        return None
    if 'uploads_assinaturas/' not in path.replace('\\','/'):
        raise ValueError('Um usuário possui caminho de assinatura inesperado. Corrija o cadastro antes de fazer backup.')
    return repo._doc_valid(path)


def _assinaturas_a_exportar(df):
    caminhos = {_assinatura_referenciada(p) for p in df['Assinatura_PNG']}
    caminhos.discard(None)
    with repo.engine().connect() as conn:
        docs = conn.execute(select(repo.docs_tb.c.caminho,repo.docs_tb.c.conteudo)
                            .where(repo.docs_tb.c.filial == repo.GLOBAL)).all()
    documentos = {c:bytes(v) for c,v in docs if 'uploads_assinaturas/' in c}
    for caminho in caminhos:
        if caminho not in documentos:
            local = Path(caminho)
            if local.is_file():
                documentos[caminho] = local.read_bytes()
            else:
                raise ValueError('Assinatura de usuário não encontrada: ' + Path(caminho).name +
                                 '. Reenvie essa assinatura no cadastro antes de fazer o backup.')
    for caminho,data in documentos.items():
        repo._doc_valid(caminho)
        if len(data) > MAX_DOC:
            raise ValueError('Assinatura acima do limite de tamanho: ' + Path(caminho).name)
    return documentos


def _validar_admin_atual(login, senha_admin):
    """Exige reautenticação antes de exportar/restaurar credenciais sensíveis."""
    df = _ler_usuarios_atuais()
    row = df[df['Usuario'].str.casefold() == str(login).casefold()]
    if (row.empty or str(row.iloc[0]['Admin']) != 'Sim' or
            not verificar_senha(senha_admin, str(row.iloc[0]['Senha']))):
        raise ValueError('Senha do administrador conectado incorreta.')


def criar_backup_contas(senha_backup, *, administrador, senha_admin):
    _validar_admin_atual(administrador, senha_admin)
    df = _csv_usuarios(repo.read_bytes('banco_usuarios.csv'), migrar_legado=True)
    documentos = _assinaturas_a_exportar(df)
    with repo.engine().connect() as conn:
        filiais = [dict(r) for r in conn.execute(select(repo.filiais_tb)).mappings().all()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('manifest.json', json.dumps({'aplicativo':'IntranetStang','tipo':'contas_globais',
                  'versao_formato':1, 'total_usuarios':len(df), 'total_assinaturas':len(documentos),
                  'gerado_em':repo._now().isoformat()}, ensure_ascii=False))
        z.writestr('usuarios/banco_usuarios.csv',df.to_csv(index=False).encode('utf-8-sig'))
        z.writestr('usuarios/filiais.json',json.dumps(filiais,ensure_ascii=False))
        for caminho, conteudo in documentos.items():
            z.writestr('assinaturas/'+caminho, conteudo)
    resultado = _criptografar(buffer.getvalue(),senha_backup)
    if len(resultado) > MAX_ENCRYPTED:
        raise ValueError("Backup de usuários acima do limite de 125 MB.")
    return resultado


def _abrir_backup(blob, senha):
    data = _descriptografar(blob, senha)
    docs = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = z.infolist()
        if len(members)>MAX_FILES or sum(m.file_size for m in members)>MAX_UNCOMPRESSED:
            raise ValueError('Backup com quantidade ou tamanho excessivo de arquivos.')
        names = [m.filename for m in members]
        if len(names)!=len(set(names)):
            raise ValueError('Backup contém entradas duplicadas.')
        obrigatorios = {'manifest.json','usuarios/banco_usuarios.csv','usuarios/filiais.json'}
        if not obrigatorios.issubset(set(names)):
            raise ValueError('Backup de contas incompleto.')
        for m in members:
            if m.is_dir() or (m.filename not in obrigatorios and not m.filename.startswith('assinaturas/')):
                raise ValueError('Backup contém arquivos inesperados.')
            if m.file_size > MAX_DOC:
                raise ValueError('Arquivo interno excessivamente grande.')
        if z.testzip() is not None:
            raise ValueError('Backup corrompido.')
        manifest = json.loads(z.read('manifest.json'))
        if (manifest.get('aplicativo')!='IntranetStang' or manifest.get('tipo')!='contas_globais' or
                manifest.get('versao_formato')!=1):
            raise ValueError('Formato de backup de contas incompatível.')
        df = _csv_usuarios(z.read('usuarios/banco_usuarios.csv'))
        filiais = json.loads(z.read('usuarios/filiais.json'))
        if not isinstance(filiais,list) or len(filiais)>1000:
            raise ValueError('Lista de filiais inválida.')
        codigos = set()
        for f in filiais:
            if not isinstance(f,dict) or not isinstance(f.get('codigo'),str) or not re.fullmatch(r'[A-Z0-9_-]{1,40}',f['codigo']):
                raise ValueError('Código de filial inválido.')
            if f['codigo'] in codigos or not str(f.get('nome','')).strip() or len(f['nome'])>180:
                raise ValueError('Nome ou código duplicado de filial.')
            codigos.add(f['codigo'])
        if not codigos:
            raise ValueError('Backup não contém cadastro de filiais.')
        for _,row in df.iterrows():
            refs = [x.strip() for x in row['Filiais'].split(',') if x.strip()]
            if not set(refs).issubset(codigos):
                raise ValueError('Usuário vinculado a uma filial ausente do backup.')
        for name in names:
            if not name.startswith('assinaturas/'):
                continue
            caminho = name[len('assinaturas/'):]
            if not caminho or 'uploads_assinaturas/' not in caminho:
                raise ValueError('Assinatura com caminho inválido.')
            repo._doc_valid(caminho)
            docs[caminho] = z.read(name)
        for ref in df['Assinatura_PNG']:
            caminho = _assinatura_referenciada(ref)
            if caminho and caminho not in docs:
                raise ValueError('Backup está sem uma assinatura vinculada a usuário.')
        return df, filiais, docs


def visualizar_backup_contas(blob, senha):
    df, filiais, docs = _abrir_backup(blob,senha)
    return {'usuarios':len(df),'administradores':int((df['Admin']=='Sim').sum()),
            'filiais':len(filiais),'assinaturas':len(docs),
            'nomes':[str(x) for x in df['Usuario']]}


def restaurar_backup_contas(blob, senha, *, administrador, senha_admin):
    """Concilia por nome de login. Preserva contas que não estão no backup.

    Importação, documentos e cadastro de filiais são transacionais no PostgreSQL.
    O usuário admin atual pode ter sua senha revertida ao hash salvo no backup.
    """
    _validar_admin_atual(administrador, senha_admin)
    df, filiais, docs = _abrir_backup(blob,senha)
    atuais = _ler_usuarios_atuais()
    if list(atuais.columns)!=list(df.columns):
        for c in df.columns:
            if c not in atuais.columns: atuais[c] = ''
        for c in atuais.columns:
            if c not in df.columns: df[c] = ''
        df = df[atuais.columns]
    indice = {str(v).casefold():i for i,v in enumerate(atuais['Usuario'])}
    adicionados, atualizados = 0, 0
    for _,row in df.iterrows():
        codigo = str(row['Usuario']).casefold()
        if codigo in indice:
            atuais.loc[indice[codigo], :] = row.to_list()
            atualizados += 1
        else:
            atuais = pd.concat([atuais,pd.DataFrame([row])], ignore_index=True)
            indice[codigo] = len(atuais)-1
            adicionados += 1
    if not (atuais['Admin']=='Sim').any():
        raise ValueError('Restauração não pode remover o último administrador.')
    csv_final = atuais.to_csv(index=False).encode('utf-8-sig')
    with repo.engine().begin() as conn:
        existentes = set(conn.execute(select(repo.filiais_tb.c.codigo)).scalars().all())
        for f in filiais:
            if f['codigo'] not in existentes:
                conn.execute(insert(repo.filiais_tb).values(codigo=f['codigo'],nome=f['nome'],ativo=int(f.get('ativo',1))))
        repo._salvar_bytes_conn(conn,repo.GLOBAL,'banco_usuarios.csv',csv_final,forcar=True)
        for caminho,conteudo in docs.items():
            row = conn.execute(select(repo.docs_tb.c.caminho).where(
                (repo.docs_tb.c.filial==repo.GLOBAL)&(repo.docs_tb.c.caminho==caminho))).first()
            if row:
                conn.execute(repo.update(repo.docs_tb).where(
                    (repo.docs_tb.c.filial==repo.GLOBAL)&(repo.docs_tb.c.caminho==caminho))
                    .values(conteudo=conteudo,alterado_em=repo._now()))
            else:
                conn.execute(insert(repo.docs_tb).values(filial=repo.GLOBAL,caminho=caminho,
                                                     conteudo=conteudo,alterado_em=repo._now()))
    repo._revisions().pop(repo._revision_key(repo.GLOBAL,'banco_usuarios.csv'),None)
    # Atualiza arquivos locais já em cache; no próximo deploy são recuperados do banco.
    for caminho,conteudo in docs.items():
        try:
            p = Path(caminho)
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(conteudo)
        except OSError:
            pass  # O backup permanece gravado no banco persistente.
    return {'adicionados':adicionados,'atualizados':atualizados,'assinaturas':len(docs),'filiais':len(filiais)}
