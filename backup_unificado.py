"""Backup UNIFICADO da Intranet Stang: contas + todas as filiais em um único arquivo.

Versão 1 do formato STANGTOTAL. O conteúdo inteiro é criptografado com AES-256-GCM;
login permanece com hash, e os tokens SMTP permanecem apenas dentro do arquivo
criptografado. Restauração utiliza uma transação SQL para todas as filiais e contas.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select

import backup_contas
import infra_persistencia as repo
from controle_acesso_filiais import pode_gerir_todas_as_filiais

MAGIC = b'STANG-TOTAL-ENC-V1\n'
EXTENSAO = '.stangtotal'
MAX_ARQUIVO = 195_000_000  # Abaixo do limite de 200 MB usado no uploader.
MAX_DESCOMPACTADO = 310_000_000
MAX_FILIAIS = 100
MAX_ENTRADAS = 105


def _chave(senha: str, salt: bytes) -> bytes:
    if not isinstance(senha, str) or len(senha) < 14 or not senha.strip():
        raise ValueError('A senha de proteção do backup deve ter pelo menos 14 caracteres.')
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(senha.encode('utf-8'))


def _criptografar(data: bytes, senha: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(_chave(senha, salt)).encrypt(nonce, data, MAGIC)
    result = MAGIC + salt + nonce + encrypted
    if len(result) > MAX_ARQUIVO:
        raise ValueError('O backup ultrapassou 195 MB. Reduza anexos antigos ou utilize armazenamento corporativo de backups.')
    return result


def _descriptografar(blob: bytes, senha: str) -> bytes:
    if len(blob) > MAX_ARQUIVO or not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 44:
        raise ValueError('Arquivo inválido ou maior do que o limite da restauração (.stangtotal).')
    pos = len(MAGIC)
    try:
        return AESGCM(_chave(senha, blob[pos:pos+16])).decrypt(
            blob[pos+16:pos+28], blob[pos+28:], MAGIC
        )
    except InvalidTag as exc:
        raise ValueError('Senha de proteção inválida ou arquivo de backup danificado.') from exc


def _autorizar(usuario: str, senha_admin: str) -> None:
    # Não aceitar simplesmente session_state['Admin'] = True: valida senha atual,
    # perfil e autorização explícita a TODAS as filiais ativas no banco.
    backup_contas._validar_admin_atual(usuario, senha_admin)
    usuarios = backup_contas._ler_usuarios_atuais()
    rows = usuarios[usuarios['Usuario'].astype(str).str.casefold() == str(usuario).casefold()]
    if rows.empty or not pode_gerir_todas_as_filiais(rows.iloc[0].to_dict(), repo.listar_filiais()):
        raise PermissionError('Somente administrador autorizado a todas as filiais pode exportar ou restaurar o backup geral.')


def _filiais_todas() -> list[str]:
    with repo.engine().connect() as conn:
        codes = list(conn.execute(select(repo.filiais_tb.c.codigo).order_by(repo.filiais_tb.c.codigo)).scalars())
    if not codes or len(codes) > MAX_FILIAIS:
        raise ValueError('Quantidade de filiais inválida para o backup.')
    for codigo in codes:
        if not re.fullmatch(r'[A-Z0-9_-]{1,40}', codigo):
            raise ValueError('Código de filial inesperado no banco.')
    return codes


def gerar_backup(*, usuario: str, senha_admin: str, senha_backup: str) -> bytes:
    """Um único arquivo, criptografado, para todas as filiais e todos os usuários."""
    _autorizar(usuario, senha_admin)
    filiais = _filiais_todas()
    conteudos = {'contas.stangbak': backup_contas.criar_backup_contas(
        senha_backup, administrador=usuario, senha_admin=senha_admin
    )}
    for filial in filiais:
        conteudos[f'filiais/{filial}.zip'] = repo.backup_filial(filial, incluir_usuarios=False)
    resumo = {
        'aplicativo': 'IntranetStang', 'tipo': 'backup_unificado', 'versao_formato': 1,
        'criado_em': datetime.now(timezone.utc).isoformat(),
        'filiais': filiais, 'inclui_contas': True,
        'arquivos': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                     for name, data in conteudos.items()},
    }
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('manifest.json', json.dumps(resumo, ensure_ascii=False, sort_keys=True))
        for name, data in conteudos.items():
            z.writestr(name, data)
    return _criptografar(mem.getvalue(), senha_backup)


def _abrir(blob: bytes, senha: str):
    data = _descriptografar(blob, senha)
    arquivos = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if (len(infos) > MAX_ENTRADAS or sum(i.file_size for i in infos) > MAX_DESCOMPACTADO
                or z.testzip() is not None):
            raise ValueError('Backup com entradas excessivas ou integridade comprometida.')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Backup com nomes duplicados ou sem manifesto.')
        manifest = json.loads(z.read('manifest.json'))
        if (manifest.get('aplicativo'), manifest.get('tipo'), manifest.get('versao_formato')) != (
                'IntranetStang', 'backup_unificado', 1):
            raise ValueError('Formato de backup incompatível.')
        filiais = manifest.get('filiais')
        hashes = manifest.get('arquivos')
        if not isinstance(filiais, list) or not 1 <= len(filiais) <= MAX_FILIAIS or len(set(filiais)) != len(filiais):
            raise ValueError('Lista de filiais inválida.')
        if not all(isinstance(f, str) and re.fullmatch(r'[A-Z0-9_-]{1,40}', f) for f in filiais):
            raise ValueError('Há código de filial inválido.')
        esperado = {'contas.stangbak', *(f'filiais/{filial}.zip' for filial in filiais)}
        if set(names) != esperado | {'manifest.json'} or not isinstance(hashes, dict) or set(hashes) != esperado:
            raise ValueError('Backup incompleto ou contendo arquivos inesperados.')
        for name in sorted(esperado):
            payload = z.read(name)
            info = hashes[name]
            if not isinstance(info, dict) or info.get('sha256') != hashlib.sha256(payload).hexdigest() or info.get('bytes') != len(payload):
                raise ValueError('Falha de integridade no arquivo: ' + name)
            arquivos[name] = payload
    # Validar as duas categorias ANTES de abrir transação de restauração.
    contas = backup_contas.visualizar_backup_contas(arquivos['contas.stangbak'], senha)
    for filial in filiais:
        with zipfile.ZipFile(io.BytesIO(arquivos[f'filiais/{filial}.zip'])) as z:
            if z.testzip() is not None:
                raise ValueError('Backup da filial danificado: ' + filial)
            info = json.loads(z.read('manifest.json'))
            if info.get('aplicativo') != 'IntranetStang' or info.get('versao_formato') != 1 or info.get('filial') != filial:
                raise ValueError('Backup da filial incompatível: ' + filial)
    return manifest, arquivos, contas


def visualizar_backup(blob: bytes, senha: str) -> dict:
    manifest, _, contas = _abrir(blob, senha)
    return {
        'filiais': len(manifest['filiais']),
        'filiais_codigos': manifest['filiais'],
        'usuarios': contas['usuarios'],
        'assinaturas': contas['assinaturas'],
        'gerado_em': manifest['criado_em'],
    }


def _limpar_cache_documentos(arquivos: dict, filiais: list[str], senha: str) -> None:
    """Após commit, elimina cópias locais potencialmente antigas dos arquivos restaurados."""
    paths = set()
    for filial in filiais:
        with zipfile.ZipFile(io.BytesIO(arquivos[f'filiais/{filial}.zip'])) as z:
            for name in z.namelist():
                if name.startswith(f'documentos/{filial}/'):
                    paths.add(name.split('/', 2)[2])
    # O backup de contas já valida nomes de assinatura na leitura criptografada.
    _, _, assinaturas = backup_contas._abrir_backup(arquivos['contas.stangbak'], senha)
    paths.update(assinaturas)
    for name in paths:
        try:
            safe = repo._doc_valid(name)
            path = (repo.BASE_DIR / safe).resolve()
            if path.is_relative_to(repo.BASE_DIR.resolve()) and path.is_file():
                path.unlink()
        except (OSError, ValueError):
            pass
    repo._revisions().clear()


def restaurar_backup(blob: bytes, senha: str, *, usuario: str, senha_admin: str) -> dict:
    _autorizar(usuario, senha_admin)
    manifest, arquivos, contas = _abrir(blob, senha)
    # Uma única transação para TODOS os CSVs, documentos, RH, almoxarifado e contas.
    # Se uma etapa falhar, rollback de todas as alterações feitas nesta operação.
    with repo.engine().begin() as conn:
        resultados = {}
        for filial in manifest['filiais']:
            resultados[filial] = repo.restaurar_filial(
                arquivos[f'filiais/{filial}.zip'], filial,
                incluir_usuarios=False, conexao_externa=conn
            )
        resumo_contas = backup_contas.restaurar_backup_contas(
            arquivos['contas.stangbak'], senha, administrador=usuario,
            senha_admin=senha_admin, conexao_externa=conn
        )
    # Cache local NÃO faz parte da transação SQL; só descartá-lo após commit.
    _limpar_cache_documentos(arquivos, manifest['filiais'], senha)
    return {'filiais': len(resultados), 'usuarios': contas['usuarios'],
            'documentos': sum(val[1] for val in resultados.values()),
            'contas': resumo_contas, 'criado_em': manifest['criado_em']}
