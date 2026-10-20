"""Administração de filiais, backups completos e restauração intencional."""
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError
import infra_persistencia as repo

VERSAO = '2.0.2'


def renderizar(filial):
    st.markdown('# 🏢 Filiais, Atualizações e Backup')
    st.caption(f'Intranet Stang v{VERSAO} · Os bancos persistem fora do repositório GitHub')
    if repo.modo_producao():
        st.success('✅ Banco externo configurado. Atualizações de código não sobrescrevem os registros remotos.')
    else:
        st.warning('⚠️ MODO LOCAL: configure DATABASE_URL no Streamlit Cloud. SQLite local NÃO sobrevive a reinicializações do servidor.')
    tab_f,tab_b,tab_u,tab_i,tab_h,tab_v=st.tabs(['🏢 Filiais','💾 Backup ZIP','🔐 Contas e credenciais','📤 Importar resgate 2026','🕒 Versões anteriores','🚀 Atualizações'])
    with tab_f:
        lista=repo.listar_filiais()
        st.dataframe(pd.DataFrame(lista).rename(columns={'codigo':'Código','nome':'Nome da filial','ativo':'Ativo'}),
            hide_index=True,use_container_width=True)
        with st.form('cadastrar_filial'):
            st.markdown('#### Cadastrar nova filial')
            c1,c2=st.columns([1,2])
            codigo=c1.text_input('Código (ex.: JOINVILLE)')
            nome=c2.text_input('Nome / Localidade')
            if st.form_submit_button('Cadastrar filial'):
                try:
                    repo.cadastrar_filial(codigo,nome)
                    st.success('Filial cadastrada, com dados independentes. Vincule usuários a ela no gerenciamento de contas.');st.rerun()
                except (ValueError,IntegrityError) as ex:st.error(str(ex))
        st.info('Administradores podem alternar entre todas as filiais. Usuários comuns só têm acesso às filiais cadastradas em seu perfil.')
    with tab_b:
        st.markdown('#### Exportação de backup completo')
        st.write('O arquivo ZIP contém os CSVs, anexos e ferramentaria da filial. Contas de acesso e assinaturas são exportadas separadamente, criptografadas, na aba 🔐 Contas e credenciais.')
        incluir=False
        if st.button('Preparar backup para download',key='gerar_zip'):
            try:
                st.session_state['backup_zip'] = repo.backup_filial(filial,incluir)
                st.session_state['backup_filial'] = filial
                st.session_state['backup_usuarios'] = incluir
            except Exception as ex:st.error(str(ex))
        if st.session_state.get('backup_zip') and st.session_state.get('backup_filial')==filial and st.session_state.get('backup_usuarios')==incluir:
            st.download_button('📥 Baixar backup ZIP',st.session_state['backup_zip'],
                file_name=f'backup_intranet_{filial}_{datetime.now():%Y%m%d_%H%M}.zip',mime='application/zip')
        st.divider()
        st.markdown('#### Restauração controlada')
        arquivo=st.file_uploader('Selecionar ZIP gerado pela Intranet',type=['zip'],key='zip_restore')
        restaurar_users=False
        st.caption('Para recuperar usuários, senhas, e-mails, permissões e assinaturas, utilize a aba 🔐 Contas e credenciais.')
        confirmar=st.checkbox(f'Estou ciente de que a restauração substituirá os dados ATUAIS da filial {filial}',value=False)
        if st.button('♻️ Restaurar backup',disabled=not(arquivo and confirmar),type='primary'):
            try:
                n_bancos,n_docs=repo.restaurar_filial(arquivo.getvalue(),filial,restaurar_users)
                st.success(f'Restauração concluída: {n_bancos} bancos e {n_docs} anexos. Atualize a tela.')
                st.session_state.pop('backup_zip',None)
                st.rerun()
            except Exception as ex:st.error(f'Restauração não realizada: {ex}')
        st.caption('Snapshots automáticos das últimas 20 alterações de cada CSV ficam no banco externo. Guarde também backups ZIP em local seguro fora do servidor.')
    with tab_u:
        import backup_contas
        st.markdown('### 🔐 Backup completo de usuários e acessos')
        st.info('Inclui TODOS os usuários de TODAS as filiais: login, hash da senha atual, validade, perfil Admin, permissões, filiais autorizadas, e-mail, senha/token SMTP, servidor e porta SMTP, além das imagens PNG de assinatura. Nenhuma senha de login aparece em texto puro. O arquivo é criptografado.')
        st.caption('Usuários e seus documentos permanecem no PostgreSQL nas atualizações normais do GitHub. Este backup é uma proteção adicional para migrações, troca de banco ou desastres.')
        with st.form('form_export_contas'):
            senha_admin_exp = st.text_input('Sua senha de administrador (autorizar exportação)',type='password',key='confirma_admin_export')
            senha_zip_exp = st.text_input('Crie uma senha exclusiva para proteger o backup (mínimo 14 caracteres)',type='password',key='chave_export')
            senha_zip_exp2 = st.text_input('Repita a senha de proteção',type='password',key='chave_export2')
            gerar_contas = st.form_submit_button('🔒 Gerar backup de contas criptografado')
            if gerar_contas:
                if senha_zip_exp != senha_zip_exp2:
                    st.error('As senhas de proteção não conferem.')
                else:
                    try:
                        st.session_state['backup_contas_encrypted'] = backup_contas.criar_backup_contas(
                            senha_zip_exp,administrador=st.session_state.usuario,senha_admin=senha_admin_exp)
                        st.success('Backup criptografado gerado. Baixe e guarde a senha em local SEPARADO.')
                    except (ValueError, RuntimeError, OSError) as ex:
                        st.error(str(ex))
        if st.session_state.get('backup_contas_encrypted'):
            st.download_button('📥 Baixar backup de TODOS os usuários (.stangbak)',
                st.session_state['backup_contas_encrypted'],
                file_name=f'CONTAS_IntranetStang_{datetime.now():%Y%m%d_%H%M}.stangbak',
                mime='application/octet-stream')
        st.warning('Guarde o arquivo e a senha em cofres separados. Se perder a senha de proteção, o arquivo NÃO poderá ser recuperado. NÃO envie o backup ao GitHub, WhatsApp público ou serviços sem controle de acesso.')
        st.divider()
        st.markdown('### ♻️ Restaurar usuários e configurações')
        st.caption('A restauração mescla contas pelo nome de login: adiciona as que faltam e atualiza senhas (hash), permissões e SMTP das existentes. Não remove usuários extras nem altera compras ou O.S. Pode restaurar filiais ausentes. Se a senha do seu usuário for alterada pela restauração, faça login com a senha salva no backup.')
        up_contas = st.file_uploader('Selecione o arquivo de contas .stangbak',type=['stangbak'],key='import_contas_encrypted')
        senha_zip_imp = st.text_input('Senha que protege o backup .stangbak',type='password',key='chave_import')
        if up_contas is not None and senha_zip_imp:
            try:
                resumo = backup_contas.visualizar_backup_contas(up_contas.getvalue(),senha_zip_imp)
                c1,c2,c3,c4=st.columns(4)
                c1.metric('Usuários',resumo['usuarios'])
                c2.metric('Administradores',resumo['administradores'])
                c3.metric('Filiais',resumo['filiais'])
                c4.metric('Assinaturas PNG',resumo['assinaturas'])
                st.write('Contas no arquivo: ', ', '.join(resumo['nomes']))
                confirma_contas = st.checkbox('Confirmo que desejo atualizar os usuários existentes com os dados deste arquivo.',key='confirma_rest_contas')
                senha_admin_imp = st.text_input('Sua senha atual de administrador (autorizar restauração)',type='password',key='admin_confirma_import')
                if st.button('🔐 Restaurar contas do backup',disabled=not confirma_contas,type='primary'):
                    try:
                        r=backup_contas.restaurar_backup_contas(up_contas.getvalue(),senha_zip_imp,
                            administrador=st.session_state.usuario,senha_admin=senha_admin_imp)
                        st.session_state.pop('backup_contas_encrypted',None)
                        st.success(f"Contas recuperadas: {r['adicionados']} novas, {r['atualizados']} atualizadas; "
                                   f"{r['assinaturas']} assinaturas e {r['filiais']} filiais conferidas.")
                        st.warning('Faça logout e entre novamente. As senhas de login podem ter sido restauradas para os valores anteriores.')
                    except (ValueError, RuntimeError, OSError) as ex:
                        st.error(str(ex))
            except (ValueError, OSError, RuntimeError) as ex:
                st.error('Não foi possível validar o backup: '+str(ex))
        st.divider()
        st.markdown('### 📩 Importar complemento de perfis e assinatura')
        st.caption('Importa apenas os e-mails de kkethilyn e Bruno Jerri e a assinatura PNG de milton. NÃO altera senhas de login, validade, permissões, administrador ou filiais. Os tokens SMTP são opcionais e digitados diretamente nesta tela.')
        import importar_perfis_parciais as perfil_parcial
        patch = st.file_uploader('Complemento criptografado .stangperfil',type=['stangperfil'],key='patch_perfis_v202')
        senha_patch = st.text_input('Senha de abertura do complemento',type='password',key='pwd_patch_perfis')
        if patch is not None and senha_patch:
            try:
                previa = perfil_parcial.visualizar(patch.getvalue(),senha_patch)
                st.dataframe(pd.DataFrame(previa['emails']),hide_index=True,use_container_width=True)
                st.info('Assinatura PNG vinculada a: milton')
                with st.form('form_aplicar_perfis_v202'):
                    token_k = st.text_input('Token SMTP novo de kkethilyn (opcional; vazio mantém o atual)',type='password')
                    token_b = st.text_input('Token SMTP novo de Bruno Jerri (opcional; vazio mantém o atual)',type='password')
                    atual_admin = st.text_input('Senha atual do administrador (obrigatória)',type='password')
                    ok_patch = st.checkbox('Confirmei que os três usuários existem no cadastro e desejo atualizar somente estes dados.')
                    submit_patch = st.form_submit_button('Aplicar complemento aos perfis',disabled=not ok_patch)
                    if submit_patch:
                        try:
                            resultado=perfil_parcial.aplicar(patch.getvalue(),senha_patch,administrador=st.session_state.usuario,
                                senha_admin=atual_admin,token_kkethilyn=token_k,token_bruno=token_b)
                            st.success('Complemento aplicado: dois e-mails e uma assinatura vinculados. Senhas de login, permissões e filiais preservadas. Gere agora o backup de contas completo nesta mesma aba.')
                        except (ValueError,RuntimeError,OSError) as ex:
                            st.error('Importação não realizada: '+str(ex))
            except (ValueError,RuntimeError,OSError) as ex:
                st.error('Não foi possível abrir o complemento: '+str(ex))
    with tab_i:
        import mod_resgate_2026 as resgate
        st.markdown('#### Importar registros recuperados da Intranet antiga')
        st.info('Importação de acréscimo e conciliação: não exclui ordens, compras, FMs, anexos ou contas existentes. Não altera senhas e permissões.')
        st.warning('Use uma janela sem novos lançamentos e faça download do backup atual antes de confirmar. Não envie documentos empresariais para o GitHub público.')
        if not repo.modo_producao():
            st.error('Banco externo não configurado. A importação feita no SQLite local poderá desaparecer ao reiniciar o Streamlit Cloud.')
        up = st.file_uploader('Backup consolidado IntranetStang_Backup_Consolidado_19-10-2026.zip',type=['zip'],key='upload_resgate_2026')
        if up:
            conteudo = up.getvalue()
            try:
                preview = resgate.planejar_resgate(conteudo, filial)
                c1,c2,c3,c4=st.columns(4)
                c1.metric('Compras a adicionar', preview['compras_adicionadas'])
                c2.metric('O.S. a adicionar',preview['os_adicionadas'])
                c3.metric('FMs a adicionar',preview['fms_adicionados'])
                c4.metric('Anexos a armazenar', preview['anexos_novos'])
                st.write(f"Atualizações de FMs (somente se a data do backup for mais recente): **{preview['fms_renovados_mais_recentes']}**")
                st.write(f"Vínculos de documentos a completar: **{preview['documentos_vinculados']}**")
                if preview['pedidos_com_anexos_sem_registro_no_csv']:
                    st.warning('Os anexos dos pedidos '+ ', '.join(preview['pedidos_com_anexos_sem_registro_no_csv'])+
                               ' foram recuperados, mas os respectivos lançamentos não constam dos CSVs enviados. Não serão inventados cadastros.')
                if not st.session_state.get('resgate_backup_previa') or st.session_state.get('resgate_previa_filial')!=filial:
                    if st.button('💾 Gerar backup ANTES da importação',key='backup_antes_resgate'):
                        st.session_state['resgate_backup_previa']=repo.backup_filial(filial,incluir_usuarios=False)
                        st.session_state['resgate_previa_filial']=filial
                        st.rerun()
                if st.session_state.get('resgate_backup_previa') and st.session_state.get('resgate_previa_filial')==filial:
                    st.download_button('📥 Baixar cópia dos dados atuais da filial',
                        data=st.session_state['resgate_backup_previa'],file_name=f'ANTES_resgate_{filial}.zip',mime='application/zip')
                confirmado = st.checkbox(f'Confirmo o resgate para a filial {filial}; salvei uma cópia do estado anterior.', key='confirm_resgate_2026')
                if st.button('✅ Importar dados recuperados', key='execute_resgate_2026',type='primary',
                             disabled=not(confirmado and st.session_state.get('resgate_backup_previa') and
                                          st.session_state.get('resgate_previa_filial')==filial)):
                    resultado=resgate.importar_resgate(conteudo,filial)
                    for campo,rotulo in [('compras_adicionadas','Itens de compras'),('os_adicionadas','O.S.'),
                                         ('fms_adicionados','FMs'),('anexos_novos','Anexos')]:
                        st.write(f"{rotulo}: {resultado[campo]} adicionados")
                    st.success('Conciliação concluída e gravada no banco. Confira os registros e faça um novo backup.')
                    st.session_state['resgate_backup_previa']=None
            except (ValueError, OSError, RuntimeError) as ex:
                st.error(f'Não foi possível processar o resgate: {ex}')
        st.caption('A importação pode ser repetida; registros e anexos já reconhecidos não são duplicados. A exportação visual de usuários não é usada.')
    with tab_h:
        itens=repo.versoes_recentes(filial)
        if itens:st.dataframe(pd.DataFrame(itens).rename(columns={'arquivo':'Banco','versao':'Versão anterior','registrado_em':'Data'}),use_container_width=True,hide_index=True)
        else:st.info('O histórico de versões aparecerá após as primeiras alterações.')
    with tab_v:
        st.markdown('**Como atualizar sem perder os dados**')
        st.write('1. Configure DATABASE_URL uma única vez em Streamlit → Settings → Secrets.\n\n'
                 '2. Faça um backup ZIP da filial e um backup criptografado das contas. Guarde os dois fora do servidor.\n\n'
                 '3. Atualize o código no GitHub. O Streamlit implantará a versão nova; registros permanecem no banco externo.\n\n'
                 '4. Reabra a aplicação e confira os registros e anexos.\n\n'
                 '5. Não sobrescreva o banco com arquivos CSV do GitHub. Os CSVs do ZIP original são usados apenas para migração inicial.')
