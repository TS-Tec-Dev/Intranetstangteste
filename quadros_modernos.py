# -*- coding: utf-8 -*-
"""Quadros de consulta da Intranet Stang — apresentação sem alterações no repositório.

O módulo só recebe DataFrames prontos. Não grava dados, não aplica regras de
permissão e não faz consultas ao banco. Tudo é escapado antes de virar HTML.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
import hashlib
import io
import math
import re
import unicodedata

import pandas as pd
import streamlit as st


NOMES = {
    'ID': 'O.S.', 'ID_Compra': 'Pedido', 'Data_Criacao': 'Criada em',
    'Data_Solicitacao': 'Solicitado em', 'Solicitante': 'Solicitante',
    'Setor': 'Setor', 'Prioridade': 'Prioridade', 'Prazo_Limite': 'Prazo',
    'Status_Prazo': 'Situação do prazo', 'Status_Visual': 'Status',
    'Status': 'Status', 'Equipamento': 'Equipamento', 'Descricao': 'Descrição',
    'Solucao': 'Solução', 'Tipo_Manutencao': 'Tipo',
    'Itens_Trocados': 'Itens utilizados', 'finalizado_por': 'Responsável',
    'Data_Realizada': 'Realizado em', 'Periodo': 'Periodicidade',
    'Vencimento': 'Vencimento', 'Dias_Restantes': 'Dias restantes',
    'FM': 'Formulário', 'Categoria': 'Categoria', 'Item': 'Item',
    'Quantidade': 'Qtd.', 'Orcamento_Assinado': 'Orçamento',
}


def _normalizar(valor: object) -> str:
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    return ''.join(ch for ch in texto if not unicodedata.combining(ch)).casefold()


def _valor(valor: object) -> str:
    if valor is None:
        return '—'
    try:
        if pd.isna(valor):
            return '—'
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip()
    if not texto or texto.casefold() in ('none', 'nan', 'null', 'nat'):
        return '—'
    if isinstance(valor, (datetime, date)):
        return valor.strftime('%d/%m/%Y')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', texto):
        try:
            return datetime.strptime(texto, '%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:
            pass
    return texto


def _tag_classe(texto: str, coluna: str = '') -> str:
    """Coloração é somente visual e depende do significado, não da origem dos dados."""
    if coluna not in ('Status', 'Status_Prazo', 'Status_Visual', 'Prioridade',
                      'Orcamento_Assinado', 'Dias_Restantes'):
        return ''
    t = _normalizar(texto)
    if coluna == 'Prioridade':
        return ('bad' if 'urgente' in t else 'warning' if 'alta' in t else
                'medium' if 'media' in t else 'neutral')
    if coluna == 'Dias_Restantes':
        try:
            dias = int(float(texto))
            return 'bad' if dias < 0 else 'warning' if dias <= 7 else 'good'
        except (ValueError, TypeError):
            return 'neutral'
    if any(x in t for x in ('atrasad', 'vencid', 'recusad', 'reprovad', 'cancelad')):
        return 'bad'
    if any(x in t for x in ('no prazo', 'anexado', 'realizada', 'concluid', 'finalizada', 'aprovad')):
        return 'good'
    if any(x in t for x in ('andamento', 'abert', 'pendente', 'agendar', 'a vencer')):
        return 'warning'
    return 'neutral'


def _celula(col: str, valor: object) -> str:
    texto = _valor(valor)
    css_tag = _tag_classe(texto, col)
    escapado = escape(texto, quote=True)
    if css_tag:
        # Não repetir o emoji original depois do indicador do status.
        limpo = re.sub(r'\s*[🟢🔴🔵🟠✅]+\s*$', '', texto).strip()
        return f'<span class="stg-pill stg-{css_tag}" title="{escapado}"><i></i>{escape(limpo)}</span>'
    longo = col in ('Descricao', 'Solucao', 'Item', 'Itens_Trocados', 'FM')
    css = 'stg-ellip' if longo else 'stg-text'
    return f'<span class="{css}" title="{escapado}">{escapado}</span>'


_CSS = '''
<style>
.stg-board { --card:rgba(7,21,43,.94); --hdr:rgba(29,52,82,.98); --line:rgba(146,184,227,.16);
 --ink:#edf5ff; --muted:#b0c4dc; --accent:#359cff; --hover:rgba(33,97,151,.24); margin:.35rem 0 1.05rem; }
.stg-board.light { --card:rgba(255,255,255,.97); --hdr:#e4eef9; --line:rgba(31,67,114,.15);
 --ink:#183047; --muted:#536c89; --accent:#096fcf; --hover:#edf5fc; }
.stg-panel {border:1px solid var(--line);border-radius:15px; overflow:hidden;
 background:var(--card); box-shadow:0 12px 28px rgba(0,7,23,.15); }
.stg-head {display:flex;align-items:center;justify-content:space-between;gap:12px;
 padding:13px 17px; border-bottom:1px solid var(--line);color:var(--ink);}
.stg-head .stg-title {font-size:1.02rem;font-weight:750;letter-spacing:.01em;}
.stg-count {font-size:.77rem;font-weight:650;color:var(--muted);}
.stg-scroll {overflow:auto;max-height:580px;scrollbar-color:rgba(124,164,206,.45) transparent;}
.stg-table {border-collapse:separate;border-spacing:0;width:100%;min-width:920px;
 color:var(--ink);font-size:.79rem;line-height:1.42;}
.stg-table thead th {position:sticky;top:0;background:var(--hdr); color:var(--muted);
 z-index:1;font-weight:750;letter-spacing:.015em;text-align:left; white-space:nowrap;
 border-bottom:1px solid var(--line);padding:11px 13px;}
.stg-table tbody td {padding:10px 13px;border-bottom:1px solid var(--line);
 vertical-align:middle; max-width:320px;white-space:nowrap;}
.stg-table tbody tr:nth-child(2n) {background:rgba(70,116,164,.065)}
.stg-table tbody tr:hover {background:var(--hover)}
.stg-ellip {display:inline-block;max-width:310px;overflow:hidden;white-space:nowrap;
 text-overflow:ellipsis;vertical-align:middle;} .stg-text {white-space:nowrap;}
.stg-pill {border:1px solid transparent;border-radius:999px;padding:4px 9px;
 display:inline-flex;align-items:center;gap:7px;font-size:.75rem;font-weight:750;white-space:nowrap;}
.stg-pill i {width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0;}
.stg-good {background:rgba(24,184,123,.13);color:#55ddb0;border-color:rgba(24,184,123,.23)}
.stg-warning {background:rgba(255,178,48,.13);color:#ffc877;border-color:rgba(255,178,48,.28)}
.stg-bad {background:rgba(248,88,98,.15);color:#ff8994;border-color:rgba(248,88,98,.25)}
.stg-medium {background:rgba(76,153,244,.15);color:#82bbff;border-color:rgba(76,153,244,.28)}
.stg-neutral {background:rgba(142,159,187,.13);color:#b3c2d5;border-color:rgba(142,159,187,.22)}
.stg-board.light .stg-good {color:#087a50}.stg-board.light .stg-warning {color:#9a6300}
.stg-board.light .stg-bad {color:#a82f43}.stg-board.light .stg-medium {color:#1258a2}
.stg-board.light .stg-neutral {color:#52657b}
.stg-foot {color:var(--muted);font-size:.78rem;display:flex;gap:14px;
 justify-content:space-between;padding:10px 17px;background:var(--card);}
.stg-kpis {display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:11px;margin:8px 0 20px}
.stg-kpi {min-height:82px;padding:13px 17px;border:1px solid var(--line);border-radius:15px;
 background:var(--card);color:var(--ink);box-shadow:0 8px 20px rgba(0,8,22,.12);}
.stg-kpi-label {display:block;color:var(--muted);font-size:.77rem;line-height:1.25}
.stg-kpi-value {display:block;font-weight:850;font-size:1.75rem;margin-top:5px;line-height:1.15}
.stg-kpi.good .stg-kpi-value {color:#34cc90}.stg-kpi.bad .stg-kpi-value {color:#ff747c}
.stg-kpi.warning .stg-kpi-value {color:#ffc36c}
@media(max-width:700px){.stg-head{padding:11px 12px}.stg-table{min-width:830px;font-size:.75rem}
.stg-table td,.stg-table thead th{padding:9px 10px}.stg-kpi-value{font-size:1.42rem}}
</style>
'''


def aplicar_estilo() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def _tema() -> str:
    return 'light' if st.session_state.get('tema_stang', 'Escuro') == 'Claro' else 'dark'


def indicadores(cards: list[tuple[str, object, str]]) -> None:
    """Lista (rótulo, valor, situação) com dados já calculados pela tela."""
    blocos = ''.join(
        f'<div class="stg-kpi {escape(str(situacao))}">'
        f'<span class="stg-kpi-label">{escape(str(rotulo))}</span>'
        f'<span class="stg-kpi-value">{escape(str(valor))}</span></div>'
        for rotulo, valor, situacao in cards
    )
    st.markdown(f'<div class="stg-board {_tema()}"><div class="stg-kpis">{blocos}</div></div>',
                unsafe_allow_html=True)


def preparar_tabela(df: pd.DataFrame, consulta: str = '', *,
                     ordenar_por: str | None = None, decrescente: bool = False) -> pd.DataFrame:
    resultado = df.copy()
    termo = (consulta or '').strip().casefold()
    if termo and not resultado.empty:
        busca = pd.Series(False, index=resultado.index)
        for coluna in resultado.columns:
            busca |= resultado[coluna].astype(str).str.casefold().str.contains(termo, regex=False, na=False)
        resultado = resultado.loc[busca]
    if ordenar_por in resultado.columns and not resultado.empty:
        tmp = resultado.copy()
        numeric = pd.to_numeric(tmp[ordenar_por], errors='coerce')
        if numeric.notna().mean() > .9:
            tmp['_ordem_uix'] = numeric
        else:
            tmp['_ordem_uix'] = tmp[ordenar_por].astype(str).str.casefold()
        resultado = tmp.sort_values('_ordem_uix', ascending=not decrescente, kind='stable', na_position='last').drop(columns=['_ordem_uix'])
    return resultado


def gerar_pdf_tabela(df: pd.DataFrame, titulo: str) -> bytes:
    """Gera PDF A4 paisagem com paginação automática, sem consultar ou gravar dados."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, LongTable, Paragraph, Spacer, TableStyle

    buffer = io.BytesIO()
    pagina = landscape(A4)
    doc = SimpleDocTemplate(buffer, pagesize=pagina, leftMargin=25,
                            rightMargin=25, topMargin=28, bottomMargin=27)
    estilo_titulo = ParagraphStyle('TituloStg', fontName='Helvetica-Bold', fontSize=14,
                                    leading=17, textColor=colors.HexColor('#123454'))
    estilo_head = ParagraphStyle('HeadStg', fontName='Helvetica-Bold', fontSize=7.2,
                                  leading=9, textColor=colors.white)
    estilo_cel = ParagraphStyle('CelStg', fontName='Helvetica', fontSize=6.8,
                                 leading=8.5, textColor=colors.HexColor('#16304c'), alignment=TA_LEFT)
    elementos = [Paragraph(escape(titulo), estilo_titulo), Spacer(1, 8)]
    if not len(df.columns):
        doc.build(elementos)
        return buffer.getvalue()
    colunas = list(df.columns)
    pesos = [2.4 if c in ('Descricao','Solucao','Item','Itens_Trocados','FM') else
             1.5 if c in ('Equipamento','Data_Criacao','Data_Solicitacao','Orcamento_Assinado','finalizado_por')
             else .9 if c in ('ID','ID_Compra','Quantidade','Dias_Restantes') else 1.1
             for c in colunas]
    largura_total = pagina[0] - doc.leftMargin - doc.rightMargin
    largura_colunas = [largura_total * w / sum(pesos) for w in pesos]
    dados = [[Paragraph(escape(NOMES.get(str(c),str(c))), estilo_head) for c in colunas]]
    for registro in df.itertuples(index=False,name=None):
        dados.append([Paragraph(escape(_valor(val).replace('🟢','').replace('🔴','').replace('🔵','').replace('🟠','').replace('✅','')),
                                 estilo_cel) for val in registro])
    tabela = LongTable(dados, colWidths=largura_colunas, repeatRows=1, hAlign='CENTER')
    tabela.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0), colors.HexColor('#143458')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#eef4fa')]),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LINEBELOW',(0,0),(-1,-1),.25,colors.HexColor('#cbd9e7')),
    ]))
    elementos.append(tabela)
    doc.build(elementos)
    return buffer.getvalue()


