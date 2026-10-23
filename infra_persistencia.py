"""Persistência externa da IntranetStang.

Não grava dados operacionais no repositório: a aplicação pode ser implantada novamente
sem apagar os registros. Em produção configurar DATABASE_URL (PostgreSQL/Supabase).
Sem DATABASE_URL usa SQLite APENAS para desenvolvimento/local; disco Streamlit Cloud é efêmero.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, LargeBinary, MetaData, Numeric, String,
    Table, Text, UniqueConstraint, create_engine, delete, func, insert, select, update
)
from sqlalchemy.exc import IntegrityError

BASE_DIR = Path(__file__).resolve().parent
LOCAL_DIR = BASE_DIR / '.runtime_data'
LOCAL_DIR.mkdir(exist_ok=True)
REVISAO_PERSISTENCIA_RH = 'v2.0.4-hotfix-2026-10-23'
DEFAULT_FILIAL = 'MANDALLA'
GLOBAL = '_GLOBAL'
DATABASE_FILES = {'banco_os.csv', 'banco_fms.csv', 'banco_compras.csv', 'banco_usuarios.csv'}
TEMPLATES = {
    'banco_os.csv': ['ID','Data_Criacao','Solicitante','Setor','Equipamento','Tipo_Manutencao','Prioridade','Descricao','Solucao','Itens_Trocados','finalizado_por','Data_Termino','Status'],
    'banco_fms.csv': ['FM','Data_Realizada','Periodo','Dias_Prazo'],
    'banco_compras.csv': ['ID_Compra','Data_Solicitacao','Solicitante','Setor','Categoria','Item','Quantidade','Observacoes','Status','Orcamento_Assinado','NF_Anexada','Boleto_Anexado','Assinado_Por','Email_Enviado'],
    'banco_usuarios.csv': ['Usuario','Senha','Validade','Permissoes','Admin','Assinatura_PNG','Email_Usuario','Senha_App_Email','Servidor_SMTP','Porta_SMTP','Filiais'],
}
metadata = MetaData()
filiais_tb = Table('intr_filiais', metadata,
    Column('codigo', String(40), primary_key=True), Column('nome', String(180), nullable=False),
    Column('ativo', Integer, nullable=False, default=1))
bancos_tb = Table('intr_bancos', metadata,
    Column('filial', String(40), primary_key=True), Column('arquivo', String(80), primary_key=True),
    Column('conteudo', LargeBinary, nullable=False), Column('versao', Integer, nullable=False),
    Column('alterado_em', DateTime, nullable=False))
historico_tb = Table('intr_historico', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False), Column('arquivo', String(80), nullable=False),
    Column('versao', Integer, nullable=False), Column('conteudo', LargeBinary, nullable=False),
    Column('registrado_em', DateTime, nullable=False))
migracoes_tb = Table('intr_migracoes', metadata,
    Column('codigo', String(100), primary_key=True), Column('executado_em', DateTime, nullable=False))
docs_tb = Table('intr_documentos', metadata,
    Column('filial', String(40), primary_key=True), Column('caminho', String(600), primary_key=True),
    Column('conteudo', LargeBinary, nullable=False), Column('alterado_em', DateTime, nullable=False))
itens_tb = Table('intr_almox_itens', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False), Column('tipo', String(20), nullable=False),
    Column('codigo', String(80), nullable=False), Column('descricao', String(300), nullable=False),
    Column('unidade', String(20), nullable=False, default='UN'),
    Column('localizacao', String(200), nullable=False, default=''),
    Column('patrimonio', String(100), nullable=False, default=''),
    Column('estoque', Numeric(16,3), nullable=False, default=0),
    Column('minimo', Numeric(16,3), nullable=False, default=0),
    Column('observacoes', Text, nullable=False, default=''),
    Column('ativo', Integer, nullable=False, default=1),
    UniqueConstraint('filial','tipo','codigo', name='uq_intr_item_filial_tipo_codigo'))
mov_tb = Table('intr_almox_movimentos', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False), Column('item_id', Integer, ForeignKey('intr_almox_itens.id'), nullable=False),
    Column('tipo', String(25), nullable=False), Column('quantidade', Numeric(16,3), nullable=False),
    Column('saldo_apos', Numeric(16,3), nullable=False), Column('responsavel', String(180), nullable=False, default=''),
    Column('usuario', String(100), nullable=False), Column('documento', String(140), nullable=False, default=''),
    Column('observacoes', Text, nullable=False, default=''), Column('data', DateTime, nullable=False))


# RH: dados segregados por filial. Sem prontuários ou resultados clínicos.
rh_colaboradores_tb = Table('intr_rh_colaboradores', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False),
    Column('matricula', String(50), nullable=False), Column('nome', String(180), nullable=False),
    Column('setor', String(120), nullable=False, default=''),
    Column('cargo', String(120), nullable=False, default=''),
    Column('admissao', Date, nullable=False),
    Column('monitorar_ferias_desde', Date, nullable=False),
    Column('jornada_diaria_min', Integer, nullable=False, default=480),
    Column('ativo', Integer, nullable=False, default=1),
    Column('observacoes', String(500), nullable=False, default=''),
    UniqueConstraint('filial','matricula', name='uq_intr_rh_matricula'))
rh_ciclos_tb = Table('intr_rh_ciclos', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False),
    Column('colaborador_id', Integer, ForeignKey('intr_rh_colaboradores.id'), nullable=False),
    Column('aquisitivo_inicio', Date, nullable=False),
    Column('faltas_injustificadas', Integer, nullable=False, default=0),
    Column('observacao', String(400), nullable=False, default=''),
    UniqueConstraint('filial','colaborador_id','aquisitivo_inicio', name='uq_intr_rh_ciclo'))
rh_ferias_tb = Table('intr_rh_ferias', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False),
    Column('colaborador_id', Integer, ForeignKey('intr_rh_colaboradores.id'), nullable=False),
    Column('aquisitivo_inicio', Date, nullable=False),
    Column('inicio', Date, nullable=False), Column('fim', Date, nullable=False),
    Column('dias', Integer, nullable=False),
    Column('situacao', String(30), nullable=False, default='Programadas'),
    Column('concordancia_fracionamento', Integer, nullable=False, default=0),
    Column('observacoes', String(500), nullable=False, default=''),
    Column('usuario', String(100), nullable=False, default=''),
    Column('criado_em', DateTime, nullable=False))
rh_horas_tb = Table('intr_rh_horas', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False),
    Column('colaborador_id', Integer, ForeignKey('intr_rh_colaboradores.id'), nullable=False),
    Column('dia', Date, nullable=False),
    Column('entrada', String(5), nullable=False), Column('saida', String(5), nullable=False),
    Column('intervalo_min', Integer, nullable=False, default=0),
    Column('previsto_min', Integer, nullable=False),
    Column('trabalhado_min', Integer, nullable=False),
    Column('saldo_min', Integer, nullable=False),
    Column('observacoes', String(500), nullable=False, default=''),
    Column('usuario', String(100), nullable=False), Column('atualizado_em', DateTime, nullable=False),
    UniqueConstraint('filial','colaborador_id','dia', name='uq_intr_rh_dia'))
rh_asos_tb = Table('intr_rh_asos', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False),
    Column('colaborador_id', Integer, ForeignKey('intr_rh_colaboradores.id'), nullable=False),
    Column('tipo', String(60), nullable=False),
    Column('realizado_em', Date, nullable=True),
    Column('vencimento', Date, nullable=False),
    Column('agendado_para', Date, nullable=True),
    Column('situacao_agendamento', String(30), nullable=False, default='Não agendado'),
    Column('observacoes', String(500), nullable=False, default=''),
    Column('atualizado_em', DateTime, nullable=False))
rh_auditoria_tb = Table('intr_rh_auditoria', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('filial', String(40), nullable=False), Column('tabela', String(60), nullable=False),
    Column('registro_id', Integer, nullable=False), Column('acao', String(35), nullable=False),
    Column('antes', Text, nullable=True), Column('usuario', String(100), nullable=False),
    Column('data', DateTime, nullable=False))

RH_TABLES = {
    'colaboradores': rh_colaboradores_tb, 'ciclos': rh_ciclos_tb,
    'ferias': rh_ferias_tb, 'horas': rh_horas_tb, 'asos': rh_asos_tb,
    'auditoria': rh_auditoria_tb,
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def config_database_url():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        try:
            import streamlit as st
            url = str(st.secrets.get('DATABASE_URL', '')).strip()
        except Exception:
            pass
    return url


@lru_cache(maxsize=2)
def _engine(url):
    if not url:
        url = 'sqlite:///' + str((LOCAL_DIR / 'intranet_local.sqlite3').resolve())
    url = url.replace('postgres://', 'postgresql://', 1)
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=600, connect_args={'timeout': 30} if url.startswith('sqlite:') else {})
    metadata.create_all(engine)
    with engine.begin() as conn:
        # Supabase expõe tabelas do schema public por API; sem políticas RLS os
        # dados de usuários e anexos poderiam ficar visíveis ao papel anon.
        # A conexão da aplicação deve ser o proprietário do banco (role postgres).
        if engine.dialect.name == 'postgresql':
            for tb in metadata.sorted_tables:
                conn.exec_driver_sql(f'ALTER TABLE {tb.name} ENABLE ROW LEVEL SECURITY')
        if conn.execute(select(filiais_tb.c.codigo).where(filiais_tb.c.codigo == DEFAULT_FILIAL)).first() is None:
            conn.execute(insert(filiais_tb).values(codigo=DEFAULT_FILIAL, nome='Base Mandalla - Itajaí', ativo=1))
    return engine


def engine():
    return _engine(config_database_url())


def modo_producao():
    return bool(config_database_url())


def _active():
    try:
        import streamlit as st
        return st.session_state.get('filial_ativa', DEFAULT_FILIAL)
    except Exception:
        return DEFAULT_FILIAL


def escopo(arquivo, filial=None):
    return GLOBAL if Path(arquivo).name == 'banco_usuarios.csv' else (filial or _active())


def _revision_key(scope, name):
    return f'{scope}:{name}'


def _revisions():
    try:
        import streamlit as st
        if 'rev_bancos' not in st.session_state:
            st.session_state.rev_bancos = {}
        return st.session_state.rev_bancos
    except Exception:
        if not hasattr(_revisions, '_local'):
            _revisions._local = {}
        return _revisions._local


def _seed_bytes(nome, scope):
    if scope in (DEFAULT_FILIAL, GLOBAL) and (BASE_DIR / nome).is_file():
        data = (BASE_DIR / nome).read_bytes()
        if data.strip():
            return data
    return pd.DataFrame(columns=TEMPLATES[nome]).to_csv(index=False).encode('utf-8-sig')


def read_bytes(nome, filial=None):
    nome = Path(nome).name
    if nome not in DATABASE_FILES:
        raise ValueError('Banco desconhecido')
    scope = escopo(nome, filial)
    eng = engine()
    with eng.begin() as conn:
        record = conn.execute(select(bancos_tb.c.conteudo, bancos_tb.c.versao).where(
            (bancos_tb.c.filial == scope) & (bancos_tb.c.arquivo == nome))).first()
        if record is None:
            seed = _seed_bytes(nome, scope)
            try:
                conn.execute(insert(bancos_tb).values(filial=scope, arquivo=nome, conteudo=seed, versao=1, alterado_em=_now()))
                record = (seed, 1)
            except IntegrityError:
                # Concorrência no primeiro acesso: relê na próxima transação.
                raise RuntimeError('Inicialização simultânea do banco. Atualize a página.')
    _revisions()[_revision_key(scope, nome)] = int(record[1])
    return bytes(record[0])


def ler_csv(nome, **kwargs):
    name = Path(str(nome)).name
    if name not in DATABASE_FILES:
        return pd.read_csv(nome, **kwargs)
    raw = read_bytes(name)
    return pd.read_csv(io.BytesIO(raw), **kwargs)


def _salvar_bytes_conn(conn, scope, nome, data, esperado=None, forcar=False):
    record = conn.execute(select(bancos_tb.c.conteudo, bancos_tb.c.versao).where(
        (bancos_tb.c.filial == scope) & (bancos_tb.c.arquivo == nome))).first()
    if record is None:
        if esperado not in (None, 0) and not forcar:
            raise RuntimeError('Conflito: banco alterado por outro usuário. Atualize a página e tente novamente.')
        conn.execute(insert(bancos_tb).values(filial=scope, arquivo=nome, conteudo=data, versao=1, alterado_em=_now()))
        return 1
    versao = int(record.versao)
    if not forcar and esperado != versao:
        raise RuntimeError('Conflito de gravação: outro usuário modificou estes dados. Atualize a página antes de salvar novamente.')
    conn.execute(insert(historico_tb).values(filial=scope, arquivo=nome, conteudo=bytes(record.conteudo), versao=versao, registrado_em=_now()))
    conn.execute(update(bancos_tb).where((bancos_tb.c.filial == scope) & (bancos_tb.c.arquivo == nome))
                 .values(conteudo=data, versao=versao+1, alterado_em=_now()))
    old = conn.execute(select(historico_tb.c.id).where(
        (historico_tb.c.filial == scope) & (historico_tb.c.arquivo == nome))
        .order_by(historico_tb.c.id.desc()).offset(20)).scalars().all()
    if old:
        conn.execute(delete(historico_tb).where(historico_tb.c.id.in_(old)))
    return versao+1


def salvar_bytes(nome, data, filial=None, forcar=False):
    nome = Path(nome).name
    if nome not in DATABASE_FILES:
        raise ValueError('Arquivo não aceito')
    scope = escopo(nome, filial)
    key = _revision_key(scope, nome)
    esperado = _revisions().get(key)
    with engine().begin() as conn:
        n = _salvar_bytes_conn(conn, scope, nome, bytes(data), esperado=esperado, forcar=forcar)
    _revisions()[key] = n
    return n


def salvar_csv(df, nome):
    if Path(str(nome)).name not in DATABASE_FILES:
        raise ValueError('Não é um banco CSV cadastrado')
    raw = df.to_csv(index=False).encode('utf-8-sig')
    try:
        return salvar_bytes(nome, raw)
    except RuntimeError as ex:
        if 'Conflito' not in str(ex) and 'conflito' not in str(ex):
            raise
        try:
            import streamlit as st
            st.error(str(ex))
            st.stop()
        except ImportError:
            raise ex


def listar_filiais():
    with engine().connect() as conn:
        return [dict(row) for row in conn.execute(select(filiais_tb).where(filiais_tb.c.ativo == 1)
                .order_by(filiais_tb.c.nome)).mappings()]


def cadastrar_filial(codigo, nome):
    codigo = re.sub(r'[^A-Z0-9_-]', '', codigo.upper().strip())
    if not codigo or len(codigo) > 40 or not nome.strip():
        raise ValueError('Informe um código sem espaços e uma descrição.')
    with engine().begin() as conn:
        conn.execute(insert(filiais_tb).values(codigo=codigo, nome=nome.strip()[:180], ativo=1))
    return codigo


def _doc_valid(caminho):
    path = str(caminho).replace('\\', '/')
    parts = Path(path).parts
    if Path(path).is_absolute() or '..' in parts or not path or len(path) > 600:
        raise ValueError('Caminho de documento inválido')
    if not (path.startswith('uploads_') or path.startswith('orcamentos_assinados/') or path.startswith('.runtime_data/cache/')):
        raise ValueError('Documentos devem estar nas pastas próprias de uploads')
    return path


def caminho_documento(nome, assinatura=False):
    nome = Path(str(nome)).name
    # Basename sanitizado; não utilizar nome de arquivo vindo do usuário como caminho.
    nome = re.sub(r'[^\w.\- ]', '_', nome, flags=re.UNICODE)[:200]
    scope = GLOBAL if assinatura else _active()
    pasta = 'uploads_assinaturas' if assinatura else 'uploads_orcamentos'
    path = LOCAL_DIR / 'cache' / scope / pasta / nome
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.relative_to(BASE_DIR).as_posix()


def _doc_scope(caminho):
    return GLOBAL if 'uploads_assinaturas/' in str(caminho).replace('\\','/') else _active()


def registrar_documento(caminho, conteudo=None):
    caminho = _doc_valid(caminho)
    conteudo = Path(caminho).read_bytes() if conteudo is None else bytes(conteudo)
    if len(conteudo) > 30_000_000:
        raise ValueError('Anexo acima de 30 MB; diminua o arquivo antes de enviar')
    scope = _doc_scope(caminho)
    with engine().begin() as conn:
        record = conn.execute(select(docs_tb.c.caminho).where(
            (docs_tb.c.filial==scope) & (docs_tb.c.caminho==caminho))).first()
        if record is None:
            conn.execute(insert(docs_tb).values(filial=scope,caminho=caminho,conteudo=conteudo,alterado_em=_now()))
        else:
            conn.execute(update(docs_tb).where((docs_tb.c.filial==scope) & (docs_tb.c.caminho==caminho))
                         .values(conteudo=conteudo,alterado_em=_now()))
    return caminho


def recuperar_documento(caminho):
    try:
        caminho = _doc_valid(caminho)
    except (ValueError, TypeError):
        return False
    # Arquivos legados que vieram com o ZIP continuam acessíveis, enquanto não houver
    # uma cópia remota; novos anexos ficam isolados por filial no cache local.
    if Path(caminho).exists():
        return True
    with engine().connect() as conn:
        row = conn.execute(select(docs_tb.c.conteudo).where(
            (docs_tb.c.filial==_doc_scope(caminho)) & (docs_tb.c.caminho==caminho))).first()
    if row is None:
        return False
    path = Path(caminho)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(row[0]))
    return True


def sincronizar_documentos():
    """Recupera somente anexos ausentes, sem transferir blobs em toda execução."""
    with engine().connect() as conn:
        nomes = conn.execute(select(docs_tb.c.caminho).where(
            docs_tb.c.filial.in_([_active(), GLOBAL]))).scalars().all()
    for caminho in nomes:
        if not Path(caminho).exists():
            try:
                recuperar_documento(caminho)
            except (OSError, ValueError):
                continue


def importar_anexos_legados():
    """Migração única dos anexos presentes no ZIP original; não ressuscita arquivos removidos."""
    codigo = 'anexos_zip_inicial_v1'
    total = 0
    with engine().begin() as conn:
        if conn.execute(select(migracoes_tb.c.codigo).where(migracoes_tb.c.codigo == codigo)).first():
            return 0
        for pasta in ('uploads_orcamentos','orcamentos_assinados','uploads_assinaturas'):
            raiz = BASE_DIR / pasta
            if not raiz.is_dir():
                continue
            for f in raiz.iterdir():
                if not f.is_file() or f.stat().st_size > 30_000_000:
                    continue
                caminho = f.relative_to(BASE_DIR).as_posix()
                esc = GLOBAL if pasta=='uploads_assinaturas' else DEFAULT_FILIAL
                presente = conn.execute(select(docs_tb.c.caminho).where(
                    (docs_tb.c.filial==esc) & (docs_tb.c.caminho==caminho))).first()
                if not presente:
                    conn.execute(insert(docs_tb).values(filial=esc,caminho=caminho,
                                 conteudo=f.read_bytes(),alterado_em=_now()))
                    total+=1
        conn.execute(insert(migracoes_tb).values(codigo=codigo,executado_em=_now()))
    return total


def remover_documento(caminho):
    caminho = _doc_valid(caminho)
    with engine().begin() as conn:
        conn.execute(delete(docs_tb).where((docs_tb.c.filial==_doc_scope(caminho)) & (docs_tb.c.caminho==caminho)))
    Path(caminho).unlink(missing_ok=True)


def backup_filial(filial, incluir_usuarios=False):
    if incluir_usuarios:
        raise ValueError('Os usuários só podem ser exportados pelo backup criptografado de contas.')
    scope_list = [filial] + ([GLOBAL] if incluir_usuarios else [])
    with engine().connect() as conn:
        regs = conn.execute(select(bancos_tb).where(bancos_tb.c.filial.in_(scope_list))).mappings().all()
        docs = conn.execute(select(docs_tb).where(docs_tb.c.filial.in_(scope_list))).mappings().all()
        itens = conn.execute(select(itens_tb).where(itens_tb.c.filial==filial)).mappings().all()
        movimentos = conn.execute(select(mov_tb).where(mov_tb.c.filial==filial)).mappings().all()
        rh_rows = {nome: [dict(x) for x in conn.execute(select(t).where(t.c.filial==filial)).mappings().all()] for nome,t in RH_TABLES.items()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('manifest.json', json.dumps({'aplicativo':'IntranetStang','versao_formato':1,
                'filial':filial,'inclui_usuarios':incluir_usuarios,'gerado_em':_now().isoformat()}, indent=2))
        z.writestr(f'inventario/{filial}/itens.json', json.dumps([dict(r) for r in itens], default=str, ensure_ascii=False))
        z.writestr(f'inventario/{filial}/movimentos.json', json.dumps([dict(r) for r in movimentos], default=str, ensure_ascii=False))
        for nome, regs_rh in rh_rows.items():
            z.writestr(f'rh/{filial}/{nome}.json', json.dumps(regs_rh, ensure_ascii=False, default=str))
        for r in regs:
            z.writestr(f'dados/{r["filial"]}/{r["arquivo"]}', bytes(r['conteudo']))
        for d in docs:
            # Backup dos documentos usa caminho exato armazenado na tabela.
            _doc_valid(d['caminho'])
            z.writestr(f'documentos/{d["filial"]}/{d["caminho"]}', bytes(d['conteudo']))
    return output.getvalue()


def restaurar_filial(blob, filial, incluir_usuarios=False):
    if len(blob) > 200_000_000:
        raise ValueError('Backup acima do limite de 200 MB')
    planos_csv, planos_doc = [], []
    inventario = {}
    rh_import = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if z.testzip() is not None:
            raise ValueError('Backup ZIP com erro de integridade')
        info = json.loads(z.read('manifest.json'))
        if info.get('aplicativo') != 'IntranetStang' or info.get('versao_formato') != 1:
            raise ValueError('Backup incompatível com esta versão')
        if info.get('filial') != filial:
            raise ValueError('O backup pertence a outra filial; escolha a filial correta')
        permitidos = {filial} | ({GLOBAL} if incluir_usuarios else set())
        if sum(i.file_size for i in z.infolist()) > 250_000_000:
            raise ValueError('Backup descompactado acima do limite de 250 MB')
        for item in z.infolist():
            if item.is_dir() or item.filename == 'manifest.json':
                continue
            if item.file_size > 30_000_000:
                raise ValueError('Anexo excessivamente grande no backup')
            parts = item.filename.split('/')
            if len(parts) < 3 or parts[1] not in permitidos:
                if len(parts) >= 2 and parts[1] == GLOBAL and not incluir_usuarios:
                    continue
                raise ValueError('Backup contém arquivo inesperado')
            scope = parts[1]
            if parts[0] == 'dados' and len(parts)==3 and parts[2] in DATABASE_FILES:
                if (parts[2]=='banco_usuarios.csv') != (scope==GLOBAL):
                    raise ValueError('Arquivo de dados em escopo incorreto')
                # Verifica cabeçalho antes de gravar.
                data = z.read(item)
                pd.read_csv(io.BytesIO(data), nrows=0)
                planos_csv.append((scope, parts[2], data))
            elif parts[0] == 'inventario' and len(parts)==3 and scope==filial and parts[2] in ('itens.json','movimentos.json'):
                inventario[parts[2]] = json.loads(z.read(item))
                if not isinstance(inventario[parts[2]],list) or len(inventario[parts[2]])>100000:
                    raise ValueError('Inventário inválido ou excessivamente grande')
            elif parts[0] == 'rh' and len(parts)==3 and scope==filial and parts[2].endswith('.json'):
                nome_rh = parts[2][:-5]
                if nome_rh not in RH_TABLES: raise ValueError('Tabela RH desconhecida no backup')
                rh_import[nome_rh] = json.loads(z.read(item))
                if not isinstance(rh_import[nome_rh], list) or len(rh_import[nome_rh]) > 200000:
                    raise ValueError('Dados RH inválidos ou grandes demais')
            elif parts[0] == 'documentos':
                caminho = '/'.join(parts[2:])
                _doc_valid(caminho)
                if _doc_scope(caminho) == GLOBAL and scope != GLOBAL:
                    raise ValueError('Escopo inválido de assinatura')
                planos_doc.append((scope,caminho,z.read(item)))
            else:
                raise ValueError('Entrada não reconhecida no backup')
    if not planos_csv and not inventario and not rh_import:
        raise ValueError('Este ZIP não contém dados para restaurar')
    # Validação de formato e filial antes de alterar qualquer dado do banco.
    if rh_import:
        esperadas = set(RH_TABLES)
        if set(rh_import) != esperadas:
            raise ValueError('Backup RH incompleto: restauração cancelada')
        for tabela_nome, rows in rh_import.items():
            permitidos = set(RH_TABLES[tabela_nome].c.keys())
            for row in rows:
                if not isinstance(row, dict) or row.get('filial') != filial or not set(row).issubset(permitidos):
                    raise ValueError('Backup RH com campos ou filial inválidos')
    with engine().begin() as conn:
        if rh_import:
            # Restaura apenas RH da filial escolhida, respeitando as chaves estrangeiras.
            for nm in ('auditoria','horas','asos','ferias','ciclos','colaboradores'):
                conn.execute(delete(RH_TABLES[nm]).where(RH_TABLES[nm].c.filial==filial))
            mapas = {}
            for row in rh_import['colaboradores']:
                v = {k:val for k,val in row.items() if k!='id'}
                for datekey in ('admissao','monitorar_ferias_desde'):
                    v[datekey] = datetime.fromisoformat(v[datekey]).date()
                n = conn.execute(insert(rh_colaboradores_tb).values(**v)).inserted_primary_key[0]
                mapas[row['id']] = n
            for nm in ('ciclos','ferias','horas','asos'):
                for row in rh_import[nm]:
                    v = {k:val for k,val in row.items() if k!='id'}
                    if v['colaborador_id'] not in mapas:
                        raise ValueError('Backup RH referencia colaborador ausente')
                    v['colaborador_id'] = mapas[v['colaborador_id']]
                    for k in ('aquisitivo_inicio','inicio','fim','dia','realizado_em','vencimento','agendado_para'):
                        if k in v and v[k] is not None: v[k]=datetime.fromisoformat(v[k]).date()
                    for k in ('criado_em','atualizado_em'):
                        if k in v and v[k] is not None: v[k]=datetime.fromisoformat(v[k])
                    conn.execute(insert(RH_TABLES[nm]).values(**v))
            for row in rh_import['auditoria']:
                v = {k:val for k,val in row.items() if k!='id'}
                v['data']=datetime.fromisoformat(v['data'])
                conn.execute(insert(rh_auditoria_tb).values(**v))
        if 'itens.json' in inventario and 'movimentos.json' in inventario:
            from decimal import Decimal
            conn.execute(delete(mov_tb).where(mov_tb.c.filial==filial))
            conn.execute(delete(itens_tb).where(itens_tb.c.filial==filial))
            id_map = {}
            for item in inventario['itens.json']:
                if item.get('filial') != filial:
                    raise ValueError('Item de outra filial no backup')
                values = {k:item[k] for k in ('filial','tipo','codigo','descricao','unidade','localizacao','patrimonio','estoque','minimo','observacoes','ativo')}
                values['estoque'] = Decimal(str(values['estoque']))
                values['minimo'] = Decimal(str(values['minimo']))
                new_id = conn.execute(insert(itens_tb).values(**values)).inserted_primary_key[0]
                id_map[item['id']] = new_id
            for mov in inventario['movimentos.json']:
                if mov.get('filial') != filial or mov.get('item_id') not in id_map:
                    raise ValueError('Movimento incompatível com a filial no backup')
                values = {k:mov[k] for k in ('filial','tipo','responsavel','usuario','documento','observacoes')}
                values.update(item_id=id_map[mov['item_id']],quantidade=Decimal(str(mov['quantidade'])),
                    saldo_apos=Decimal(str(mov['saldo_apos'])),data=datetime.fromisoformat(mov['data']))
                conn.execute(insert(mov_tb).values(**values))
        for scope, name, data in planos_csv:
            _salvar_bytes_conn(conn, scope, name, data, forcar=True)
        for scope, caminho, data in planos_doc:
            row = conn.execute(select(docs_tb.c.caminho).where(
                (docs_tb.c.filial==scope)&(docs_tb.c.caminho==caminho))).first()
            if row:
                conn.execute(update(docs_tb).where((docs_tb.c.filial==scope)&(docs_tb.c.caminho==caminho))
                             .values(conteudo=data, alterado_em=_now()))
            else:
                conn.execute(insert(docs_tb).values(filial=scope,caminho=caminho,conteudo=data,alterado_em=_now()))
    _revisions().clear()
    for scope, caminho, data in planos_doc:
        p = Path(caminho)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return len(planos_csv), len(planos_doc)


def versoes_recentes(filial):
    with engine().connect() as conn:
        rows = conn.execute(select(historico_tb.c.arquivo,historico_tb.c.versao,historico_tb.c.registrado_em)
             .where(historico_tb.c.filial==filial).order_by(historico_tb.c.id.desc()).limit(30)).mappings().all()
    return [dict(r) for r in rows]


def listar_itens(filial, tipo):
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(itens_tb).where((itens_tb.c.filial==filial)&(itens_tb.c.tipo==tipo))
            .order_by(itens_tb.c.descricao)).mappings()]


def cadastrar_item(filial, tipo, codigo, descricao, unidade, localizacao, patrimonio, minimo, observacoes, usuario, saldo=0):
    from decimal import Decimal
    saldo = Decimal(str(saldo))
    if saldo < 0: raise ValueError('Estoque inicial não pode ser negativo')
    with engine().begin() as conn:
        result = conn.execute(insert(itens_tb).values(filial=filial,tipo=tipo,codigo=codigo.strip().upper(),
            descricao=descricao.strip().upper(),unidade=unidade,localizacao=localizacao.strip(),
            patrimonio=patrimonio.strip(),minimo=Decimal(str(minimo)),observacoes=observacoes,estoque=saldo,ativo=1))
        if saldo > 0:
            conn.execute(insert(mov_tb).values(filial=filial,item_id=result.inserted_primary_key[0],tipo='SALDO INICIAL',
                quantidade=saldo,saldo_apos=saldo,responsavel='',usuario=usuario,documento='',
                observacoes='Cadastro inicial',data=_now()))


def atualizar_item(filial, item_id, descricao, unidade, localizacao, patrimonio, minimo, observacoes, ativo):
    from decimal import Decimal
    with engine().begin() as conn:
        res = conn.execute(update(itens_tb).where((itens_tb.c.filial==filial)&(itens_tb.c.id==item_id))
            .values(descricao=descricao.strip().upper(),unidade=unidade,localizacao=localizacao.strip(),
                    patrimonio=patrimonio.strip(),minimo=Decimal(str(minimo)),observacoes=observacoes,ativo=int(ativo)))
        if res.rowcount != 1: raise ValueError('Item não encontrado nesta filial')


def excluir_item_cadastro(filial, item_id, tipo):
    """Exclusão lógica preserva transações, saldos e vínculo filial/tipo."""
    from decimal import Decimal
    from collections import defaultdict
    with engine().begin() as conn:
        cond = ((itens_tb.c.filial == filial) & (itens_tb.c.id == item_id)
                & (itens_tb.c.tipo == tipo) & (itens_tb.c.ativo == 1))
        if tipo == 'FERRAMENTA':
            saldo_emprestimos = defaultdict(Decimal)
            registros = conn.execute(select(mov_tb.c.tipo, mov_tb.c.quantidade, mov_tb.c.responsavel).where(
                (mov_tb.c.filial == filial) & (mov_tb.c.item_id == item_id) &
                mov_tb.c.tipo.in_(['EMPRÉSTIMO', 'DEVOLUÇÃO'])
            )).all()
            for r in registros:
                usuario = (r.responsavel or '').strip().casefold()
                saldo_emprestimos[usuario] += Decimal(str(r.quantidade)) * (
                    1 if r.tipo == 'EMPRÉSTIMO' else -1)
            if any(pendente > 0 for pendente in saldo_emprestimos.values()):
                raise ValueError('Há ferramentas emprestadas. Registre a devolução antes de excluir.')
        res = conn.execute(update(itens_tb).where(cond).values(ativo=0))
        if res.rowcount != 1:
            raise ValueError('Item não encontrado ou já excluído nesta filial.')


def reativar_item_cadastro(filial, item_id, tipo):
    with engine().begin() as conn:
        cond = ((itens_tb.c.filial == filial) & (itens_tb.c.id == item_id)
                & (itens_tb.c.tipo == tipo) & (itens_tb.c.ativo == 0))
        res = conn.execute(update(itens_tb).where(cond).values(ativo=1))
        if res.rowcount != 1:
            raise ValueError('Item não encontrado ou já ativo nesta filial.')


def movimentar_item(filial, item_id, movimento, quantidade, usuario, responsavel='', documento='', observacoes=''):
    from decimal import Decimal
    qtd = Decimal(str(quantidade))
    if qtd <= 0: raise ValueError('A quantidade deve ser maior que zero')
    tipos = {'ENTRADA':1,'DEVOLUÇÃO':1,'SAÍDA':-1,'EMPRÉSTIMO':-1,'AJUSTE +':1,'AJUSTE -':-1}
    if movimento not in tipos: raise ValueError('Movimentação desconhecida')
    if movimento in ('EMPRÉSTIMO','DEVOLUÇÃO') and not responsavel.strip():
        raise ValueError('Informe o responsável pelo empréstimo ou devolução')
    with engine().begin() as conn:
        # UPDATE condicional garante que duas saídas simultâneas não produzam saldo negativo.
        delta = qtd * tipos[movimento]
        cond = (itens_tb.c.id==item_id) & (itens_tb.c.filial==filial) & (itens_tb.c.ativo==1)
        if delta < 0:
            cond &= itens_tb.c.estoque >= -delta
        res = conn.execute(update(itens_tb).where(cond).values(estoque=itens_tb.c.estoque+delta))
        if res.rowcount != 1:
            raise ValueError('Estoque insuficiente, item inativo ou pertencente a outra filial')
        saldo = conn.execute(select(itens_tb.c.estoque).where(itens_tb.c.id==item_id)).scalar_one()
        if movimento == 'DEVOLUÇÃO':
            anteriores = conn.execute(select(mov_tb.c.tipo,mov_tb.c.quantidade,mov_tb.c.responsavel).where(
                (mov_tb.c.filial==filial)&(mov_tb.c.item_id==item_id)&
                (mov_tb.c.tipo.in_(['EMPRÉSTIMO','DEVOLUÇÃO'])))).all()
            pendente = sum((Decimal(str(r.quantidade)) * (1 if r.tipo=='EMPRÉSTIMO' else -1)
                for r in anteriores if r.responsavel.strip().casefold()==responsavel.strip().casefold()),Decimal('0'))
            if pendente < qtd:
                raise ValueError(f'Devolução maior que o empréstimo em aberto deste responsável ({pendente}).')
        conn.execute(insert(mov_tb).values(filial=filial,item_id=item_id,tipo=movimento,
            quantidade=qtd,saldo_apos=saldo,responsavel=responsavel.strip(),usuario=usuario,
            documento=documento.strip(),observacoes=observacoes.strip(),data=_now()))
        return saldo


def listar_movimentacoes(filial,tipo):
    query = select(mov_tb.c.id,mov_tb.c.data,itens_tb.c.codigo,itens_tb.c.descricao,mov_tb.c.tipo,
        mov_tb.c.quantidade,mov_tb.c.saldo_apos,mov_tb.c.responsavel,mov_tb.c.usuario,
        mov_tb.c.documento,mov_tb.c.observacoes).join(itens_tb,mov_tb.c.item_id==itens_tb.c.id).where(
            (mov_tb.c.filial==filial)&(itens_tb.c.tipo==tipo)).order_by(mov_tb.c.id.desc()).limit(500)
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]


def emprestimos_pendentes(filial):
    from collections import defaultdict
    from decimal import Decimal
    q = select(itens_tb.c.codigo,itens_tb.c.descricao,mov_tb.c.tipo,mov_tb.c.quantidade,
        mov_tb.c.responsavel).join(itens_tb,mov_tb.c.item_id==itens_tb.c.id).where(
        (mov_tb.c.filial==filial)&(itens_tb.c.tipo=='FERRAMENTA')&
        (mov_tb.c.tipo.in_(['EMPRÉSTIMO','DEVOLUÇÃO'])))
    with engine().connect() as conn:
        rows=conn.execute(q).all()
    pend=defaultdict(Decimal)
    descriptions={}
    for r in rows:
        key=(r.codigo,r.responsavel.strip().upper())
        pend[key]+=Decimal(str(r.quantidade))*(1 if r.tipo=='EMPRÉSTIMO' else -1)
        descriptions[key]=r.descricao
    return [{'Código':k[0],'Item':descriptions[k],'Responsável':k[1],
             'Quantidade pendente':v} for k,v in pend.items() if v>0]
