"""Importação conservadora das exportações visuais e anexos resgatados em 19/10/2026.

Este módulo NÃO é uma restauração: faz merge por chaves, preserva usuários,
permissões e valores existentes e registra um histórico por CSV afetado.
Os arquivos de importação devem ser enviados por administrador e nunca
publicados no repositório GitHub público.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import io
import re
from pathlib import Path
import zipfile

import pandas as pd
from sqlalchemy import select, insert

import infra_persistencia as repo

CSV_NAMES = {
    '2026-10-19T16-38_export.csv': 'banco_compras.csv',
    '2026-10-19T16-41_export.csv': 'banco_os.csv',
    '2026-10-19T16-46_export.csv': 'banco_fms.csv',
}
USER_EXPORT_NAME = '2026-10-19T16-42_export.csv'
PREFIX_CSV = '01_exportacoes_csv_originais/'
PREFIX_DOC = '02_anexos_originais/'
DOC_NAME_RE = re.compile(r'^pedido_(\d{1,12})_.+\.(?:pdf|png|jpe?g)$', re.IGNORECASE)
EMPTY = ('', 'none', 'nan', 'nat', 'null', 'não informado')
MAX_FILE = 30_000_000
MAX_UNPACKED = 200_000_000
MAX_TOTAL = 90_000_000


class ErroResgate(ValueError):
    pass


def _empty(value):
    return str(value).strip().lower() in EMPTY


def _clean(df):
    df = df.copy().fillna('')
    df = df.loc[:, [str(c) for c in df.columns if not str(c).startswith('Unnamed:')]]
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def _csv_bytes(df):
    return df.to_csv(index=False).encode('utf-8-sig')


def _read_csv(data, required):
    try:
        df = _clean(pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False))
    except Exception as e:
        raise ErroResgate('CSV ilegível ou inválido.') from e
    if not required.issubset(set(df.columns)):
        raise ErroResgate(f'CSV sem campos obrigatórios: {sorted(required - set(df.columns))}')
    if len(df) > 200_000:
        raise ErroResgate('Número excessivo de registros no CSV')
    return df


def _inputs(blob):
    if len(blob) > MAX_TOTAL:
        raise ErroResgate('ZIP maior do que o limite de 90 MB')
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError) as e:
        raise ErroResgate('Selecione o ZIP de resgate consolidado válido') from e
    with z:
        files = z.infolist()
        if len(files) > 3000 or sum(f.file_size for f in files) > MAX_UNPACKED:
            raise ErroResgate('Quantidade de arquivos ou tamanho descompactado excessivo')
        if any(f.file_size > MAX_FILE for f in files):
            raise ErroResgate('Há anexos acima do limite permitido')
        if z.testzip() is not None:
            raise ErroResgate('Falha na integridade interna do ZIP')
        csvs, documents = {}, []
        for info in files:
            name = info.filename
            if info.is_dir():
                continue
            # Somente paths e categorias EXATAMENTE esperados: sem escape por ../
            if '\\' in name or name.startswith('/') or any(p in ('.','..') for p in name.split('/')):
                raise ErroResgate('Caminho inseguro no ZIP')
            if name.startswith(PREFIX_CSV):
                basename = name[len(PREFIX_CSV):]
                if '/' not in basename and basename in CSV_NAMES:
                    csvs[CSV_NAMES[basename]] = z.read(info)
            elif name.startswith(PREFIX_DOC):
                basename = name[len(PREFIX_DOC):]
                if '/' not in basename:
                    match = DOC_NAME_RE.fullmatch(basename)
                    if match:
                        documents.append((match.group(1), basename, z.read(info)))
        if set(csvs) != set(CSV_NAMES.values()):
            raise ErroResgate('Faltam exportações de compras, O.S. ou FMs do resgate de 19/10/2026')
        if not documents:
            raise ErroResgate('Nenhum anexo encontrado no ZIP')
    raw_compras = _read_csv(csvs['banco_compras.csv'],
        {'ID_Compra','Data_Solicitacao','Solicitante','Setor','Categoria','Item','Quantidade','Status_Visual'})
    raw_os = _read_csv(csvs['banco_os.csv'], {'ID','Data_Criacao','Solicitante','Status','Equipamento'})
    raw_fms = _read_csv(csvs['banco_fms.csv'], {'FM','Data_Realizada','Periodo'})
    for col in ('ID_Compra',):
        if any(not re.fullmatch(r'\d{1,12}', str(v)) for v in raw_compras[col]):
            raise ErroResgate('CSV de compras contém ID inválido')
    if any(not re.fullmatch(r'\d{1,12}', str(v)) for v in raw_os['ID']):
        raise ErroResgate('CSV de O.S. contém ID inválido')
    if raw_os['ID'].duplicated().any():
        raise ErroResgate('Exportação de O.S. com IDs duplicados; requer conferência manual')
    if raw_fms['FM'].str.upper().duplicated().any():
        raise ErroResgate('Exportação de FMs com identificadores duplicados')
    # Os dados de usuários do backup foram extraídos de uma tabela visual.
    # NÃO contêm senha nem permissões; portanto não são importados.
    raw_compras = raw_compras.rename(columns={'Status_Visual': 'Status'})
    raw_compras['Status'] = raw_compras['Status'].map(
        lambda v: 'Compra Realizada' if 'Realizada' in v else
                  'Compra Recusada' if 'Recusada' in v else 'Compra em Aberta')
    raw_compras = raw_compras.drop(columns=['Orcamento_Assinado'], errors='ignore')
    raw_fms['Dias_Prazo'] = raw_fms['Periodo'].str.extract(r'\((\d+)\s+dias?\)', expand=False).fillna('')
    if (raw_fms['Dias_Prazo'] == '').any():
        raise ErroResgate('Há FMs com período não reconhecido; interrompendo sem inventar dias')
    raw_fms = raw_fms[['FM','Data_Realizada','Periodo','Dias_Prazo']]
    documents.sort(key=lambda t: t[1])
    # cópias repetidas do mesmo pedido e mesmo hash ficam representadas uma vez,
    # mas entre pedidos diferentes o vínculo é mantido separadamente.
    selected, seen = [], set()
    for pedido, basename, data in documents:
        digest = hashlib.sha256(data).hexdigest()
        key = (pedido, digest)
        if key not in seen:
            seen.add(key)
            selected.append((pedido, basename, data, digest))
    return {
        'banco_compras.csv':raw_compras,
        'banco_os.csv':raw_os,
        'banco_fms.csv':raw_fms,
        'anexos':selected,
        'hash':hashlib.sha256(blob).hexdigest(),
        'linhas':{'compras':len(raw_compras),'os':len(raw_os),'fms':len(raw_fms)},
    }


def _existing(conn, filial, filename):
    scope = repo.escopo(filename, filial)
    record = conn.execute(select(repo.bancos_tb.c.conteudo).where(
        (repo.bancos_tb.c.filial==scope) & (repo.bancos_tb.c.arquivo==filename))).scalar_one_or_none()
    return _read_csv(bytes(record) if record is not None else repo._seed_bytes(filename,scope), set())


def _columns(df, incoming):
    for col in incoming.columns:
        if col not in df.columns:
            df[col] = ''
    for col in df.columns:
        if col not in incoming.columns:
            incoming[col] = ''
    return df, incoming[df.columns]


def _merge_os(existing, incoming):
    existing = existing.copy(deep=True)
    existing, incoming = _columns(existing, incoming.copy())
    mapped = {str(v):i for i,v in enumerate(existing['ID'])}
    result = {'novos':0, 'campos_preenchidos':0}
    for _,row in incoming.iterrows():
        key = row['ID']
        if key not in mapped:
            existing.loc[len(existing)] = row
            mapped[key] = len(existing)-1
            result['novos'] += 1
        else:
            ix=mapped[key]
            for col in incoming.columns:
                if _empty(existing.at[ix,col]) and not _empty(row[col]):
                    existing.at[ix,col] = row[col]
                    result['campos_preenchidos']+=1
    return existing, result


def _identity_purchase(r):
    cols=('ID_Compra','Data_Solicitacao','Solicitante','Setor','Categoria','Item','Quantidade')
    return tuple(str(r.get(c,'')).strip().casefold() for c in cols)


def _merge_compras(existing, incoming):
    existing = existing.copy(deep=True)
    existing, incoming = _columns(existing, incoming.copy())
    used = defaultdict(list)
    for ix,row in existing.iterrows():
        used[_identity_purchase(row)].append(ix)
    consumed=Counter()
    result={'novos':0,'campos_preenchidos':0}
    for _, row in incoming.iterrows():
        key=_identity_purchase(row)
        pos=consumed[key]
        consumed[key]+=1
        if pos>=len(used[key]):
            existing.loc[len(existing)] = row
            result['novos'] += 1
        else:
            ix=used[key][pos]
            for col in incoming.columns:
                if _empty(existing.at[ix,col]) and not _empty(row[col]):
                    existing.at[ix,col]=row[col]
                    result['campos_preenchidos']+=1
    return existing,result


def _parse_iso(dt):
    try:
        return date.fromisoformat(str(dt)[:10])
    except (ValueError, TypeError):
        return None


def _fm_key(value):
    # Textos de FM mudaram entre agosto e outubro, mas o código FM 01/02/etc.
    # continua sendo o identificador. Evita duplicar 13 FMs por pequenas correções.
    m=re.match(r'^\s*FM\s*0*(\d+)\b',str(value),re.IGNORECASE)
    return ('codigo',int(m.group(1))) if m else ('texto',str(value).strip().casefold())


def _merge_fms(existing, incoming):
    existing = existing.copy(deep=True)
    existing, incoming = _columns(existing, incoming.copy())
    mapped={_fm_key(v):ix for ix,v in enumerate(existing['FM'])}
    result={'novos':0,'renovados':0}
    for _,row in incoming.iterrows():
        key=_fm_key(row['FM'])
        if key not in mapped:
            existing.loc[len(existing)] = row
            result['novos']+=1
        else:
            ix=mapped[key]
            antiga=_parse_iso(existing.at[ix,'Data_Realizada'])
            nova=_parse_iso(row['Data_Realizada'])
            if nova and (not antiga or nova>antiga):
                for c in ('FM','Data_Realizada','Periodo','Dias_Prazo'):
                    existing.at[ix,c]=row[c]
                result['renovados']+=1
    return existing,result


def _doc_path(filial, pedido, basename, digest):
    suffix=Path(basename).suffix.lower()
    return f'.runtime_data/cache/{filial}/uploads_orcamentos/pedido_{pedido}_resgate_{digest[:16]}{suffix}'


def _doc_stored(conn, filial, caminho):
    if conn.execute(select(repo.docs_tb.c.caminho).where(
        (repo.docs_tb.c.filial==filial) & (repo.docs_tb.c.caminho==caminho))).first():
        return True
    return (repo.BASE_DIR / caminho).is_file()


def _merge_docs(conn, filial, compras, anexos):
    plan_docs=[]
    first_link={}
    pedidos_compras=set(compras['ID_Compra'])
    pendentes=set()
    for pedido, basename, blob, digest in anexos:
        path=_doc_path(filial,pedido,basename,digest)
        repo._doc_valid(path)
        if pedido not in first_link:
            first_link[pedido]=path
        if pedido not in pedidos_compras:
            pendentes.add(pedido)
        if not _doc_stored(conn,filial,path):
            plan_docs.append((filial,path,blob))
    changed=0
    # Na exportação de tabela o campo original foi convertido para "✅ Anexado":
    # só apontamos para um arquivo recuperado quando não há anexo existente disponível.
    for ix, row in compras.iterrows():
        pedido=row['ID_Compra']
        if pedido not in first_link:
            continue
        path=row.get('Orcamento_Assinado','')
        if _empty(path) or not _doc_stored(conn,filial,path):
            if str(path)!=first_link[pedido]:
                compras.at[ix,'Orcamento_Assinado']=first_link[pedido]
                changed+=1
    return plan_docs,changed,sorted(pendentes,key=int)


def _process(blob, filial, executar):
    if not re.fullmatch(r'[A-Z0-9_-]{1,40}',str(filial)):
        raise ErroResgate('Código de filial inválido')
    parsed = _inputs(blob)
    with repo.engine().begin() as conn:
        filial_existe=conn.execute(select(repo.filiais_tb.c.codigo).where(repo.filiais_tb.c.codigo==filial)).first()
        if not filial_existe:
            raise ErroResgate('Filial de destino não cadastrada')
        # Impede dois resgates concorrentes (PostgreSQL) sem bloquear todas as filiais.
        if executar and conn.dialect.name=='postgresql':
            conn.exec_driver_sql('SELECT pg_advisory_xact_lock(hashtext(%s))',(f'INTRANET_RESGATE_{filial}',))
        original={k:_existing(conn,filial,k) for k in CSV_NAMES.values()}
        os_new, os_stats = _merge_os(original['banco_os.csv'],parsed['banco_os.csv'])
        fm_new, fm_stats = _merge_fms(original['banco_fms.csv'],parsed['banco_fms.csv'])
        comp_new,comp_stats = _merge_compras(original['banco_compras.csv'],parsed['banco_compras.csv'])
        comp_new, blank = _columns(comp_new, pd.DataFrame(columns=['Orcamento_Assinado']))
        docs,linked, pending=_merge_docs(conn,filial,comp_new,parsed['anexos'])
        new_dfs={'banco_compras.csv':comp_new,'banco_os.csv':os_new,'banco_fms.csv':fm_new}
        stats={
            'linhas_csv_original':parsed['linhas'],
            'compras_adicionadas':comp_stats['novos'],
            'os_adicionadas':os_stats['novos'],
            'fms_adicionados':fm_stats['novos'],
            'fms_renovados_mais_recentes':fm_stats['renovados'],
            'campos_os_preenchidos':os_stats['campos_preenchidos'],
            'campos_compras_preenchidos':comp_stats['campos_preenchidos'],
            'anexos_novos':len(docs),
            'documentos_vinculados':linked,
            'pedidos_com_anexos_sem_registro_no_csv':pending,
            'usuarios_e_senhas':'não alterados',
            'filial':filial,
            'arquivos_csv_atualizados':[]
        }
        changed={k: _csv_bytes(v)!=_csv_bytes(original[k]) for k,v in new_dfs.items()}
        stats['arquivos_csv_atualizados']=[k for k,v in changed.items() if v]
        if executar:
            # Não faz exclusões de itens/usuários/documentos em nenhuma hipótese.
            for filename, df in new_dfs.items():
                if changed[filename]:
                    repo._salvar_bytes_conn(conn,filial,filename,_csv_bytes(df),forcar=True)
            for scope,path,doc in docs:
                conn.execute(insert(repo.docs_tb).values(
                    filial=scope,caminho=path,conteudo=doc,alterado_em=repo._now()))
        return stats


def planejar_resgate(blob:bytes, filial:str):
    return _process(blob,filial,executar=False)


def importar_resgate(blob:bytes, filial:str):
    return _process(blob,filial,executar=True)


def ids_reservados_em_anexos(filial:str):
    """Impedir reutilização dos IDs 538/539 mesmo antes do CSV atualizado."""
    with repo.engine().connect() as conn:
        names=conn.execute(select(repo.docs_tb.c.caminho).where(repo.docs_tb.c.filial==filial)).scalars().all()
    result=[]
    for name in names:
        match=re.search(r'/pedido_(\d+)_resgate_[a-f0-9]{16}\.(?:pdf|png|jpe?g)$',name)
        if match:
            result.append(int(match.group(1)))
    return result