def render_tabela(df: pd.DataFrame, *, chave: str, titulo: str,
                  por_pagina: int = 12, busca: bool = True,
                  ordenar_por: str | None = None, decrescente: bool = False,
                  mostrar_exportacao: bool = False) -> pd.DataFrame:
    """Renderiza tabela paginada. O retorno inclui somente linhas filtradas (sem paginação)."""
    if df is None or df.empty:
        st.info(f'Nenhum registro disponível em {titulo.lower()}.')
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    controles = st.columns([5, 1.5]) if busca else [None, None]
    if busca:
        with controles[0]:
            termo = st.text_input('Buscar registros', placeholder='Buscar por código, nome, item ou descrição...',
                                  key=f'stg_busca_{chave}', label_visibility='collapsed')
        with controles[1]:
            tamanho = st.selectbox('Linhas por página', [8, 12, 20, 50],
                                   index=[8, 12, 20, 50].index(por_pagina) if por_pagina in (8,12,20,50) else 1,
                                   key=f'stg_tamanho_{chave}', label_visibility='collapsed')
    else:
        termo, tamanho = '', por_pagina

    filtrado = preparar_tabela(df, termo, ordenar_por=ordenar_por, decrescente=decrescente)
    total = len(filtrado)
    paginas = max(1, math.ceil(total / tamanho))
    if paginas > 1:
        c_a, c_b = st.columns([4.6, 1.4])
        with c_b:
            pagina = st.selectbox('Página', list(range(1, paginas+1)),
                                   key=f'stg_pagina_{chave}', format_func=lambda p: f'Página {p}/{paginas}')
    else:
        pagina = 1
    inicio = (pagina-1) * tamanho
    atual = filtrado.iloc[inicio:inicio+tamanho]
    cabecalhos = ''.join(f'<th scope="col">{escape(NOMES.get(str(c), str(c)))}</th>' for c in atual.columns)
    linhas = []
    for row in atual.itertuples(index=False, name=None):
        cells = ''.join(f'<td>{_celula(str(col), valor)}</td>' for col, valor in zip(atual.columns, row))
        linhas.append(f'<tr>{cells}</tr>')
    resumo = f'Exibindo {inicio+1 if total else 0}–{min(inicio+tamanho,total)} de {total} registros'
    html = (f'<div class="stg-board {_tema()}"><section class="stg-panel" aria-label="{escape(titulo)}">'
            f'<div class="stg-head"><span class="stg-title">{escape(titulo)}</span>'
            f'<span class="stg-count">{total} registros</span></div>'
            '<div class="stg-scroll"><table class="stg-table"><thead><tr>' + cabecalhos +
            '</tr></thead><tbody>' + ''.join(linhas) + '</tbody></table></div>'
            f'<div class="stg-foot"><span>{resumo}</span><span>Role para os lados para ver mais colunas</span></div>'
            '</section></div>')
    st.markdown(html, unsafe_allow_html=True)
    if st.checkbox('Modo planilha (ordenar colunas e copiar células)', value=False,
                   key=f'stg_modo_planilha_{chave}'):
        st.dataframe(filtrado, use_container_width=True, hide_index=True)
    if mostrar_exportacao:
        csv = filtrado.to_csv(index=False, sep=';').encode('utf-8-sig')
        assinatura = hashlib.sha256(csv).hexdigest()
        chave_pdf = f'stg_pdf_{chave}'
        chave_ass = f'stg_pdf_assinatura_{chave}'
        if st.session_state.get(chave_ass) != assinatura:
            st.session_state.pop(chave_pdf, None)
            st.session_state.pop(chave_ass, None)
        c_csv, c_pdf = st.columns(2)
        with c_csv:
            st.download_button('⬇️ Exportar CSV', data=csv, file_name=f'{chave}.csv',
                               mime='text/csv', key=f'stg_csv_{chave}', use_container_width=True)
        with c_pdf:
            if st.button('🖨️ Preparar PDF para impressão', key=f'stg_gerar_pdf_{chave}', use_container_width=True):
                try:
                    st.session_state[chave_pdf] = gerar_pdf_tabela(filtrado, titulo)
                    st.session_state[chave_ass] = assinatura
                except (ImportError, ValueError, TypeError, OSError) as erro:
                    st.error(f'Não foi possível preparar o PDF: {erro}')
        if st.session_state.get(chave_pdf):
            st.download_button('📄 Baixar PDF pronto / Imprimir', data=st.session_state[chave_pdf],
                               file_name=f'{chave}.pdf', mime='application/pdf',
                               key=f'stg_download_pdf_{chave}', use_container_width=True)
    return filtrado


