"""Tela simplificada da Intranet Stang: SOMENTE exportação/importação de backup completo."""
from datetime import datetime
import hashlib
import streamlit as st

# Aparência 3D opcional: nenhuma regra de RH/estoque/backup depende dos ícones.
try:
    import menu_icones_3d as ui3d
except ImportError:
    class _Fallback3D:
        @staticmethod
        def titulo(texto, chave, nivel=1):
            st.markdown('#'*nivel + ' ' + texto)
        @staticmethod
        def aba(texto, chave):
            return texto
    ui3d = _Fallback3D()

import infra_persistencia as repo
import backup_unificado
from controle_acesso_filiais import validar_sessao

VERSAO = '2.0.5'


def renderizar(filial, usuario=None):
    usuario = usuario or st.session_state.get('usuario', '')
    try:
        _, _, _, acesso_global = validar_sessao(usuario, filial, repo)
    except PermissionError as exc:
        st.error(str(exc))
        return

    ui3d.titulo('Backup da Intranet Stang','filiais')
    st.caption('Um único arquivo criptografado para todos os usuários, filiais, compras, O.S., FMs, documentos, RH e ferramentaria.')
    if not repo.modo_producao():
        st.error('Banco externo não configurado. Não faça backups para produção em SQLite temporário.')
        return
    if not acesso_global:
        st.warning('O backup geral contém dados e credenciais de todas as filiais. Somente um administrador autorizado a TODAS as filiais pode exportá-lo ou restaurá-lo.')
        return
    st.info('Backups da versão anterior permanecem guardados onde você os salvou. A partir desta versão, use apenas um arquivo **.stangtotal** para as novas cópias completas.')
    aba_exportar, aba_importar = st.tabs([ui3d.aba('Exportar backup','filiais'), ui3d.aba('Importar backup','filiais')])

    with aba_exportar:
        ui3d.titulo('Exportar todos os dados','filiais',nivel=2)
        st.caption('Inclui as contas e permissões, hashes de senha de login, e-mails/tokens SMTP, assinaturas, todas as filiais e seus PDFs. Não envie este arquivo ao GitHub.')
        with st.form('form_backup_unificado_v205'):
            senha_admin = st.text_input('Sua senha atual de administrador', type='password')
            chave = st.text_input('Crie a senha de proteção do backup (mínimo 14 caracteres)', type='password')
            chave2 = st.text_input('Confirme a senha de proteção', type='password')
            confirmado = st.checkbox('Estou ciente de que o arquivo contém informações empresariais e credenciais protegidas.')
            gerar = st.form_submit_button('🔐 Preparar backup completo')
        if gerar:
            if not confirmado or chave != chave2:
                st.error('Marque a confirmação e confira as duas senhas de proteção.')
            else:
                try:
                    st.session_state['backup_total_2026'] = backup_unificado.gerar_backup(
                        usuario=usuario, senha_admin=senha_admin, senha_backup=chave
                    )
                    st.session_state['backup_total_usuario'] = usuario
                    st.success('Cópia completa gerada. Baixe e guarde a senha em outro local seguro.')
                except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                    st.error('Não foi possível gerar o backup: ' + str(exc))
        if st.session_state.get('backup_total_2026') and st.session_state.get('backup_total_usuario') == usuario:
            st.download_button('📥 Baixar backup completo (.stangtotal)',
                data=st.session_state['backup_total_2026'],
                file_name=f'IntranetStang_COMPLETO_{datetime.now():%Y%m%d_%H%M}.stangtotal',
                mime='application/octet-stream', use_container_width=True)
        st.caption('Para evitar divergências entre lançamentos feitos ao mesmo tempo, gere backups preferencialmente fora do horário de operação.')

    with aba_importar:
        ui3d.titulo('Importar backup completo','filiais',nivel=2)
        st.warning('A restauração substituirá os dados operacionais das filiais contidas no arquivo e poderá restaurar senhas e permissões anteriores. Faça uma cópia do estado ATUAL antes de confirmar.')
        arquivo = st.file_uploader('Arquivo completo .stangtotal', type=['stangtotal'], key='restore_total_upload_v205')
        senha_import = st.text_input('Senha usada para proteger este arquivo', type='password', key='restore_total_pass_v205')
        if st.button('🔎 Conferir integridade e conteúdo', key='restore_total_preview_btn_v205'):
            if not arquivo or not senha_import:
                st.error('Selecione o arquivo e informe a senha de proteção.')
            else:
                try:
                    resumo = backup_unificado.visualizar_backup(arquivo.getvalue(), senha_import)
                    st.session_state['restore_total_previa_v205'] = resumo
                    st.session_state['restore_total_hash_v205'] = hashlib.sha256(arquivo.getvalue()).hexdigest()
                except (ValueError, OSError, RuntimeError) as exc:
                    st.session_state.pop('restore_total_previa_v205', None)
                    st.error('Arquivo inválido ou senha incorreta: ' + str(exc))
        esperado = hashlib.sha256(arquivo.getvalue()).hexdigest() if arquivo else None
        previa = (st.session_state.get('restore_total_previa_v205') if esperado == st.session_state.get('restore_total_hash_v205') else None)
        if previa:
            c1, c2, c3 = st.columns(3)
            c1.metric('Filiais', previa['filiais'])
            c2.metric('Usuários', previa['usuarios'])
            c3.metric('Assinaturas', previa['assinaturas'])
            st.caption('Filiais: ' + ', '.join(previa['filiais_codigos']))
            st.caption('Gerado em (UTC): ' + str(previa['gerado_em']))
            st.divider()
            senha_admin_rest = st.text_input('Sua senha atual de administrador (autorizar restauração)', type='password', key='admin_rest_total_v205')
            if st.button('🛡️ Gerar cópia de segurança dos dados ATUAIS', key='gerar_pre_restore_total_v205'):
                try:
                    copia = backup_unificado.gerar_backup(
                        usuario=usuario, senha_admin=senha_admin_rest, senha_backup=senha_import
                    )
                    st.session_state['pre_restore_total_v205'] = copia
                    st.session_state['pre_restore_hash_v205'] = esperado
                    st.success('Baixe a cópia dos dados atuais ANTES de continuar.')
                except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                    st.error('Não foi possível gerar cópia atual: ' + str(exc))
            if st.session_state.get('pre_restore_hash_v205') == esperado and st.session_state.get('pre_restore_total_v205'):
                st.download_button('⬇️ Baixar backup ANTERIOR à restauração',
                    data=st.session_state['pre_restore_total_v205'],
                    file_name=f'ANTES_DA_RESTAURACAO_{datetime.now():%Y%m%d_%H%M}.stangtotal',
                    mime='application/octet-stream', use_container_width=True)
            aceite = st.checkbox('Já baixei o backup atual e aceito restaurar os dados e as contas incluídos no arquivo.', key='aceito_rest_total_v205')
            frase = st.text_input('Digite RESTAURAR para confirmar', key='frase_rest_total_v205')
            if st.button('♻️ Restaurar backup completo', type='primary', key='rest_total_btn_v205'):
                if not (aceite and frase == 'RESTAURAR' and senha_admin_rest and st.session_state.get('pre_restore_hash_v205') == esperado):
                    st.error('Faça a cópia de segurança atual, marque a confirmação e digite RESTAURAR.')
                else:
                    try:
                        resultado = backup_unificado.restaurar_backup(
                            arquivo.getvalue(), senha_import, usuario=usuario,
                            senha_admin=senha_admin_rest
                        )
                        st.success(f"Restauração concluída: {resultado['filiais']} filiais, "
                                   f"{resultado['usuarios']} usuários e {resultado['documentos']} documentos de filiais.")
                        for chave_sessao in ('backup_total_2026', 'pre_restore_total_v205', 'restore_total_previa_v205'):
                            st.session_state.pop(chave_sessao, None)
                        st.warning('Saia e entre novamente para revalidar senha e permissões restauradas.')
                    except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                        st.error('Restauração cancelada; confira os dados e os logs antes de repetir. Detalhe: ' + str(exc))
