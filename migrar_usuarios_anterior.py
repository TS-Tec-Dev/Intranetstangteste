"""Migração controlada dos perfis da Intranet antiga para a Intranet 2.0.2.

Importa SOMENTE metadados e uma assinatura PNG fornecida em arquivo separado
(com senha de login definida na interface no momento da criação). Não coloca
senhas de login, tokens SMTP ou chaves dentro do GitHub/ZIP de migração.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

from PIL import Image
from sqlalchemy import select, insert, update

import infra_persistencia as repo
from backup_contas import _validar_admin_atual
from seguranca_senhas import gerar_hash

COLUMNS = ('Usuario', 'Validade', 'Admin', 'Permissoes', 'Email_Usuario',
           'Servidor_SMTP', 'Porta_SMTP', 'Filiais', 'Assinatura_PNG')
MAX_ZIP = 3_000_000
MAX_CSV = 150_000
MAX_PNG = 2_000_000
VALID_MENUS = (
    '📝 Nova O.S.', '📋 Gerenciar O.S.', '🖨️ Imprimir O.S.',
    '📅 Formulários e Prazos (FMs)', '🛒 Solicitações de Compras',
    '✍️ Painel de Assinaturas', '📊 Dashboard', '🧰 Ferramentaria',
    '🏢 Filiais e Backup'
)


def validar_zip(content):
    if len(content) > MAX_ZIP:
        raise ValueError('Arquivo excede 3 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            entries = z.infolist()
            expected = {'perfis_usuarios.csv', 'assinatura_milton.png'}
            if len(entries) != 2 or {e.filename for e in entries} != expected or any(e.is_dir() for e in entries):
                raise ValueError('Arquivo incompatível. Use o ZIP privado de migração de usuários.')
            if z.testzip() is not None or sum(e.file_size for e in entries) > (MAX_CSV+MAX_PNG):
                raise ValueError('Arquivo de migração danificado ou excessivamente grande.')
            csv_data = z.read('perfis_usuarios.csv')
            png_data = z.read('assinatura_milton.png')
    except (zipfile.BadZipFile, RuntimeError) as ex:
        raise ValueError('ZIP de migração inválido.') from ex
    if len(csv_data)>MAX_CSV or len(png_data)>MAX_PNG:
        raise ValueError('Arquivo interno acima do limite.')
    if not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('A assinatura deve ser um PNG.')
    try:
        with Image.open(io.BytesIO(png_data)) as im:
            if im.format != 'PNG' or im.width > 8000 or im.height > 8000:
                raise ValueError('Dimensões ou tipo de assinatura inválidos.')
            im.verify()
    except (OSError, SyntaxError) as ex:
        raise ValueError('Imagem de assinatura inválida.') from ex
    try:
        table = csv.DictReader(io.StringIO(csv_data.decode('utf-8-sig')), delimiter=';')
        if list(table.fieldnames or ()) != list(COLUMNS):
            raise ValueError('Colunas do plano de migração incompatíveis.')
        profiles = list(table)
    except (UnicodeError, csv.Error) as ex:
        raise ValueError('CSV de perfis inválido.') from ex
    if not profiles or len(profiles) > 100:
        raise ValueError('Número inválido de contas no arquivo.')
    names=set()
    for row in profiles:
        login = row['Usuario'].strip()
        key = login.casefold()
        if not login or len(login) > 120 or key in names or any(ch in login for ch in '\r\n\x00'):
            raise ValueError('Login vazio, inválido ou duplicado no arquivo.')
        names.add(key)
        if row['Admin'] not in ('Sim','Não'):
            raise ValueError('Perfil de administrador inválido para ' + login)
        if row['Validade'] != 'Vitalício' and not re.fullmatch(r'20\d\d-\d\d-\d\d', row['Validade']):
            raise ValueError('Validade inválida para ' + login)
        if row['Email_Usuario'] and ('@' not in row['Email_Usuario'] or len(row['Email_Usuario']) > 254):
            raise ValueError('E-mail inválido para ' + login)
        if row['Porta_SMTP'] and (not row['Porta_SMTP'].isdigit() or not 1 <= int(row['Porta_SMTP']) <= 65535):
            raise ValueError('Porta SMTP inválida para ' + login)
        if row['Servidor_SMTP'] and len(row['Servidor_SMTP']) > 120:
            raise ValueError('Servidor SMTP inválido para ' + login)
        if row['Assinatura_PNG'] not in ('', 'milton'):
            raise ValueError('Referência inesperada à assinatura PNG.')
        if row['Assinatura_PNG']=='milton' and key!='milton':
            raise ValueError('Assinatura PNG atribuída ao usuário incorreto.')
        if not row['Filiais'] or any(not re.fullmatch(r'[A-Z0-9_-]{1,40}', item) for item in row['Filiais'].split(',')):
            raise ValueError('Código de filial inválido para ' + login)
        menus = [x.strip() for x in row['Permissoes'].split('|') if x.strip()]
        if len(menus)!=len(set(menus)) or any(x not in VALID_MENUS for x in menus):
            raise ValueError('Permissões desconhecidas para ' + login)
    return profiles, png_data


def consultar_contas():
    """Lê o cadastro atual (sem mostrar senhas, tokens ou hashes na tela)."""
    import pandas as pd
    data=repo.read_bytes('banco_usuarios.csv')
    df=pd.read_csv(io.BytesIO(data),dtype=str,keep_default_na=False)
    return df


def aplicar(content, *, administrador, senha_admin, senhas_novas, permissoes_jorge,
           atualizar_existentes=False, token_kkethilyn='', token_bruno=''):
    """Aplica importação validada, atômica no PostgreSQL e idempotente.

    Logins já existentes preservam SEMPRE Senha (hash) e Assinatura_PNG, exceto
    assinatura fornecida para milton. Outros metadados são atualizados SOMENTE
    após confirmação específica. Usuários ausentes exigem senha nova forte.
    """
    _validar_admin_atual(administrador, senha_admin)
    profiles, png = validar_zip(content)
    import pandas as pd
    valid_filiais={str(r['codigo']) for r in repo.listar_filiais()}
    for row in profiles:
        if not set(row['Filiais'].split(',')).issubset(valid_filiais):
            raise ValueError('Cadastre as filiais necessárias antes de importar '+row['Usuario'])
    if not permissoes_jorge or any(m not in VALID_MENUS for m in permissoes_jorge):
        raise ValueError('Selecione as permissões do Jorge; elas não constam da captura enviada.')
    with repo.engine().begin() as conn:
        current = conn.execute(select(repo.bancos_tb.c.conteudo, repo.bancos_tb.c.versao)
            .where((repo.bancos_tb.c.filial==repo.GLOBAL)&
                   (repo.bancos_tb.c.arquivo=='banco_usuarios.csv')).with_for_update()).first()
        if current is None:
            raise ValueError('O banco de usuários ainda não está inicializado.')
        users = pd.read_csv(io.BytesIO(bytes(current.conteudo)),dtype=str,keep_default_na=False)
        for col in repo.TEMPLATES['banco_usuarios.csv']:
            if col not in users.columns:users[col]=''
        index={str(v).strip().casefold():i for i,v in enumerate(users['Usuario'])}
        novas, atualizadas, preservadas=0,0,0
        for row in profiles:
            login=row['Usuario'].strip()
            key=login.casefold()
            perms=[x.strip() for x in row['Permissoes'].split('|') if x.strip()]
            if key=='jorge':perms=list(permissoes_jorge)
            if not perms:
                raise ValueError('É necessário selecionar ao menos uma permissão para '+login)
            updated = {
                'Validade':row['Validade'], 'Admin':row['Admin'],
                'Permissoes':','.join(perms), 'Filiais':row['Filiais'],
                'Email_Usuario':row['Email_Usuario'],
                'Servidor_SMTP':row['Servidor_SMTP'] or 'smtp.gmail.com',
                'Porta_SMTP':row['Porta_SMTP'] or '587',
            }
            token = token_kkethilyn if key=='kkethilyn' else token_bruno if key=='bruno jerri' else ''
            if key in index:
                if atualizar_existentes:
                    for name,value in updated.items(): users.at[index[key],name]=value
                    if token:users.at[index[key],'Senha_App_Email']=token.strip()
                    atualizadas+=1
                else:
                    # Somente o token SMTP explicitamente indicado pode ser atualizado sem sincronizar o perfil.
                    if token: users.at[index[key],'Senha_App_Email']=token.strip()
                    preservadas+=1
            else:
                pwd = senhas_novas.get(key,'')
                if not isinstance(pwd,str) or len(pwd)<12:
                    raise ValueError('Defina uma nova senha forte (mínimo 12 caracteres) para '+login)
                data={'Usuario':login,'Senha':gerar_hash(pwd),'Assinatura_PNG':'None',
                      'Senha_App_Email':token.strip()}
                data.update(updated)
                users=pd.concat([users,pd.DataFrame([data])],ignore_index=True)
                index[key]=len(users)-1
                novas+=1
        # Se Milton já existia, atualiza apenas a assinatura, mesmo se não sincronizar outros campos.
        milton_idx=index.get('milton')
        assinatura=repo.caminho_documento('assinatura_milton.png',assinatura=True)
        if milton_idx is not None:
            users.at[milton_idx,'Assinatura_PNG']=assinatura
            filtro=(repo.docs_tb.c.filial==repo.GLOBAL)&(repo.docs_tb.c.caminho==assinatura)
            existe=conn.execute(select(repo.docs_tb.c.caminho).where(filtro)).first()
            if existe:
                conn.execute(update(repo.docs_tb).where(filtro).values(conteudo=png,alterado_em=repo._now()))
            else:
                conn.execute(insert(repo.docs_tb).values(filial=repo.GLOBAL,caminho=assinatura,
                    conteudo=png,alterado_em=repo._now()))
        if not (users['Admin']=='Sim').any():
            raise ValueError('A migração precisa preservar um administrador.')
        version=repo._salvar_bytes_conn(conn,repo.GLOBAL,'banco_usuarios.csv',
                users.to_csv(index=False).encode('utf-8-sig'),esperado=int(current.versao))
    repo._revisions()[repo._revision_key(repo.GLOBAL,'banco_usuarios.csv')]=version
    try:
        path=Path(assinatura)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
    except OSError:
        pass
    return {'criados':novas,'perfis_atualizados':atualizadas,'perfis_preservados':preservadas,
            'assinatura_milton':milton_idx is not None}


def renderizar():
    import streamlit as st
    st.markdown('### 👥 Migrar usuários da Intranet anterior')
    st.info('Esta importação preserva o login e hash das senhas de usuários existentes. Para contas ausentes, defina novas senhas aqui; elas são gravadas somente como hash no banco Supabase, nunca no GitHub.')
    st.warning('Antes de importar, gere e baixe um backup .stangbak em 🔐 Contas e credenciais. O ZIP de migração contém somente perfis e PNG (sem senhas nem tokens SMTP).')
    if not repo.modo_producao():
        st.error('Conexão externa DATABASE_URL não configurada. Não faça migração neste modo.')
        return
    uploaded=st.file_uploader('Importar plano de usuários (.zip)',type=['zip'],key='migrar_usuarios_v202_zip')
    if uploaded is None:
        return
    try:
        profiles, _ = validar_zip(uploaded.getvalue())
        df=consultar_contas()
        exist = {str(x).strip().casefold() for x in df['Usuario']}
        preview=[]
        for r in profiles:
            preview.append({'Usuário':r['Usuario'],'Situação':'Já existe' if r['Usuario'].casefold() in exist else 'Criar',
                            'Admin':r['Admin'],'Validade':r['Validade'],
                            'E-mail':r['Email_Usuario'],'Permissões': 'Conferir manualmente' if r['Usuario'].casefold()=='jorge' else r['Permissoes']})
        st.dataframe(preview,use_container_width=True,hide_index=True)
        expirados=[r['Usuario'] for r in profiles if r['Validade']!='Vitalício' and r['Validade']<'2026-10-20']
        if expirados:st.warning('Atenção: a validade do perfil ' + ', '.join(expirados)+ ' no CSV já venceu em 20/10/2026. O sistema respeitará a data antiga até você editá-la.')
        st.caption('Filial padrão indicada no arquivo: MANDALLA. Revise os vínculos por usuário posteriormente se houver outras bases.')
        with st.form('form_migrar_contas_anterior_v202'):
            st.markdown('**Permissões de Jorge** — não há imagem desse usuário. Selecione os acessos exatos.')
            jorge=st.multiselect('Menus permitidos para jorge',options=list(VALID_MENUS))
            st.markdown('**Senhas novas** — somente usuários ausentes no Supabase')
            pass_dict={}
            for p in profiles:
                name=p['Usuario']
                if name.casefold() not in exist:
                    pass_dict[name.casefold()]=st.text_input('Nova senha para '+name+' (mínimo 12 caracteres)',type='password',key='senha_migrar_'+name.casefold().replace(' ','_'))
            sync=st.checkbox('Atualizar também permissões, perfil de administrador, e-mails e validade dos usuários já cadastrados conforme o plano. Não alterar senhas existentes.',value=False)
            token_k=st.text_input('Novo token SMTP de kkethilyn (opcional)',type='password')
            token_b=st.text_input('Novo token SMTP de Bruno Jerri (opcional)',type='password')
            pwd_adm=st.text_input('Senha atual do administrador para autorizar',type='password')
            confirme=st.checkbox('Revisei os administradores, validade e menus e desejo cadastrar/atualizar estes perfis.')
            submitted=st.form_submit_button('Importar usuários e assinatura',type='primary')
            if submitted:
                if not confirme:st.error('Marque a confirmação antes de importar.')
                elif not pwd_adm:st.error('Informe a senha do administrador.')
                else:
                    try:
                        result=aplicar(uploaded.getvalue(),administrador=st.session_state.usuario,
                            senha_admin=pwd_adm,senhas_novas=pass_dict,permissoes_jorge=jorge,
                            atualizar_existentes=sync,token_kkethilyn=token_k,token_bruno=token_b)
                        st.success(f"Importação concluída: {result['criados']} novos, {result['perfis_atualizados']} perfis sincronizados, {result['perfis_preservados']} preservados. Assinatura do Milton: {'gravada' if result['assinatura_milton'] else 'não vinculada'}.")
                        st.info('Atualize a página e confira os usuários. Gere novo backup completo .stangbak. As novas senhas devem ser comunicadas aos respectivos usuários por canal seguro.')
                    except (ValueError, RuntimeError, OSError) as ex:
                        st.error('Migração não realizada: '+str(ex))
    except (ValueError, RuntimeError, OSError) as ex:
        st.error('Não foi possível ler o plano: '+str(ex))