def resumir_os(df: pd.DataFrame) -> list[tuple[str, object, str]]:
    if df.empty:
        return [('Total O.S.', 0, ''), ('Em aberto', 0, 'warning'), ('Finalizadas', 0, 'good'), ('Urgentes', 0, 'bad')]
    status = df.get('Status', pd.Series('', index=df.index)).astype(str)
    prioridade = df.get('Prioridade', pd.Series('', index=df.index)).astype(str)
    return [('Total de O.S.', len(df), ''),
            ('Em aberto', int(status.str.contains('aberto|andamento', case=False, regex=True, na=False).sum()), 'warning'),
            ('Finalizadas', int(status.str.contains('finalizada|concluída', case=False, regex=True, na=False).sum()), 'good'),
            ('Urgentes', int(prioridade.str.contains('urgente', case=False, na=False).sum()), 'bad')]


def resumir_fms(df: pd.DataFrame) -> list[tuple[str, object, str]]:
    dias = pd.to_numeric(df.get('Dias_Restantes', pd.Series(dtype=float)), errors='coerce')
    return [('Formulários', len(df), ''), ('No prazo', int((dias >= 0).sum()), 'good'),
            ('Atrasados', int((dias < 0).sum()), 'bad'),
            ('Próximos 7 dias', int(((dias >= 0) & (dias <= 7)).sum()), 'warning')]


