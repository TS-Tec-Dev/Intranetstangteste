# Testes funcionais sem depender de hospedagem Streamlit.
import sys, types, importlib.util
from datetime import date
import pandas as pd
class Context:
    def __enter__(self): return self
    def __exit__(self,*a): return None
class Stub(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit');self.session_state={'tema_stang':'Escuro'};self.outputs=[]
    def markdown(self, html, **kw): self.outputs.append(str(html))
    def columns(self, nums, **kwargs): return [Context() for _ in range(len(nums))]
    def text_input(self,*args,**kw): return ''
    def selectbox(self,*args,**kw): return kw.get('options',args[1] if len(args)>1 else [])[0] if 'options' in kw else args[1][0]
    def checkbox(self,*args,**kw): return False
    def info(self,*args,**kw): self.outputs.append(str(args))
    def download_button(self,*args,**kw): self.outputs.append('DOWNLOAD:'+str(kw.get('file_name')))
mock=Stub()
sys.modules['streamlit']=mock
path='/mnt/data/stang_quadros_v2061/quadros_modernos.py'
spec=importlib.util.spec_from_file_location('quadros_modernos',path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# XSS escaping, badge status and column labels
record=pd.DataFrame([{
 'ID':1076, 'Solicitante':'<script>alert(1)</script>', 'Status':'Em Aberto',
 'Prioridade':'URGENTE', 'Descricao':'TEXTO COM <img src=x onerror=alert(2)>'},
 {'ID':1075,'Solicitante':'BRUNO', 'Status':'Finalizada','Prioridade':'BAIXA','Descricao':'Tudo certo'}])
mod.aplicar_estilo()
shown=mod.render_tabela(record,chave='test_os',titulo='Ordens de Serviço',ordenar_por='ID',decrescente=True)
html=''.join(mock.outputs)
assert '<script>' not in html and '<img src=x' not in html
assert '&lt;script&gt;' in html and '&lt;img src=x' in html
assert 'stg-bad' in html and 'stg-good' in html and 'stg-warning' in html
assert len(shown)==2

# Busca literal sem regex
assert len(mod.preparar_tabela(record,'BRUNO'))==1
assert len(mod.preparar_tabela(record,'alert(2)'))==1
assert len(mod.preparar_tabela(record,'['))==0
assert mod._valor(date(2026,11,13))=='13/11/2026'

# FMs: períodos e dias restantes; não modificar DataFrame de origem
origem=pd.DataFrame([{'FM':'FM1','Data_Realizada':'2026-11-03','Dias_Prazo':'5','Periodo':'Diário'},
 {'FM':'FM2','Data_Realizada':'2026-11-03','Dias_Prazo':'15','Periodo':'Quinzenal'}])
f=mod.preparar_fms(origem, hoje=date(2026,11,13))
assert f.iloc[0]['Dias_Restantes']==-5
assert f.iloc[1]['Dias_Restantes']==5
assert 'Vencimento' not in origem.columns
assert mod.resumir_fms(f)[2][1]==1
assert mod.resumir_fms(f)[3][1]==1

# Não superestimar números em compras com vários itens por ID
comp=pd.DataFrame([
 {'ID_Compra':'553','Status':'Compra Realizada', 'Orcamento_Assinado':'orc1.pdf'},
 {'ID_Compra':'553','Status':'Compra Realizada', 'Orcamento_Assinado':'orc1.pdf'},
 {'ID_Compra':'554','Status':'Em Aberto', 'Orcamento_Assinado':'None'}])
res=mod.resumir_compras(comp)
assert res[0][1]==2 and res[1][1]==1 and res[2][1]==1 and res[3][1]==1, res

# Troca claro/escuro e títulos corretos
mock.session_state['tema_stang']='Claro';mock.outputs=[]
mod.indicadores([('Teste',3,'good')]);assert 'light' in mock.outputs[0]

print('8 grupos de verificações funcionais passaram.')

# A nova barra tem 3 colunas em UMA linha (sem linha separada para página).
_mock_old = mock.columns
col_calls=[]
def _cols(spec, **kw):
    col_calls.append((list(spec), kw))
    return _mock_old(spec, **kw)
mock.columns = _cols
mock.outputs=[]
several=pd.DataFrame([{'ID':i,'Status':'Em Aberto'} for i in range(45)])
mod.render_tabela(several, chave='test_layout', titulo='Tabela de O.S.')
assert len(col_calls)==1, col_calls
assert len(col_calls[0][0])==3, col_calls
assert col_calls[0][1].get('gap')=='small'

# Mudar para uma busca que reduz páginas não deve deixar página inválida.
mock.session_state['stg_pagina_test_pag'] = 19
mod.render_tabela(several,chave='test_pag',titulo='O.S.')
assert mock.session_state['stg_pagina_test_pag']==1

# A tabela de compras não deve manter os 5 seletores globais antigos.
codigo_compra=open('/mnt/data/stang_quadros_v2061/Intranet.py',encoding='utf-8').read()
secao=codigo_compra.split('st.markdown("### Gerenciar Solicitações e Anexar Orçamento")',1)[1].split('ids_compras =',1)[0]
assert 'f_comp_data' not in secao and 'f_comp_solic' not in secao
assert 'f_comp_cat' not in secao and 'f_comp_item' not in secao
assert 'f_comp_status' not in secao
assert 'quadros.render_tabela(' in secao
print('3 verificações adicionais de layout, paginação e filtros passaram.')
