"""Gestão administrativa de RH da Intranet Stang.

Acesso administrativo, com escopo por filial. Não armazena resultados clínicos,
prontuários, diagnósticos, CPF ou documentos de identidade.
Horas e férias são apurações gerenciais; conferência com DP/PCMSO obrigatória.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

import infra_persistencia as repo

SMT_ANTECEDENCIA = 7
FIM_PROXIMO = 60
SITUACOES_FERIAS = ('Programadas', 'Gozadas', 'Canceladas')
TIPOS_ASO = ('Periódico', 'Admissional', 'Retorno ao trabalho', 'Mudança de riscos ocupacionais', 'Demissional', 'Outro')
STATUS_AGENDAMENTO = ('Não agendado', 'Agendado', 'Realizado', 'Cancelado')
TZ = ZoneInfo('America/Sao_Paulo')


def hoje() -> date:
    return datetime.now(TZ).date()


def dstr(v) -> str:
    return v.isoformat() if hasattr(v, 'isoformat') else (str(v or ''))


def aniversario(admissao: date, anos: int) -> date:
    try:
        return admissao.replace(year=admissao.year + anos)
    except ValueError:  # 29/fev
        return admissao.replace(year=admissao.year + anos, day=28)


def direito_ferias(faltas: int) -> int:
    """Art. 130 CLT; conta apenas faltas injustificadas, não ausências justificadas."""
    if faltas < 0: raise ValueError('Número de faltas não pode ser negativo')
    return 30 if faltas <= 5 else 24 if faltas <= 14 else 18 if faltas <= 23 else 12 if faltas <= 32 else 0


def dias_entre(inicio: date, fim: date) -> int:
    return (fim - inicio).days + 1


def _auditar(conn, filial, tabela, row_id, acao, usuario, antes=None):
    conn.execute(insert(repo.rh_auditoria_tb).values(
        filial=filial, tabela=tabela, registro_id=row_id, acao=acao, usuario=usuario,
        antes=json.dumps(antes, ensure_ascii=False, default=str) if antes is not None else None,
        data=repo._now()))


def colaboradores(filial, incluir_inativos=False):
    c = repo.rh_colaboradores_tb
    q = select(c).where(c.c.filial == filial)
    if not incluir_inativos: q = q.where(c.c.ativo == 1)
    with repo.engine().connect() as db:
        return [dict(x) for x in db.execute(q.order_by(c.c.nome)).mappings()]


def _col(conn, filial, colaborador_id):
    c=repo.rh_colaboradores_tb
    result=conn.execute(select(c).where(and_(c.c.filial==filial,c.c.id==colaborador_id))).mappings().first()
    if not result: raise ValueError('Colaborador inexistente nesta filial')
    return dict(result)


def cadastrar_colaborador(filial, matricula, nome, setor, cargo, admissao, monitorar_desde, jornada, usuario):
    if not matricula.strip() or not nome.strip(): raise ValueError('Matrícula e nome são obrigatórios')
    if admissao > hoje(): raise ValueError('Admissão futura não é válida como data efetiva')
    if monitorar_desde < admissao: raise ValueError('Início da apuração não pode ser anterior à admissão')
    if not 1 <= jornada <= 1440: raise ValueError('Jornada prevista deve estar entre 1 e 1440 minutos')
    with repo.engine().begin() as db:
        x=db.execute(insert(repo.rh_colaboradores_tb).values(
            filial=filial, matricula=matricula.strip(), nome=nome.strip(), setor=setor.strip(), cargo=cargo.strip(),
            admissao=admissao, monitorar_ferias_desde=monitorar_desde, jornada_diaria_min=jornada,
            ativo=1, observacoes='')).inserted_primary_key[0]
        _auditar(db,filial,'colaboradores',x,'CRIAR',usuario)
    return x


def editar_colaborador(filial, cid, values, usuario):
    campos={'nome','cargo','setor','jornada_diaria_min','monitorar_ferias_desde','ativo','observacoes'}
    if not set(values) <= campos: raise ValueError('Campo não permitido')
    c=repo.rh_colaboradores_tb
    with repo.engine().begin() as db:
        velho=_col(db,filial,cid)
        if 'monitorar_ferias_desde' in values and values['monitorar_ferias_desde'] < velho['admissao']:
            raise ValueError('Apuração não pode começar antes da admissão')
        if 'jornada_diaria_min' in values and not 1<=values['jornada_diaria_min']<=1440:
            raise ValueError('Jornada inválida')
        db.execute(update(c).where(and_(c.c.id==cid,c.c.filial==filial)).values(**values))
        _auditar(db,filial,'colaboradores',cid,'ALTERAR',usuario,velho)


def parse_hora(texto):
    try: return datetime.strptime(texto.strip(),'%H:%M').time()
    except ValueError: raise ValueError('Horário inválido; use HH:MM')


def calcular_minutos(entrada: str, saida: str, intervalo_min: int, previsto_min: int):
    a,b=parse_hora(entrada),parse_hora(saida)
    total=(b.hour*60+b.minute)-(a.hour*60+a.minute)
    if total <= 0: total += 1440  # turno noturno, cruzando meia-noite
    if intervalo_min<0 or intervalo_min>=total: raise ValueError('Intervalo deve ser menor que o tempo entre entrada e saída')
    if not 0<=previsto_min<=1440: raise ValueError('Jornada prevista inválida')
    trab=total-intervalo_min
    return trab,trab-previsto_min


def fmt_min(minutos):
    return f'{"+" if minutos>0 else "-" if minutos<0 else ""}{abs(minutos)//60:02d}:{abs(minutos)%60:02d}'


def lancar_horas(filial, cid, dia, entrada, saida, intervalo, previsto, observacao, usuario):
    trabalhado,saldo=calcular_minutos(entrada,saida,intervalo,previsto)
    t=repo.rh_horas_tb
    with repo.engine().begin() as db:
        _col(db,filial,cid)
        existente=db.execute(select(t).where(and_(t.c.filial==filial,t.c.colaborador_id==cid,t.c.dia==dia))).mappings().first()
        vals=dict(entrada=entrada,saida=saida,intervalo_min=intervalo,previsto_min=previsto,
            trabalhado_min=trabalhado,saldo_min=saldo,observacoes=observacao[:500],usuario=usuario,atualizado_em=repo._now())
        if existente:
            db.execute(update(t).where(t.c.id==existente['id']).values(**vals))
            _auditar(db,filial,'horas',existente['id'],'RETIFICAR',usuario,dict(existente))
        else:
            row_id=db.execute(insert(t).values(filial=filial,colaborador_id=cid,dia=dia,**vals)).inserted_primary_key[0]
            _auditar(db,filial,'horas',row_id,'CRIAR',usuario)
    return trabalhado,saldo


def horas(filial,cid=None):
    t=repo.rh_horas_tb
    q=select(t).where(t.c.filial==filial)
    if cid is not None:q=q.where(t.c.colaborador_id==cid)
    with repo.engine().connect() as db:
        return [dict(x) for x in db.execute(q.order_by(t.c.dia.desc())).mappings()]


def _ciclo_data(admissao, inicio):
    for anos in range(0,150):
        a=aniversario(admissao,anos)
        if a==inicio: return anos
        if a>inicio: break
    raise ValueError('Período aquisitivo deve começar no aniversário da admissão')


def _ciclo_valido(col,inicio):
    indice=_ciclo_data(col['admissao'],inicio)
    return aniversario(col['admissao'],indice+1)-timedelta(days=1),aniversario(col['admissao'],indice+2)-timedelta(days=1)


def _dados_ciclo(db,filial,cid,inicio):
    t=repo.rh_ciclos_tb
    return db.execute(select(t).where(and_(t.c.filial==filial,t.c.colaborador_id==cid,
        t.c.aquisitivo_inicio==inicio))).mappings().first()


def registrar_faltas(filial,cid,inicio,faltas,usuario):
    if faltas<0 or faltas>366: raise ValueError('Faltas fora da faixa permitida')
    c=repo.rh_ciclos_tb
    with repo.engine().begin() as db:
        col=_col(db,filial,cid)
        _ciclo_valido(col,inicio)
        old=_dados_ciclo(db,filial,cid,inicio)
        if old:
            db.execute(update(c).where(c.c.id==old['id']).values(faltas_injustificadas=faltas))
            rid=old['id']
        else:
            rid=db.execute(insert(c).values(filial=filial,colaborador_id=cid,aquisitivo_inicio=inicio,
                faltas_injustificadas=faltas,observacao='')).inserted_primary_key[0]
        _auditar(db,filial,'ciclos',rid,'ALTERAR_FALTAS',usuario,dict(old) if old else None)


def ciclos_ferias(filial,cid,referencia=None):
    referencia=referencia or hoje()
    f=repo.rh_ferias_tb;c=repo.rh_ciclos_tb
    with repo.engine().connect() as db:
        col=_col(db,filial,cid)
        ferias=[dict(x) for x in db.execute(select(f).where(and_(f.c.filial==filial,f.c.colaborador_id==cid))).mappings()]
        faltas={x['aquisitivo_inicio']:x['faltas_injustificadas'] for x in db.execute(select(c).where(and_(c.c.filial==filial,c.c.colaborador_id==cid))).mappings()}
    saida=[]
    for n in range(0,120):
        inicio=aniversario(col['admissao'],n)
        if inicio>referencia: break
        if inicio<col['monitorar_ferias_desde']: continue
        fim_a=aniversario(col['admissao'],n+1)-timedelta(days=1)
        prazo=aniversario(col['admissao'],n+2)-timedelta(days=1)
        if inicio>fim_a or inicio>prazo: continue
        p=[r for r in ferias if r['aquisitivo_inicio']==inicio and r['situacao']!='Canceladas']
        gozados=sum(r['dias'] for r in p if r['situacao']=='Gozadas')
        programados=sum(r['dias'] for r in p if r['situacao']=='Programadas')
        dias=direito_ferias(faltas.get(inicio,0))
        saldo=max(0,dias-gozados)
        if referencia<=fim_a:status='Em aquisição'
        elif saldo==0:status='Quitadas'
        elif referencia>prazo:status='Vencidas'
        elif (prazo-referencia).days<=FIM_PROXIMO:status='A vencer (60 dias)'
        else:status='No prazo'
        saida.append({'aquisitivo_inicio':inicio,'aquisitivo_fim':fim_a,
            'concessivo_fim':prazo,'faltas':faltas.get(inicio,0),'direito':dias,
            'gozados':gozados,'programados':programados,'saldo':saldo,
            'disponivel_para_programar':max(0,dias-gozados-programados),
            'parcelas':len(p),'status':status})
    return saida


def listar_ferias(filial,cid=None):
    f=repo.rh_ferias_tb
    q=select(f).where(f.c.filial==filial)
    if cid is not None:q=q.where(f.c.colaborador_id==cid)
    with repo.engine().connect() as db:
        return [dict(x) for x in db.execute(q.order_by(f.c.inicio.desc())).mappings()]


def programar_ferias(filial,cid,aquisitivo_inicio,inicio,dias,situacao,concordancia,observacoes,usuario):
    if situacao not in SITUACOES_FERIAS[:2]: raise ValueError('Situação inválida')
    if dias<5 or dias>30: raise ValueError('Parcela deve ter ao menos 5 dias corridos e no máximo 30')
    fim=inicio+timedelta(days=dias-1)
    if situacao == 'Gozadas' and fim > hoje():
        raise ValueError('Uma parcela futura deve ser Programada; marque como Gozada após o término')
    f=repo.rh_ferias_tb
    with repo.engine().begin() as db:
        col=_col(db,filial,cid)
        fim_a,prazo=_ciclo_valido(col,aquisitivo_inicio)
        if inicio <= fim_a:raise ValueError('Férias devem começar após a aquisição do direito (verifique regras especiais com DP)')
        # É permitido registrar férias antigas/fora de prazo com aviso na interface.
        existentes=[dict(r) for r in db.execute(select(f).where(and_(f.c.filial==filial,f.c.colaborador_id==cid,
           f.c.aquisitivo_inicio==aquisitivo_inicio,f.c.situacao!='Canceladas'))).mappings()]
        for reg in existentes:
            if inicio<=reg['fim'] and fim>=reg['inicio']:raise ValueError('Período sobreposto às férias já registradas')
        if len(existentes)>=3:raise ValueError('Fracionamento máximo: 3 períodos')
        if (existentes or dias<30) and not concordancia:raise ValueError('Confirme a concordância do empregado com o fracionamento')
        registro=_dados_ciclo(db,filial,cid,aquisitivo_inicio)
        direito=direito_ferias(registro['faltas_injustificadas'] if registro else 0)
        alocados=sum(x['dias'] for x in existentes)
        if alocados+dias>direito:raise ValueError(f'O total de parcelas excede o direito ({direito} dias)')
        parcelas=[x['dias'] for x in existentes]+[dias]
        if len(parcelas)==3 and max(parcelas)<14:
            raise ValueError('Fracionamento inválido: uma das parcelas deve ter ao menos 14 dias')
        if sum(parcelas)==direito and len(parcelas)>1 and max(parcelas)<14:
            raise ValueError('Fracionamento inválido: não existe parcela de 14 dias')
        # Feriado e DSR dependem do calendário local: não fazem parte desta validação.
        x=db.execute(insert(f).values(filial=filial,colaborador_id=cid,aquisitivo_inicio=aquisitivo_inicio,
            inicio=inicio,fim=fim,dias=dias,situacao=situacao,concordancia_fracionamento=int(concordancia),
            observacoes=observacoes[:500],usuario=usuario,criado_em=repo._now())).inserted_primary_key[0]
        _auditar(db,filial,'ferias',x,'REGISTRAR',usuario)
    return x, fim,prazo


def mudar_situacao_ferias(filial,rid,situacao,usuario):
    if situacao not in SITUACOES_FERIAS:raise ValueError('Situação inválida')
    f=repo.rh_ferias_tb
    with repo.engine().begin() as db:
        antigo=db.execute(select(f).where(and_(f.c.filial==filial,f.c.id==rid))).mappings().first()
        if not antigo:raise ValueError('Registro de férias não encontrado')
        db.execute(update(f).where(and_(f.c.filial==filial,f.c.id==rid)).values(situacao=situacao))
        _auditar(db,filial,'ferias',rid,'ALTERAR_STATUS',usuario,dict(antigo))


def status_aso(vencimento,agendado_para=None,situacao='Não agendado',referencia=None):
    referencia=referencia or hoje()
    dias=(vencimento-referencia).days
    if dias<0:return 'Vencido', dias
    if situacao=='Realizado':return 'Agendamento realizado',dias
    if situacao=='Agendado':return 'Agendado SMT',dias
    if dias<=SMT_ANTECEDENCIA:return 'Agendar SMT (até 7 dias)',dias
    return 'Em dia',dias


def listar_asos(filial,cid=None):
    a=repo.rh_asos_tb
    q=select(a).where(a.c.filial==filial)
    if cid is not None:q=q.where(a.c.colaborador_id==cid)
    with repo.engine().connect() as db:
        return [dict(x) for x in db.execute(q.order_by(a.c.vencimento)).mappings()]


def registrar_aso(filial,cid,tipo,realizado,vencimento,agenda,situacao,obs,usuario):
    if tipo not in TIPOS_ASO or situacao not in STATUS_AGENDAMENTO:raise ValueError('Seleção de ASO inválida')
    if realizado and vencimento<=realizado:raise ValueError('Vencimento deve ser posterior ao exame realizado')
    if situacao=='Agendado' and agenda is None:raise ValueError('Informe a data do agendamento na SMT')
    with repo.engine().begin() as db:
        _col(db,filial,cid)
        x=db.execute(insert(repo.rh_asos_tb).values(filial=filial,colaborador_id=cid,tipo=tipo,
            realizado_em=realizado,vencimento=vencimento,agendado_para=agenda,
            situacao_agendamento=situacao,observacoes=obs[:500],atualizado_em=repo._now())).inserted_primary_key[0]
        _auditar(db,filial,'asos',x,'CRIAR',usuario)
    return x


def editar_aso(filial,rid,agenda,situacao,usuario):
    if situacao not in STATUS_AGENDAMENTO:raise ValueError('Situação inválida')
    if situacao=='Agendado' and not agenda:raise ValueError('Informe a data de agendamento')
    a=repo.rh_asos_tb
    with repo.engine().begin() as db:
        antigo=db.execute(select(a).where(and_(a.c.filial==filial,a.c.id==rid))).mappings().first()
        if not antigo:raise ValueError('ASO inexistente na filial')
        db.execute(update(a).where(and_(a.c.filial==filial,a.c.id==rid)).values(
            agendado_para=agenda,situacao_agendamento=situacao,atualizado_em=repo._now()))
        _auditar(db,filial,'asos',rid,'AGENDAR',usuario,dict(antigo))


def _nomes(rows):
    return {r['id']:f'{r["matricula"]} — {r["nome"]}' for r in rows}


def _df_export(rows):
    out=[]
    for r in rows:
        out.append({k:(dstr(v) if isinstance(v,(date,datetime)) else v) for k,v in r.items() if k not in ('observacoes','usuario')})
    return pd.DataFrame(out)


def _baixar_csv(titulo,rows,nome,key):
    if rows:
        st.download_button(titulo,_df_export(rows).to_csv(index=False).encode('utf-8-sig'),
            nome,mime='text/csv',key=key)


def _selecionar_colaborador(rows,key,rotulo='Colaborador'):
    options=_nomes(rows)
    return st.selectbox(rotulo,list(options),format_func=lambda k:options[k],key=key)


def renderizar(filial,usuario,administrador):
    st.markdown('# 👥 Gestão de RH — Colaboradores, Horas, Férias e ASO')
    st.caption(f'Filial: {filial} · Informações administrativas reservadas · SMT: alerta 7 dias antes do vencimento')
    if not administrador:
        st.error('Acesso restrito a administradores. Solicite autorização ao responsável pelo RH.')
        return
    if not repo.modo_producao():
        st.error('Configure DATABASE_URL para armazenar dados de RH permanentemente. Não cadastre colaboradores no banco local.')
        return
    tabs=st.tabs(['📊 Painel RH','👤 Colaboradores','⏱️ Controle de Horas','🏖️ Férias','🩺 ASO e SMT'])
    rows=colaboradores(filial,True)
    ativos=[r for r in rows if r['ativo']]
    mapa={r['id']:r for r in rows}
    with tabs[0]:
        st.subheader('Vencimentos e prioridades')
        resumo=[]
        for r in ativos:
            ciclos=ciclos_ferias(filial,r['id'])
            for c in ciclos:
                if c['status'] in ('Vencidas','A vencer (60 dias)'):
                    resumo.append(dict(Colaborador=r['nome'],Matrícula=r['matricula'],Tipo='Férias',
                        Status=c['status'],Prazo=dstr(c['concessivo_fim']),Dias_pendentes=c['saldo']))
        aso_linhas=[]
        ultimos = {}
        for reg in listar_asos(filial):
            chave = (reg['colaborador_id'], reg['tipo'])
            if chave not in ultimos or (reg['vencimento'], reg['id']) > (ultimos[chave]['vencimento'], ultimos[chave]['id']):
                ultimos[chave] = reg
        for a in ultimos.values():
            if a['colaborador_id'] not in mapa or not mapa[a['colaborador_id']]['ativo']:continue
            sta,dias=status_aso(a['vencimento'],a['agendado_para'],a['situacao_agendamento'])
            aso_linhas.append({**a,'status':sta,'dias':dias})
            if sta in ('Vencido','Agendar SMT (até 7 dias)'):
                resumo.append(dict(Colaborador=mapa[a['colaborador_id']]['nome'],Matrícula=mapa[a['colaborador_id']]['matricula'],
                    Tipo=f'ASO {a["tipo"]}',Status=sta,Prazo=dstr(a['vencimento']),Dias_pendentes='—'))
        cols=st.columns(4)
        cols[0].metric('Colaboradores ativos',len(ativos))
        cols[1].metric('Férias vencidas',sum(v['Tipo']=='Férias' and v['Status']=='Vencidas' for v in resumo))
        cols[2].metric('ASOs vencidos',sum(v['status']=='Vencido' for v in aso_linhas))
        cols[3].metric('Agendar SMT',sum(v['status']=='Agendar SMT (até 7 dias)' for v in aso_linhas))
        if resumo:
            st.dataframe(pd.DataFrame(resumo),hide_index=True,use_container_width=True)
            _baixar_csv('📥 Exportar pendências CSV',resumo,f'rh_pendencias_{filial}.csv','rh_pend')
        else:st.success('Sem pendências detectadas nos ciclos/ASOs cadastrados.')
        st.info('A tabela de ASOs considera o registro de vencimento mais recente por colaborador e tipo. Ciclos históricos sem registro de férias podem aparecer vencidos. Atualize o marco inicial de apuração ou lance os períodos já gozados. A situação de ASO depende do vencimento definido pelo PCMSO.')
    with tabs[1]:
        st.subheader('Cadastro de colaboradores')
        with st.form('rh_novo_colaborador'):
            c1,c2=st.columns(2)
            mat=c1.text_input('Matrícula / Código *')
            nome=c2.text_input('Nome completo *')
            c3,c4=st.columns(2)
            setor=c3.text_input('Setor')
            cargo=c4.text_input('Cargo')
            a,b,c=st.columns(3)
            adm=a.date_input('Admissão *',value=hoje()-timedelta(days=365),max_value=hoje())
            inicio=b.date_input('Monitorar férias desde *',value=adm,
                help='Escolha o início do primeiro período que deve ser apurado. Ciclos anteriores só devem ser desconsiderados se já estiverem regularizados.')
            jornada=c.number_input('Jornada diária prevista (h)',min_value=0.25,max_value=24.0,value=8.0,step=0.25)
            if st.form_submit_button('➕ Cadastrar colaborador'):
                try:
                    cadastrar_colaborador(filial,mat,nome,setor,cargo,adm,inicio,int(jornada*60),usuario)
                    st.success('Colaborador cadastrado.');st.rerun()
                except (ValueError,IntegrityError) as ex:st.error(str(ex))
        if rows:
            st.dataframe(_df_export(rows),hide_index=True,use_container_width=True)
            _baixar_csv('📥 Exportar colaboradores CSV',rows,f'rh_colaboradores_{filial}.csv','rhcolcsv')
            with st.expander('✏️ Editar / inativar colaborador e marco inicial de férias'):
                cid=_selecionar_colaborador(rows,'rh_edit_col')
                col=mapa[cid]
                with st.form('rh_form_edit'):
                    n=st.text_input('Nome',value=col['nome']);car=st.text_input('Cargo',value=col['cargo']);setor2=st.text_input('Setor',value=col['setor'])
                    corte=st.date_input('Monitorar ciclos a partir de',value=col['monitorar_ferias_desde'])
                    jj=st.number_input('Jornada diária em minutos',min_value=1,max_value=1440,value=col['jornada_diaria_min'])
                    ativo=st.checkbox('Ativo',value=bool(col['ativo']))
                    if st.form_submit_button('💾 Salvar alterações'):
                        try:
                            editar_colaborador(filial,cid,dict(nome=n,cargo=car,setor=setor2,monitorar_ferias_desde=corte,
                                jornada_diaria_min=jj,ativo=int(ativo)),usuario)
                            st.success('Cadastro atualizado.');st.rerun()
                        except ValueError as ex:st.error(str(ex))
    with tabs[2]:
        st.subheader('Controle diário de horas')
        st.caption('Registro manual gerencial. Não substitui o sistema legal de controle de ponto nem calcula adicional noturno ou regras de convenções coletivas.')
        if not ativos:st.info('Cadastre colaboradores primeiro.')
        else:
            cid=_selecionar_colaborador(ativos,'rh_horas_cid')
            with st.form('rh_lancar_horas'):
                d=st.date_input('Dia de trabalho',value=hoje())
                c1,c2,c3,c4=st.columns(4)
                e=c1.text_input('Entrada HH:MM',value='08:00')
                s=c2.text_input('Saída HH:MM',value='17:00')
                intervalo=c3.number_input('Intervalo (min)',min_value=0,max_value=1439,value=60)
                esperado=c4.number_input('Previsto (min)',min_value=0,max_value=1440,value=mapa[cid]['jornada_diaria_min'])
                obs=st.text_input('Observações (opcional)')
                if st.form_submit_button('💾 Salvar / corrigir jornada do dia'):
                    try:
                        trab,saldo=lancar_horas(filial,cid,d,e,s,intervalo,esperado,obs,usuario)
                        st.success(f'Jornada {fmt_min(trab)} · Saldo {fmt_min(saldo)}. Se o dia já existia, foi retificado com registro de auditoria.')
                    except ValueError as ex:st.error(str(ex))
            registros=horas(filial,cid)
            if registros:
                total=sum(r['saldo_min'] for r in registros)
                st.metric('Saldo acumulado dos registros',fmt_min(total))
                st.dataframe(pd.DataFrame([dict(Data=dstr(r['dia']),Entrada=r['entrada'],Saída=r['saida'],Intervalo=r['intervalo_min'],
                    Previsto=fmt_min(r['previsto_min']),Trabalhado=fmt_min(r['trabalhado_min']),Saldo=fmt_min(r['saldo_min']),
                    Observações=r['observacoes']) for r in registros]),hide_index=True,use_container_width=True)
                _baixar_csv('📥 Exportar horas CSV',registros,f'rh_horas_{filial}_{cid}.csv','rhh_csv')
    with tabs[3]:
        st.subheader('Férias — períodos aquisitivos e concessivos')
        st.caption('CLT, arts. 130 e 134: duração conforme faltas injustificadas e até 3 parcelas, uma ≥14 dias e demais ≥5. Acordo coletivo e situações especiais devem ser verificados pelo DP.')
        if not ativos:st.info('Cadastre colaboradores primeiro.')
        else:
            cid=_selecionar_colaborador(ativos,'rh_ferias_cid')
            ciclos=ciclos_ferias(filial,cid)
            if ciclos:
                st.dataframe(pd.DataFrame([dict(Início=dstr(c['aquisitivo_inicio']),Fim_aquisitivo=dstr(c['aquisitivo_fim']),
                    Fim_concessivo=dstr(c['concessivo_fim']),Direito=c['direito'],Gozados=c['gozados'],
                    Programados=c['programados'],Saldo=c['saldo'],Parcelas=c['parcelas'],Status=c['status']) for c in reversed(ciclos)]),
                    hide_index=True,use_container_width=True)
                ativos_ciclos=[c for c in ciclos if c['aquisitivo_fim'] < hoje() and c['direito']>0]
                if ativos_ciclos:
                    inicio=st.selectbox('Período aquisitivo para gestão',list(reversed([c['aquisitivo_inicio'] for c in ativos_ciclos])),
                        format_func=lambda x:f'{x:%d/%m/%Y} até {next(c for c in ciclos if c["aquisitivo_inicio"]==x)["aquisitivo_fim"]:%d/%m/%Y}',key='rh_ciclo_escolhido')
                    atual=next(c for c in ciclos if c['aquisitivo_inicio']==inicio)
                    st.info(f'Direito: {atual["direito"]} dias | Gozados: {atual["gozados"]} | Reservados: {atual["programados"]} | Para programar: {atual["disponivel_para_programar"]} | Concessivo até: {atual["concessivo_fim"]:%d/%m/%Y}')
                    with st.expander('⚙️ Ajustar faltas injustificadas do ciclo (art. 130 CLT)'):
                        with st.form('rh_faltas'):
                            f=st.number_input('Faltas injustificadas no aquisitivo',min_value=0,max_value=366,value=atual['faltas'])
                            st.caption(f'Dias de direito calculados: {direito_ferias(f)}')
                            if st.form_submit_button('Salvar faltas'):
                                try:registrar_faltas(filial,cid,inicio,f,usuario);st.rerun()
                                except ValueError as ex:st.error(str(ex))
                    with st.form('rh_ferias_registro'):
                        st.markdown('#### Registrar parcela de férias')
                        c1,c2,c3=st.columns(3)
                        data_inicio=c1.date_input('Início do gozo',value=max(hoje(),atual['aquisitivo_fim']+timedelta(days=1)))
                        dias=c2.number_input('Dias corridos da parcela',min_value=5,max_value=30,value=min(14,max(5,atual['disponivel_para_programar'])))
                        estado=c3.selectbox('Situação',SITUACOES_FERIAS[:2])
                        concord=st.checkbox('Concordância do empregado com fracionamento, quando aplicável')
                        obs=st.text_input('Observações')
                        st.caption('Importante: validar início com feriados/DSR (vedados os 2 dias anteriores), aviso prévio de 30 dias e pagamento até 2 dias antes, conforme CLT.')
                        if st.form_submit_button('💾 Registrar parcela'):
                            try:
                                rid,fim,prazo=programar_ferias(filial,cid,inicio,data_inicio,dias,estado,concord,obs,usuario)
                                st.success(f'Parcela registrada até {fim:%d/%m/%Y}. Prazo concessivo: {prazo:%d/%m/%Y}.')
                                st.rerun()
                            except ValueError as ex:st.error(str(ex))
                regi=listar_ferias(filial,cid)
                if regi:
                    st.markdown('#### Parcelas registradas')
                    st.dataframe(_df_export(regi),hide_index=True,use_container_width=True)
                    _baixar_csv('📥 Exportar férias CSV',regi,f'rh_ferias_{filial}_{cid}.csv','rhfer_csv')
                    with st.expander('Alterar situação de parcela'):
                        op={f'{x["id"]} — {x["inicio"]} a {x["fim"]} — {x["situacao"]}':x for x in regi}
                        escolha=st.selectbox('Parcela',list(op),key='rh_fer_edit')
                        novo=st.selectbox('Situação nova',SITUACOES_FERIAS,key='rh_fer_status')
                        if st.button('Atualizar situação',key='rh_fer_salvar'):
                            try:mudar_situacao_ferias(filial,op[escolha]['id'],novo,usuario);st.rerun()
                            except ValueError as ex:st.error(str(ex))
            else:st.info('Nenhum ciclo de férias no intervalo monitorado.')
    with tabs[4]:
        st.subheader('ASOs — prazo e agendamento na SMT Saúde Ocupacional')
        st.info('Procedimento interno: solicitar agendamento 7 dias antes do vencimento informado. A periodicidade do ASO deve ser definida pelo PCMSO/NR-7, sem pressupor que todos sejam anuais.')
        if not ativos:st.info('Cadastre colaboradores primeiro.')
        else:
            cid=_selecionar_colaborador(ativos,'rh_aso_cid')
            with st.form('rh_cadastrar_aso'):
                t=st.selectbox('Tipo de exame',TIPOS_ASO)
                c1,c2=st.columns(2)
                realizou=st.checkbox('Informar data da realização anterior')
                realizado=c1.date_input('Exame realizado em',value=hoje()-timedelta(days=360),disabled=not realizou)
                vence=c2.date_input('Vencimento / próximo exame *',value=hoje()+timedelta(days=30))
                situ=st.selectbox('Situação do agendamento',STATUS_AGENDAMENTO)
                tem_agenda=st.checkbox('Já existe data de agendamento na SMT')
                agendado=st.date_input('Agendamento na SMT',value=hoje()+timedelta(days=1),disabled=not tem_agenda)
                obs=st.text_input('Observações administrativas (não inserir dados clínicos)')
                if st.form_submit_button('💾 Salvar controle de ASO'):
                    try:
                        registrar_aso(filial,cid,t,realizado if realizou else None,vence,
                            agendado if tem_agenda else None,situ,obs,usuario)
                        st.success('ASO cadastrado.');st.rerun()
                    except ValueError as ex:st.error(str(ex))
            asos=listar_asos(filial,cid)
            if asos:
                dat=[]
                for aso in asos:
                    status,dias=status_aso(aso['vencimento'],aso['agendado_para'],aso['situacao_agendamento'])
                    dat.append(dict(id=aso['id'],tipo=aso['tipo'],realizado=dstr(aso['realizado_em']),
                        vencimento=dstr(aso['vencimento']),agendar_a_partir=dstr(aso['vencimento']-timedelta(days=SMT_ANTECEDENCIA)),
                        marcado_para=dstr(aso['agendado_para']),status=status,dias_para_vencer=dias))
                st.dataframe(pd.DataFrame(dat),hide_index=True,use_container_width=True)
                _baixar_csv('📥 Exportar ASOs CSV',dat,f'rh_asos_{filial}_{cid}.csv','rhasocsv')
                with st.expander('📅 Atualizar agendamento SMT'):
                    op={f'{r["tipo"]} — vencimento {r["vencimento"]} — ID {r["id"]}':r for r in asos}
                    k=st.selectbox('ASO a atualizar',list(op))
                    sit=st.selectbox('Situação do agendamento',STATUS_AGENDAMENTO,key='rh_aso_edit_status')
                    ag=st.checkbox('Informar nova data de agendamento',key='rh_aso_agenda')
                    dia=st.date_input('Data na SMT',value=hoje()+timedelta(days=1),disabled=not ag,key='rh_aso_edit_dia')
                    if st.button('Salvar agendamento',key='rh_aso_edit'):
                        try:editar_aso(filial,op[k]['id'],dia if ag else None,sit,usuario);st.rerun()
                        except ValueError as ex:st.error(str(ex))
