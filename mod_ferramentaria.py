"""Tela de ferramentas e materiais: operações sempre vinculadas à filial ativa."""
import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError
import infra_persistencia as repo


def renderizar(filial, usuario, administrador=False):
    st.markdown('# 🧰 Controle de Ferramentaria e Materiais')
    st.caption(f'Filial: **{filial}** · Estoque e histórico isolados para esta unidade')
    tab_tools, tab_mats = st.tabs(['🛠️ Ferramentas', '📦 Materiais'])
    for tab, tipo in ((tab_tools, 'FERRAMENTA'), (tab_mats, 'MATERIAL')):
        with tab:
            _painel_tipo(filial, usuario, administrador, tipo)


def _painel_tipo(filial, usuario, administrador, tipo):
    itens = repo.listar_itens(filial,tipo)
    ativos = [r for r in itens if r['ativo']==1]
    deficit = [r for r in ativos if r['estoque'] <= r['minimo']]
    k1,k2,k3 = st.columns(3)
    k1.metric('Itens cadastrados',len(ativos))
    k2.metric('Quantidade em estoque',f'{sum(float(r["estoque"]) for r in ativos):,.1f}')
    k3.metric('No estoque mínimo ou abaixo',len(deficit))
    aba1,aba2,aba3,aba4,aba5 = st.tabs(['📋 Estoque','➕ Cadastro','🔄 Movimentações','🕘 Histórico','🗑️ Excluir / Reativar'])
    with aba1:
        busca = st.text_input('Pesquisar código ou descrição',key=f'busca_{tipo}',placeholder='Nome, código, patrimônio ou localização')
        mostrar_inativos = st.checkbox('Mostrar itens excluídos / desativados', value=False, key=f'mostrar_inativos_{tipo}')
        exib = [r for r in itens if (mostrar_inativos or int(r['ativo']) == 1) and busca.lower() in ' '.join(str(r.get(k,'')) for k in ('codigo','descricao','patrimonio','localizacao')).lower()]
        if exib:
            df = pd.DataFrame(exib)
            df['alerta'] = df.apply(lambda r: '⚠️ Mínimo' if r['ativo'] and r['estoque'] <= r['minimo'] else '',axis=1)
            st.dataframe(df.rename(columns={'codigo':'Código','descricao':'Descrição','estoque':'Saldo disponível',
                'minimo':'Mínimo','patrimonio':'Patrimônio / Nº série','localizacao':'Localização',
                'unidade':'Unid.','ativo':'Ativo','alerta':'Alerta'})[['Código','Descrição','Saldo disponível','Unid.',
                'Mínimo','Alerta','Localização','Patrimônio / Nº série','Ativo']],hide_index=True,use_container_width=True)
        else:
            st.info('Ainda não há itens cadastrados nessa categoria.')
        if administrador and itens:
            with st.expander('✏️ Editar cadastro / desativar item'):
                opts = {f'{r["codigo"]} — {r["descricao"]}':r for r in itens}
                sel = st.selectbox('Item para editar',list(opts),key=f'edit_sel_{tipo}')
                r=opts[sel]
                with st.form(f'edit_form_{tipo}_{r["id"]}'):
                    desc=st.text_input('Descrição',value=r['descricao'])
                    c1,c2,c3=st.columns(3)
                    unidade=c1.text_input('Unidade',value=r['unidade'])
                    local=c2.text_input('Localização',value=r['localizacao'])
                    patrimonio=c3.text_input('Patrimônio / série',value=r['patrimonio'])
                    minimo=st.number_input('Estoque mínimo',min_value=0.0,value=float(r['minimo']),step=1.0)
                    obs=st.text_area('Observações',value=r['observacoes'])
                    ativo=st.checkbox('Item ativo',value=bool(r['ativo']))
                    if st.form_submit_button('Salvar cadastro'):
                        if not desc.strip() or not unidade.strip():st.error('Preencha descrição e unidade.')
                        else:
                            repo.atualizar_item(filial,r['id'],desc,unidade,local,patrimonio,minimo,obs,ativo)
                            st.success('Cadastro alterado sem modificar o histórico e o saldo.');st.rerun()
    with aba2:
        st.caption('Ferramentas podem ser controladas por patrimônio/série, com empréstimo e devolução. Materiais usam entrada e saída de estoque.')
        with st.form(f'cad_item_{tipo}',clear_on_submit=True):
            c1,c2=st.columns([1,2])
            codigo=c1.text_input('Código único *')
            desc=c2.text_input('Descrição *')
            c3,c4,c5=st.columns(3)
            unidade=c3.selectbox('Unidade',['UN','PC','CX','KG','L','M','PAR','KIT'],key=f'un_{tipo}')
            local=c4.text_input('Localização')
            patrimonio=c5.text_input('Patrimônio / Número de série')
            c6,c7=st.columns(2)
            inicial=c6.number_input('Saldo inicial',min_value=0.0,step=1.0,key=f'init_{tipo}')
            minimo=c7.number_input('Estoque mínimo',min_value=0.0,step=1.0,key=f'min_{tipo}')
            obs=st.text_area('Observações',key=f'obs_cad_{tipo}')
            if st.form_submit_button('💾 Cadastrar item',use_container_width=True):
                if not codigo.strip() or not desc.strip():
                    st.error('Código e descrição são obrigatórios.')
                else:
                    try:
                        repo.cadastrar_item(filial,tipo,codigo,desc,unidade,local,patrimonio,minimo,obs,usuario,inicial)
                        st.success('Item cadastrado no estoque desta filial.');st.rerun()
                    except IntegrityError:
                        st.error('Código já existe nesta categoria e nesta filial.')
                    except Exception as ex:st.error(str(ex))
    with aba3:
        if not ativos:
            st.info('Cadastre ao menos um item ativo para registrar movimentações.')
        else:
            opts={f'{r["codigo"]} — {r["descricao"]} (saldo {r["estoque"]} {r["unidade"]})':r for r in ativos}
            with st.form(f'mov_form_{tipo}',clear_on_submit=True):
                selecao=st.selectbox('Item',list(opts),key=f'mov_sel_{tipo}')
                movs=['ENTRADA','SAÍDA','DEVOLUÇÃO','AJUSTE +','AJUSTE -']
                if tipo=='FERRAMENTA':movs=['ENTRADA','EMPRÉSTIMO','DEVOLUÇÃO','SAÍDA','AJUSTE +','AJUSTE -']
                if not administrador:movs=[m for m in movs if not m.startswith('AJUSTE')]
                c1,c2=st.columns(2)
                movimento=c1.selectbox('Movimento',movs,key=f'mov_tipo_{tipo}')
                qtd=c2.number_input('Quantidade *',min_value=0.001,step=1.0,format='%.3f',key=f'mov_qtd_{tipo}')
                responsavel=st.text_input('Responsável / Colaborador (obrigatório em empréstimos e devoluções)',key=f'mov_resp_{tipo}')
                documento=st.text_input('Requisição, O.S. ou referência',key=f'mov_doc_{tipo}')
                justificativa=st.text_area('Observações / motivo',key=f'mov_obs_{tipo}')
                if st.form_submit_button('💾 Registrar movimento',use_container_width=True):
                    try:
                        saldo=repo.movimentar_item(filial,opts[selecao]['id'],movimento,qtd,usuario,responsavel,documento,justificativa)
                        st.success(f'Movimento registrado. Novo saldo: {saldo} {opts[selecao]["unidade"]}.');st.rerun()
                    except (ValueError,IntegrityError) as ex:
                        st.error(str(ex))
    with aba4:
        movs=repo.listar_movimentacoes(filial,tipo)
        if not movs:
            st.info('Nenhuma movimentação registrada.')
        else:
            df=pd.DataFrame(movs)
            st.dataframe(df.rename(columns={'data':'Data','codigo':'Código','descricao':'Item','tipo':'Movimento',
                'quantidade':'Qtd.','saldo_apos':'Saldo após','responsavel':'Responsável','usuario':'Registrado por',
                'documento':'Documento','observacoes':'Observações'})[['Data','Código','Item','Movimento','Qtd.',
                'Saldo após','Responsável','Registrado por','Documento','Observações']],hide_index=True,use_container_width=True)
            st.download_button('📥 Exportar histórico CSV',df.to_csv(index=False).encode('utf-8-sig'),
                file_name=f'movimentacoes_{tipo.lower()}_{filial}.csv',mime='text/csv',key=f'expmov_{tipo}')
            if tipo=='FERRAMENTA':
                abertos=repo.emprestimos_pendentes(filial)
                st.markdown('#### Ferramentas emprestadas e não devolvidas')
                if abertos:st.dataframe(pd.DataFrame(abertos),hide_index=True,use_container_width=True)
                else:st.success('Sem empréstimos pendentes entre as movimentações consultadas.')

    with aba5:
        st.subheader('🗑️ Excluir itens do cadastro')
        st.caption('A exclusão retira o item do estoque ativo. Registros que tiveram movimentações permanecem no histórico para auditoria.')
        if not administrador:
            st.info('Somente administradores podem excluir ou reativar itens cadastrados.')
        elif not itens:
            st.info('Ainda não há cadastros nesta categoria.')
        else:
            disponiveis = [r for r in itens if int(r['ativo']) == 1]
            arquivados = [r for r in itens if int(r['ativo']) == 0]
            if disponiveis:
                opcoes = {f'{r["codigo"]} — {r["descricao"]} (saldo {r["estoque"]} {r["unidade"]})':r for r in disponiveis}
                escolhido = st.selectbox('Selecione o item a excluir', list(opcoes), key=f'item_excluir_{tipo}')
                item = opcoes[escolhido]
                st.warning('O item deixará de aparecer no estoque ativo e não poderá receber novas movimentações. Histórico anterior será mantido.')
                with st.form(f'confirmar_exclusao_{tipo}_{item["id"]}'):
                    confirmado = st.checkbox('Confirmo a exclusão deste cadastro', key=f'conf_excluir_{tipo}')
                    if st.form_submit_button('🗑️ Excluir item selecionado', type='primary'):
                        if not confirmado:
                            st.error('Marque a confirmação antes de excluir.')
                        else:
                            try:
                                repo.excluir_item_cadastro(filial, item['id'], tipo)
                                st.success('Item retirado do cadastro ativo, mantendo o histórico de movimentações.')
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
            else:
                st.info('Não existem itens ativos para excluir.')
            if arquivados:
                st.markdown('#### ♻️ Reativar item excluído')
                o2 = {f'{r["codigo"]} — {r["descricao"]}':r for r in arquivados}
                k2 = st.selectbox('Item excluído', list(o2), key=f'item_reativar_{tipo}')
                if st.button('♻️ Reativar cadastro', key=f'reativar_{tipo}'):
                    try:
                        repo.reativar_item_cadastro(filial, o2[k2]['id'], tipo)
                        st.success('Cadastro reativado.')
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
