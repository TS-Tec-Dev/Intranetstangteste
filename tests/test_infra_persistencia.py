"""Testes offline; o banco Supabase real deve ser configurado pelo proprietário."""
from pathlib import Path
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
import infra_persistencia as db
from seguranca_senhas import gerar_hash, verificar_senha

@pytest.fixture
def repo(tmp_path, monkeypatch):
    eng = create_engine('sqlite:///' + str(tmp_path/'test.sqlite'))
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(db.insert(db.filiais_tb).values(codigo='MANDALLA',nome='Itajaí',ativo=1))
        conn.execute(db.insert(db.filiais_tb).values(codigo='BASE2',nome='Filial dois',ativo=1))
    monkeypatch.setattr(db,'engine',lambda:eng)
    monkeypatch.setattr(db,'_active',lambda:'MANDALLA')
    db._revisions().clear()
    yield eng,monkeypatch
    db._revisions().clear()
    eng.dispose()


def test_filiais_separadas(repo):
    eng,monkeypatch=repo
    df=db.ler_csv('banco_os.csv',dtype=str)
    size=len(df)
    df.loc[len(df)]={'ID':'9999','Solicitante':'ITAJAI'}
    db.salvar_csv(df,'banco_os.csv')
    monkeypatch.setattr(db,'_active',lambda:'BASE2')
    df_2=db.ler_csv('banco_os.csv',dtype=str)
    assert df_2.empty
    assert 'ID' in df_2.columns
    monkeypatch.setattr(db,'_active',lambda:'MANDALLA')
    assert len(db.ler_csv('banco_os.csv',dtype=str))==size+1


def test_concorrencia_csv(repo):
    df=db.ler_csv('banco_os.csv',dtype=str)
    key='MANDALLA:banco_os.csv'
    original=db._revisions()[key]
    db._revisions()[key]=original-1
    with pytest.raises(RuntimeError,match='Conflito'):
        db.salvar_csv(df,'banco_os.csv')


def test_movimento_atomico_e_backup_inventario(repo):
    db.cadastrar_item('MANDALLA','FERRAMENTA','FR01','Furadeira','UN','Armário','PAT-01',1,'', 'admin',2)
    i=db.listar_itens('MANDALLA','FERRAMENTA')[0]
    assert db.movimentar_item('MANDALLA',i['id'],'EMPRÉSTIMO',1,'operacao','João') == Decimal('1.000')
    with pytest.raises(ValueError,match='Estoque insuficiente'):
        db.movimentar_item('MANDALLA',i['id'],'SAÍDA',2,'operacao')
    with pytest.raises(ValueError, match='Devolução maior'):
        db.movimentar_item('MANDALLA',i['id'],'DEVOLUÇÃO',2,'operacao','João')
    b=db.backup_filial('MANDALLA')
    db.movimentar_item('MANDALLA',i['id'],'DEVOLUÇÃO',1,'operacao','João')
    assert db.restaurar_filial(b,'MANDALLA') == (0,0)
    assert db.listar_itens('MANDALLA','FERRAMENTA')[0]['estoque']==Decimal('1.000')
    assert len(db.listar_movimentacoes('MANDALLA','FERRAMENTA'))==2


def test_escopo_e_arquivo_perigoso(repo):
    with pytest.raises(ValueError):db._doc_valid('../arquivo.pdf')
    with pytest.raises(ValueError):db._doc_valid('/etc/passwd')
    with pytest.raises(ValueError):db._doc_valid('segredo.env')


def test_senhas():
    h=gerar_hash('senha-grande-123')
    assert verificar_senha('senha-grande-123',h)
    assert not verificar_senha('senha-errada',h)
    assert verificar_senha('legada','legada')


def test_documentos_no_backup(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pasta=tmp_path/'uploads_orcamentos'
    pasta.mkdir()
    nome='uploads_orcamentos/recibo_teste.pdf'
    (pasta/'recibo_teste.pdf').write_bytes(b'%PDF-TESTE-PERSISTENCIA')
    db.registrar_documento(nome)
    backup=db.backup_filial('MANDALLA')
    (pasta/'recibo_teste.pdf').unlink()
    assert db.recuperar_documento(nome)
    assert (pasta/'recibo_teste.pdf').read_bytes()==b'%PDF-TESTE-PERSISTENCIA'
    assert db.restaurar_filial(backup,'MANDALLA')[1]==1