def resumir_compras(df: pd.DataFrame) -> list[tuple[str, object, str]]:
    if df.empty:
        return [('Pedidos', 0, ''), ('Realizados', 0, 'good'), ('Em aberto', 0, 'warning'), ('Com orçamento', 0, '')]
    # Cada pedido pode conter vários itens; o indicador conta pedidos, não linhas.
    col = 'ID_Compra'
    unique = df.drop_duplicates(subset=[col]) if col in df.columns else df
    status = unique.get('Status', pd.Series('', index=unique.index)).astype(str)
    orc = unique.get('Orcamento_Assinado', pd.Series('', index=unique.index)).astype(str)
    anex = orc.str.strip().str.casefold().apply(lambda v: v not in ('', 'nan', 'none', 'null', '—'))
    return [('Pedidos', len(unique), ''),
            ('Realizados', int(status.str.contains('realizada', case=False, na=False).sum()), 'good'),
            ('Em aberto', int(status.str.contains('abert|pendente|solicitad', case=False, regex=True, na=False).sum()), 'warning'),
            ('Com orçamento', int(anex.sum()), '')]


def preparar_fms(df_fms: pd.DataFrame, hoje: date | None = None) -> pd.DataFrame:
    """Cálculo exclusivo para exibição do Dashboard; nunca grava no cadastro."""
    cols = ['FM','Data_Realizada','Periodo','Vencimento','Dias_Restantes','Status']
    if df_fms is None or df_fms.empty:
        return pd.DataFrame(columns=cols)
    hoje = hoje or datetime.now().date()
    df = df_fms.copy()
    realizadas = pd.to_datetime(df.get('Data_Realizada', pd.Series(index=df.index,dtype=str)), errors='coerce', dayfirst=True, format='mixed').dt.date
    prazo = pd.to_numeric(df.get('Dias_Prazo', pd.Series(index=df.index,dtype=str)), errors='coerce').fillna(0).astype(int)
    df['Data_Realizada'] = realizadas
    df['Vencimento'] = [d + timedelta(days=int(n)) if pd.notna(d) else pd.NaT for d,n in zip(realizadas,prazo)]
    df['Dias_Restantes'] = [(d-hoje).days if pd.notna(d) else None for d in df['Vencimento']]
    def situacao(dias):
        return ('Sem data' if dias is None or pd.isna(dias) else 'Atrasado 🔴' if dias < 0 else 'No Prazo 🟢')
    df['Status'] = [situacao(d) for d in df['Dias_Restantes']]
    for c in cols:
        if c not in df.columns:
            df[c] = ''
    return df[cols]
