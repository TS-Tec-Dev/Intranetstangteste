"""Testes do importador (dados sintéticos: NÃO dependem de documentos privados)."""
import io
from pathlib import Path
import zipfile

import pandas as pd
import pytest
from sqlalchemy import create_engine, select

import infra_persistencia as repo
import mod_resgate_2026 as resgate


@pytest.fixture
def banco(tmp_path,monkeypatch):
    eng=create_engine('sqlite:///'+str(tmp_path/'testedb.sqlite'))
    repo.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(repo.insert(repo.filiais_tb).values(codigo='MANDALLA',nome='Mandalla',ativo=1))
        c.execute(repo.insert(repo.filiais_tb).values(codigo='OUTRA',nome='Outra',ativo=1))
    monkeypatch.setattr(repo,'engine',lambda:eng)
    monkeypatch.setattr(repo,'_active',lambda:'MANDALLA')
    repo._revisions().clear()
    yield eng
    repo._revisions().clear()
    eng.dispose()


def fake_zip(docname='pedido_538_anexo.pdf'):
    csvs={
        '2026-10-19T16-38_export.csv':pd.DataFrame([{
            'ID_Compra':'501','Data_Solicitacao':'2026-09-21 11:00','Solicitante':'VOLMAR',
            'Setor':'OPERAÇÃO','Categoria':'Item Operacional','Item':'MÁSCARA',
            'Quantidade':'2','Status_Visual':'Compra Realizada 🟢',
            'Orcamento_Assinado':'✅ Anexado',
        }]),
        '2026-10-19T16-41_export.csv':pd.DataFrame([{
            'ID':'1058','Data_Criacao':'19/10/2026 11:00',
            'Solicitante':'TIAGO','Status':'Em Aberto','Equipamento':'BOMBA',
        }]),
        '2026-10-19T16-46_export.csv':pd.DataFrame([{
            'FM':'FM 01 - RELATÓRIO DO GERADOR','Data_Realizada':'2026-10-18',
            'Periodo':'Mensal (30 dias)',
        }]),
    }
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for name,df in csvs.items():
            z.writestr('01_exportacoes_csv_originais/'+name,df.to_csv(index=False))
        z.writestr('02_anexos_originais/'+docname,b'%PDF-1.4\nconteudo sintetico')
    return b.getvalue()


def count_rows(engine,scope,file):
    with engine.connect() as c:
        row=c.execute(select(repo.bancos_tb.c.conteudo).where(
            (repo.bancos_tb.c.filial==scope) & (repo.bancos_tb.c.arquivo==file))).first()
    return None if row is None else pd.read_csv(io.BytesIO(row[0]),dtype=str).fillna('')


def test_importacao_aditiva_isolamento_reimportacao(banco):
    upload=fake_zip()
    a=resgate.planejar_resgate(upload,'MANDALLA')
    assert a['compras_adicionadas']==1 and a['os_adicionadas']==1
    assert a['fms_renovados_mais_recentes']==1
    assert a['pedidos_com_anexos_sem_registro_no_csv']==['538']
    assert count_rows(banco,'MANDALLA','banco_compras.csv') is None
    resgate.importar_resgate(upload,'MANDALLA')
    assert count_rows(banco,'MANDALLA','banco_compras.csv')['ID_Compra'].tolist()==['501']
    assert count_rows(banco,'MANDALLA','banco_fms.csv').query("FM.str.contains('FM 01')",engine='python').shape[0]==1
    assert count_rows(banco,'OUTRA','banco_compras.csv') is None
    assert resgate.ids_reservados_em_anexos('MANDALLA')==[538]
    again=resgate.importar_resgate(upload,'MANDALLA')
    assert again['compras_adicionadas']==0
    assert again['os_adicionadas']==0
    assert again['anexos_novos']==0
    assert again['arquivos_csv_atualizados']==[]
    assert count_rows(banco,'MANDALLA','banco_usuarios.csv') is None


def test_nao_substituir_dados_operacionais_existentes(banco):
    initial=pd.DataFrame([{'ID_Compra':'501','Data_Solicitacao':'2026-09-21 11:00','Solicitante':'VOLMAR',
                          'Setor':'OPERAÇÃO','Categoria':'Item Operacional','Item':'MÁSCARA',
                          'Quantidade':'2','Status':'Compra Recusada',
                          'Observacoes':'Registro local auditado','Orcamento_Assinado':'None'}])
    repo.salvar_bytes('banco_compras.csv',initial.to_csv(index=False).encode(),forcar=True)
    result=resgate.importar_resgate(fake_zip(),'MANDALLA')
    assert result['compras_adicionadas']==0
    rec=count_rows(banco,'MANDALLA','banco_compras.csv').iloc[0]
    assert rec['Status']=='Compra Recusada'
    assert rec['Observacoes']=='Registro local auditado'


def test_zip_inseguro_rejeitado_sem_mutacoes(banco):
    with pytest.raises(resgate.ErroResgate,match='Caminho inseguro'):
        resgate.importar_resgate(fake_zip('pedido_538_anexo.pdf/../../s.pdf'),'MANDALLA')
    assert count_rows(banco,'MANDALLA','banco_os.csv') is None
