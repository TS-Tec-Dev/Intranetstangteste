"""Migração de perfis sem exigir troca de senhas de login.

Contas que já existem no Supabase preservam seus hashes. Para criar contas
ausentes, lê, se fornecido pelo administrador, o CSV privado da intranet
anterior (também dentro do ZIP original) ou recebe a senha já utilizada.
Nunca publica ou escreve senhas em texto puro em arquivos de exportação.
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
from seguranca_senhas import PREFIXO, gerar_hash

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



def _ler_senhas_da_intranet_antiga(arquivo, nome_arquivo):
    """Lê apenas banco_usuarios.csv de um CSV privado ou ZIP original.

    Não armazena o conteúdo em disco nem expõe as senhas na interface.
    O ZIP pode conter outros arquivos: somente banco_usuarios.csv é lido.
    """
    if not arquivo:
        return {}
    if not isinstance(arquivo, bytes):
        raise ValueError('Arquivo antigo inválido.')
    nome = str(nome_arquivo or '').lower()
    if nome.endswith('.zip'):
        if len(arquivo) > 30_000_000:
            raise ValueError('ZIP original acima do tamanho aceito (30 MB).')
        try:
            with zipfile.ZipFile(io.BytesIO(arquivo)) as z:
                possibilidades = [i for i in z.infolist()
                                  if Path(i.filename).name == 'banco_usuarios.csv' and not i.is_dir()]
                if len(possibilidades) != 1 or possibilidades[0].file_size > MAX_CSV:
                    raise ValueError('ZIP deve conter um único banco_usuarios.csv válido.')
                data = z.read(possibilidades[0])
        except zipfile.BadZipFile as ex:
            raise ValueError('O ZIP antigo não pôde ser aberto.') from ex
    elif nome.endswith('.csv'):
        data = arquivo
    else:
        raise ValueError('Selecione o banco_usuarios.csv antigo ou o ZIP original da Intranet.')
    if not data or len(data) > MAX_CSV:
        raise ValueError('O banco_usuarios.csv antigo está vazio ou acima de 150 KB.')
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as ex:
        raise ValueError('Não foi possível interpretar o banco_usuarios.csv antigo.') from ex
    if not {'Usuario','Senha'}.issubset(df.columns):
        raise ValueError('O CSV antigo não contém as colunas Usuario e Senha.')
    if len(df) > 10000:
        raise ValueError('O arquivo de usuários contém registros demais.')
    mapping = {}
    for _, row in df.iterrows():
        login = str(row['Usuario']).strip().casefold()
        valor = str(row['Senha'])
        if not login or login in mapping or not valor:
            raise ValueError('Há usuários duplicados, vazios ou sem senha no CSV antigo.')
        mapping[login] = valor
    return mapping


def _armazenar_senha_igual(senha_atual):
    """Grava hash verificável usando a senha atual, sem impor comprimento."""
    if not isinstance(senha_atual, str) or not senha_atual:
        raise ValueError('Informe a senha que este usuário já utilizava (não pode estar vazia).')
    if senha_atual.startswith(PREFIXO):
        # Reutiliza hashes PBKDF2 válidos sem gerar outro hash incompatível.
        try:
            _, n, sal, digest = senha_atual.split('$', 3)
            valido = (100_000 <= int(n) <= 2_000_000 and
                      len(bytes.fromhex(sal)) == 16 and len(bytes.fromhex(digest)) == 32)
        except (ValueError, TypeError):
            valido = False
        if not valido:
            raise ValueError('Hash da senha antiga inválido.')
        return senha_atual
    return gerar_hash(senha_atual)

def consultar_contas():
    """Lê o cadastro atual (sem mostrar senhas, tokens ou hashes na tela)."""
    import pandas as pd
    data=repo.read_bytes('banco_usuarios.csv')
    df=pd.read_csv(io.BytesIO(data),dtype=str,keep_default_na=False)
    return df


def aplicar(content, *, administrador, senha_admin, senhas_novas, permissoes_jorge,
           atualizar_existentes=False, token_kkethilyn='', token_bruno='',
           arquivo_usuarios_antigo=None, nome_arquivo_usuarios_antigo=''):
    """Aplica importação validada, atômica no PostgreSQL e idempotente.

    Logins já existentes preservam SEMPRE Senha (hash) e Assinatura_PNG, exceto
    assinatura fornecida para milton. Outros metadados são atualizados SOMENTE
    após confirmação específica. Usuários ausentes usam suas senhas anteriores.
    """
    _validar_admin_atual(administrador, senha_admin)
    profiles, png = validar_zip(content)
    senhas_antigas = _ler_senhas_da_intranet_antiga(
        arquivo_usuarios_antigo, nome_arquivo_usuarios_antigo)
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
                # Preferência por senha digitada nesta migração para corrigir
                # um eventual CSV legado desatualizado. Nunca muda contas já existentes.
                pwd = senhas_novas.get(key) or senhas_antigas.get(key, '')
                if not pwd:
                    raise ValueError('Informe a senha já utilizada por '+login+
                                     ' ou envie o CSV original que a contenha.')
                data={'Usuario':login,'Senha':_armazenar_senha_igual(pwd),'Assinatura_PNG':'None',
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
    st.info('As senhas de quem já existe no Supabase serão mantidas. Para logins ausentes, use o CSV antigo com as senhas originais ou informe a senha que já utilizavam. Não há mínimo nem máximo de caracteres para senhas de login, mas o campo não pode ficar vazio.')
    st.warning('Antes de importar, gere e baixe um backup .stangbak em 🔐 Contas e credenciais. O ZIP de perfis não contém senhas. Se enviar o CSV antigo, ele é usado apenas nesta operação e as senhas são gravadas como hash.')
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
        st.markdown('#### Recuperar as senhas já utilizadas (opcional)')
        st.caption('Selecione **banco_usuarios.csv** da Intranet anterior ou o ZIP original **IntranetStang-main.zip**. Nunca envie esse arquivo ao GitHub: a versão antiga pode conter senhas em texto puro.')
        origem_legada=st.file_uploader('Banco de usuários antigo / ZIP original (privado)',type=['csv','zip'],key='migrar_banco_usuarios_antigo_v202')
        dados_legados = origem_legada.getvalue() if origem_legada is not None else None
        nome_legado = origem_legada.name if origem_legada is not None else ''
        try:
            old_passwords = _ler_senhas_da_intranet_antiga(dados_legados,nome_legado)
        except ValueError as ex:
            st.error('Arquivo antigo não utilizado: '+str(ex))
            return
        encontrados=[r['Usuario'] for r in profiles if r['Usuario'].strip().casefold() not in exist and r['Usuario'].strip().casefold() in old_passwords]
        faltando=[r['Usuario'] for r in profiles if r['Usuario'].strip().casefold() not in exist and r['Usuario'].strip().casefold() not in old_passwords]
        if origem_legada is not None:
            st.success(f'Senhas originais encontradas para {len(encontrados)} usuário(s) que ainda não existem no Supabase. Nenhuma senha será exibida.')
        if faltando:
            st.info('Para os logins que não existem no Supabase nem no CSV antigo, informe a senha que cada um já usava: '+', '.join(faltando)+'.')
        with st.form('form_migrar_contas_anterior_v202'):
            st.markdown('**Permissões de Jorge** — não há imagem desse usuário. Selecione os acessos exatos.')
            jorge=st.multiselect('Menus permitidos para jorge',options=list(VALID_MENUS))
            st.markdown('**Senhas originais** — somente quando o usuário está ausente do Supabase e do arquivo antigo')
            pass_dict={}
            for p in profiles:
                name=p['Usuario']
                if name.casefold() not in exist and name.casefold() not in old_passwords:
                    pass_dict[name.casefold()]=st.text_input('Senha atual de '+name+' (sem limite de caracteres)',type='password',key='senha_migrar_'+name.casefold().replace(' ','_'))
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
                            atualizar_existentes=sync,token_kkethilyn=token_k,token_bruno=token_b,
                            arquivo_usuarios_antigo=dados_legados,nome_arquivo_usuarios_antigo=nome_legado)
                        st.success(f"Importação concluída: {result['criados']} novos, {result['perfis_atualizados']} perfis sincronizados, {result['perfis_preservados']} preservados. Assinatura do Milton: {'gravada' if result['assinatura_milton'] else 'não vinculada'}.")
                        st.info('Atualize a página e confira os usuários. Gere novo backup completo .stangbak. Para contas existentes nenhuma senha de login foi alterada.')
                    except (ValueError, RuntimeError, OSError) as ex:
                        st.error('Migração não realizada: '+str(ex))
    except (ValueError, RuntimeError, OSError) as ex:
        st.error('Não foi possível ler o plano: '+str(ex))
