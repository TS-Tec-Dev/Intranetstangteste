import streamlit as st

try:
    import menu_icones_3d as ui3d
except ImportError:
    class _Ui3dFallback:
        @staticmethod
        def titulo(texto, chave, nivel=1):
            st.markdown('#' * nivel + ' ' + texto)
        @staticmethod
        def aba(texto, chave):
            return texto
    ui3d = _Ui3dFallback()
from time import perf_counter

_INTRANET_INICIO_EXECUCAO = perf_counter()
import streamlit.components.v1 as components
import pandas as pd
import os
from datetime import datetime, timedelta
from functools import lru_cache
import plotly.express as px
import plotly.graph_objects as go
from PIL import Image
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import io
import html as htmlmod
import infra_persistencia as repositorio
from seguranca_senhas import gerar_hash, verificar_senha
import tema_stang
from controle_acesso_filiais import filiais_permitidas, pode_gerir_todas_as_filiais

# Tenta importar bibliotecas para manipulação e carimbo de PDFs
try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    HAS_PDF_LIBS = True
except ImportError:
    HAS_PDF_LIBS = False

# Cria diretórios de uploads se não existirem
os.makedirs("uploads_orcamentos", exist_ok=True)
os.makedirs("uploads_assinaturas", exist_ok=True)

# Configuração da página
icone_path = "icone.ico" if os.path.exists("icone.ico") else ("logo.png" if os.path.exists("logo.png") else "🔧")
st.set_page_config(
    page_title="Intranet Stang - Gestão e Manutenção",
    page_icon=icone_path,
    layout="wide",
    initial_sidebar_state="expanded"
)
# Design visual isolado: tema de exibição não altera CSVs, senhas, banco nem sessões de login.
tema_stang.inicializar_tema()
tema_stang.aplicar_tema("capa.png")
if not repositorio.modo_producao():
    st.warning("⚠️ Banco de desenvolvimento local ativo. No Streamlit Cloud, configure DATABASE_URL para não perder dados após atualizações.")

# Lista completa de menus disponíveis no sistema
TODOS_MENUS = [
    "📝 Nova O.S.",
    "📋 Gerenciar O.S.",
    "🖨️ Imprimir O.S.",
    "📅 Formulários e Prazos (FMs)",
    "🛒 Solicitações de Compras",
    "✍️ Painel de Assinaturas",
    "📊 Dashboard",
    "🧰 Ferramentaria",
    "👥 Gestão de RH",
    "🏢 Filiais e Backup"
]

# Bancos de Dados locais CSV
ARQUIVO_OS = "banco_os.csv"
ARQUIVO_FMS = "banco_fms.csv"
ARQUIVO_USERS = "banco_usuarios.csv"
ARQUIVO_COMPRAS = "banco_compras.csv"

def inicializar_bancos():
    # Migração compatível: lê sempre a versão persistente. Os CSVs do GitHub
    # são importados uma única vez pelo repositório externo, nunca usados nas gravações.
    colunas_os = repositorio.TEMPLATES["banco_os.csv"]
    df = repositorio.ler_csv(ARQUIVO_OS, dtype=str)
    mudou = False
    if "Responsavel_Servico" in df.columns and "finalizado_por" not in df.columns:
        df["finalizado_por"] = df["Responsavel_Servico"]
        mudou = True
    if "Finalizado_Por" in df.columns and "finalizado_por" not in df.columns:
        df["finalizado_por"] = df["Finalizado_Por"]
        mudou = True
    for col in colunas_os:
        if col not in df.columns:
            df[col] = ""
            mudou = True
    if mudou:
        repositorio.salvar_csv(df, ARQUIVO_OS)

    repositorio.ler_csv(ARQUIVO_FMS, dtype=str)

    df_c = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)
    mudou_c = False
    for col in repositorio.TEMPLATES["banco_compras.csv"]:
        if col not in df_c.columns:
            df_c[col] = ("None" if col in ["Orcamento_Assinado", "NF_Anexada", "Boleto_Anexado", "Assinado_Por"]
                         else "Não" if col == "Email_Enviado" else "")
            mudou_c = True
    if mudou_c:
        repositorio.salvar_csv(df_c, ARQUIVO_COMPRAS)

    df_users = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
    if df_users.empty:
        # Somente instalação realmente nova, sem CSV legado nem usuários no banco.
        segredo = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not segredo:
            try:
                segredo = str(st.secrets.get("BOOTSTRAP_ADMIN_PASSWORD", ""))
            except Exception:
                pass
        if len(segredo) < 12:
            st.error("Instalação nova: configure BOOTSTRAP_ADMIN_PASSWORD com pelo menos 12 caracteres nos Secrets do Streamlit.")
            st.stop()
        df_users = pd.DataFrame([{
            "Usuario": "admin", "Senha": gerar_hash(segredo), "Validade": "Vitalício",
            "Permissoes": ",".join(TODOS_MENUS), "Admin": "Sim", "Assinatura_PNG": "None",
            "Email_Usuario": "", "Senha_App_Email": "", "Servidor_SMTP": "smtp.gmail.com",
            "Porta_SMTP": "587", "Filiais": "MANDALLA"
        }])
        repositorio.salvar_csv(df_users, ARQUIVO_USERS)
    else:
        mudou_u = False
        for col in repositorio.TEMPLATES["banco_usuarios.csv"]:
            if col not in df_users.columns:
                if col == "Assinatura_PNG": df_users[col] = "None"
                elif col == "Servidor_SMTP": df_users[col] = "smtp.gmail.com"
                elif col == "Porta_SMTP": df_users[col] = "587"
                elif col == "Filiais": df_users[col] = "MANDALLA"
                else: df_users[col] = ""
                mudou_u = True
        if mudou_u:
            repositorio.salvar_csv(df_users, ARQUIVO_USERS)


# As migrações são verificadas uma vez por sessão, não a cada clique.
# Não utilizar cache global para registros operacionais (vários usuários/filiais).
@lru_cache(maxsize=2)
def _anexos_legados_migrados(url_banco):
    # A própria função verifica no PostgreSQL a migração concluída.
    # Depois da primeira verificação por processo não precisa consultar outra vez.
    return repositorio.importar_anexos_legados()

_INTRANET_INICIO_BANCO = perf_counter()
if not st.session_state.get("_intranet_bancos_inicializados_20261103", False):
    inicializar_bancos()
    _anexos_legados_migrados(repositorio.config_database_url())
    st.session_state["_intranet_bancos_inicializados_20261103"] = True
_INTRANET_TEMPO_INICIALIZACAO = perf_counter() - _INTRANET_INICIO_BANCO

def carregar_banco_os():
    # No Supabase, os arquivos CSV não precisam existir no disco local.
    df = repositorio.ler_csv(ARQUIVO_OS, dtype=str)
    if "ID" in df.columns:
        df["ID"] = pd.to_numeric(df["ID"], errors="coerce").fillna(0).astype(int)
    return df

# Recuperação sob demanda: mantém no Supabase os anexos que não estão no cache
# local do Streamlit. Evita transferir toda a biblioteca a cada clique.
def documento_disponivel(caminho):
    if caminho is None or pd.isna(caminho):
        return False
    caminho = str(caminho).strip()
    if not caminho or caminho.lower() in ("none", "nan"):
        return False
    if os.path.exists(caminho):
        return True
    try:
        return bool(repositorio.recuperar_documento(caminho))
    except (OSError, ValueError, TypeError):
        return False


# Visualização de documentos isolada: renderiza PDFs em imagens no servidor,
# porque iframes/object com data:application/pdf são bloqueados em alguns navegadores.
def exibir_documento(caminho_arquivo, titulo):
    from visualizacao_documentos import exibir_documento as _exibir
    atual = st.session_state.get('filial_ativa', 'MANDALLA')
    _exibir(caminho_arquivo, titulo,
           chave=f'{atual}_{st.session_state.get("sel_ass_id", "")}_{titulo}'.replace(' ', '_'))


def _etapa_preenchida(valor):
    return pd.notna(valor) and str(valor).strip().casefold() not in ('', 'none', 'nan', 'null', 'não')


def _resumo_etapas_compra(df_pedido):
    """Status no dropdown calculado pelos registros, sem baixar todos os PDFs."""
    if df_pedido.empty:
        return (0, 5)
    colunas = ('Orcamento_Assinado', 'NF_Anexada', 'Boleto_Anexado', 'Assinado_Por')
    etapas = [all(_etapa_preenchida(x) for x in df_pedido[c].tolist()) if c in df_pedido.columns else False
              for c in colunas]
    enviados = (all(str(v).strip().casefold().startswith('sim')
                    for v in df_pedido['Email_Enviado'].tolist())
                if 'Email_Enviado' in df_pedido.columns else False)
    etapas.append(enviados)
    return (sum(etapas), len(etapas))

# --- FUNÇÃO PARA GERAR O PDF DA ORDEM DE SERVIÇO ---
def gerar_pdf_os(os_row):
    if not HAS_PDF_LIBS:
        return None
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    styles = getSampleStyleSheet()
    
    style_title = ParagraphStyle('Title', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1)
    style_header_right = ParagraphStyle('HeaderRight', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=2)
    style_cell_bold = ParagraphStyle('CellBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9)
    style_cell_normal = ParagraphStyle('CellNormal', parent=styles['Normal'], fontName='Helvetica', fontSize=9)
    style_cell_center_bold = ParagraphStyle('CellCenterBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=1)
    
    elements = []
    
    # 1. Tabela de Cabeçalho (Logo, Título e FM)
    logo_cell = Paragraph("<b>STANG</b>", style_title)
    if os.path.exists("logo.png"):
        try:
            logo_cell = RLImage("logo.png", width=100, height=35)
        except Exception:
            pass
            
    header_data = [
        [
            logo_cell,
            Paragraph("<b>Solicitação de Manutenção - Ordem de Serviço</b>", style_title),
            Paragraph("<b>FM 12</b><br/>Revisão: 02/2024", style_header_right)
        ]
    ]
    t_header = RLTable(header_data, colWidths=[120, 280, 140])
    t_header.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'CENTER'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_header)
    elements.append(Spacer(1, 4))
    
    # 2. Dados de Identificação (Número, Data, Hora)
    data_criacao_val = str(os_row.get('Data_Criacao', ''))
    partes_dt = data_criacao_val.split(' ')
    data_str = partes_dt[0] if len(partes_dt) > 0 else ''
    hora_str = partes_dt[1] if len(partes_dt) > 1 else '17:00'
    
    info_data = [
        [
            Paragraph(f"<b>Número:</b> {os_row.get('ID', '')}", style_cell_normal),
            Paragraph(f"<b>Data:</b> {data_str}", style_cell_normal),
            Paragraph(f"<b>Hora:</b> {hora_str}", style_cell_normal)
        ]
    ]
    t_info = RLTable(info_data, colWidths=[180, 180, 180])
    t_info.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.black),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_info)
    elements.append(Spacer(1, 4))
    
    # 3. Tipo e Prioridade
    prio_data = [
        [
            Paragraph("Tipo de Manutenção", style_cell_center_bold),
            Paragraph("Prioridade de Manutenção", style_cell_center_bold)
        ],
        [
            Paragraph(f"<b>{os_row.get('Tipo_Manutencao', '')}</b>", style_cell_center_bold),
            Paragraph(f"<b>{os_row.get('Prioridade', '')}</b>", style_cell_center_bold)
        ]
    ]
    t_prio = RLTable(prio_data, colWidths=[270, 270])
    t_prio.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,0), (1,0), colors.HexColor('#e0e0e0')),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_prio)
    elements.append(Spacer(1, 4))
    
    # 4. Setor, Solicitante e Equipamento
    equip_val = os_row.get('Equipamento', 'N/A')
    if pd.isna(equip_val) or str(equip_val).strip() == "":
        equip_val = 'N/A'
        
    solic_data = [
        [
            Paragraph(f"<b>SETOR:</b> {os_row.get('Setor', '')}", style_cell_normal),
            Paragraph(f"<b>SOLICITANTE:</b> {os_row.get('Solicitante', '')}", style_cell_normal)
        ],
        [
            Paragraph(f"<b>Equipamento:</b> {equip_val}", style_cell_normal),
            ""
        ]
    ]
    t_solic = RLTable(solic_data, colWidths=[270, 270])
    t_solic.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.black),
        ('SPAN', (0,1), (1,1)),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_solic)
    elements.append(Spacer(1, 4))
    
    # 5. Seções de Texto: Descrição, Solução, Itens Trocados
    def criar_secao_texto(titulo, conteudo, min_height=40):
        val_txt = conteudo if pd.notna(conteudo) else ""
        sec_data = [
            [Paragraph(f"<b>{titulo}</b>", style_cell_center_bold)],
            [Paragraph(str(val_txt).replace('\n', '<br/>'), style_cell_normal)]
        ]
        t_sec = RLTable(sec_data, colWidths=[540], rowHeights=[18, max(min_height, 35)])
        t_sec.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 1, colors.black),
            ('INNERGRID', (0,0), (-1,-1), 1, colors.black),
            ('BACKGROUND', (0,0), (0,0), colors.HexColor('#e0e0e0')),
            ('VALIGN', (0,1), (0,1), 'TOP'),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        return t_sec

    elements.append(criar_secao_texto("Descrição do Problema", os_row.get('Descricao', ''), min_height=60))
    elements.append(Spacer(1, 4))
    elements.append(criar_secao_texto("Descrição da Solução", os_row.get('Solucao', ''), min_height=45))
    elements.append(Spacer(1, 4))
    elements.append(criar_secao_texto("Itens Trocados", os_row.get('Itens_Trocados', ''), min_height=35))
    elements.append(Spacer(1, 25))
    
    # 6. Assinaturas
    finalizador_val = os_row.get('finalizado_por', '')
    if pd.isna(finalizador_val):
        finalizador_val = ''
        
    ass_data = [
        [
            Paragraph("__________________________________________________<br/><b>Manutenção</b>", style_cell_center_bold),
            Paragraph(f"__________________________________________________<br/><b>Responsável pelo Serviço ({finalizador_val})</b>", style_cell_center_bold)
        ]
    ]
    t_ass = RLTable(ass_data, colWidths=[270, 270])
    t_ass.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    elements.append(t_ass)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

# --- RELATÓRIO GERAL DE O.S.: PDF E HTML PARA IMPRESSÃO ---
def gerar_pdf_relatorio_os(df_rel):
    """Gera um relatório geral de O.S. em PDF usando os registros já filtrados."""
    if not HAS_PDF_LIBS:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=__import__("reportlab.lib.pagesizes", fromlist=["landscape"]).landscape(letter),
        rightMargin=24,
        leftMargin=24,
        topMargin=28,
        bottomMargin=28
    )

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "RelTitulo",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        alignment=1,
        spaceAfter=6
    )
    subtitulo = ParagraphStyle(
        "RelSubtitulo",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        alignment=1,
        textColor=colors.HexColor("#444444"),
        spaceAfter=8
    )
    cab = ParagraphStyle(
        "RelCab",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        alignment=1,
        leading=8
    )
    cel = ParagraphStyle(
        "RelCel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=6.5,
        leading=7.5
    )
    celc = ParagraphStyle(
        "RelCelC",
        parent=cel,
        alignment=1
    )

    def esc(v):
        if pd.isna(v):
            return ""
        return htmlmod.escape(str(v)).replace("\n", "<br/>")

    elements = [
        Paragraph("STANG - RELATÓRIO GERAL DE ORDENS DE SERVIÇO", titulo),
        Paragraph(
            f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} | Total de O.S.: {len(df_rel)}",
            subtitulo
        )
    ]

    colunas = [
        ("ID", 34), ("Data", 64), ("Solicitante", 78), ("Setor", 60),
        ("Equipamento", 85), ("Tipo", 58), ("Prioridade", 54),
        ("Status", 62), ("Executante", 85)
    ]
    header = [Paragraph(htmlmod.escape(t), cab) for t, _ in colunas]
    tabela_data = [header]

    for _, row in df_rel.iterrows():
        tabela_data.append([
            Paragraph(esc(row.get("ID", "")), celc),
            Paragraph(esc(row.get("Data_Criacao", "")), celc),
            Paragraph(esc(row.get("Solicitante", "")), cel),
            Paragraph(esc(row.get("Setor", "")), cel),
            Paragraph(esc(row.get("Equipamento", "")), cel),
            Paragraph(esc(row.get("Tipo_Manutencao", "")), celc),
            Paragraph(esc(row.get("Prioridade", "")), celc),
            Paragraph(esc(row.get("Status", "")), celc),
            Paragraph(esc(row.get("finalizado_por", "")), cel)
        ])

    tabela = RLTable(
        tabela_data,
        colWidths=[w for _, w in colunas],
        repeatRows=1,
        splitByRow=1
    )
    tabela.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8B8B8")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E6E6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(tabela)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def gerar_html_relatorio_os_impressao(df_rel, filtros_descricao="Sem filtros"):
    """Monta uma página HTML limpa para impressão do relatório geral de O.S."""
    colunas = [
        ("ID", "ID"),
        ("Data", "Data_Criacao"),
        ("Solicitante", "Solicitante"),
        ("Setor", "Setor"),
        ("Equipamento", "Equipamento"),
        ("Tipo", "Tipo_Manutencao"),
        ("Prioridade", "Prioridade"),
        ("Status", "Status"),
        ("Executante", "finalizado_por")
    ]

    linhas_html = []
    for _, row in df_rel.iterrows():
        celulas = []
        for _, campo in colunas:
            valor = "" if pd.isna(row.get(campo, "")) else str(row.get(campo, ""))
            celulas.append(f"<td>{htmlmod.escape(valor).replace(chr(10), '<br>')}</td>")
        linhas_html.append("<tr>" + "".join(celulas) + "</tr>")

    if not linhas_html:
        linhas_html.append(
            "<tr><td colspan='9' style='text-align:center;'>Nenhuma O.S. encontrada para impressão.</td></tr>"
        )

    cabecalhos = "".join(f"<th>{htmlmod.escape(titulo)}</th>" for titulo, _ in colunas)

    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Relatório Geral de O.S.</title>
<style>
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0;
        padding: 16px;
        background: #ffffff;
        color: #000000;
        font-family: Arial, Helvetica, sans-serif;
    }}
    .toolbar {{
        display:flex;
        gap:10px;
        align-items:center;
        justify-content:center;
        margin-bottom:14px;
    }}
    .btn {{
        border:0;
        border-radius:6px;
        padding:10px 18px;
        font-size:14px;
        font-weight:700;
        cursor:pointer;
        background:#111111;
        color:#ffffff;
    }}
    .cabecalho {{
        border:1.5px solid #000;
        padding:12px;
        margin-bottom:10px;
    }}
    .titulo {{
        text-align:center;
        font-size:18px;
        font-weight:800;
        margin-bottom:4px;
    }}
    .meta {{
        text-align:center;
        font-size:11px;
        margin-top:3px;
    }}
    .resumo {{
        font-size:11px;
        margin:8px 0 10px;
    }}
    table {{
        width:100%;
        border-collapse:collapse;
        font-size:9px;
        table-layout:fixed;
    }}
    th, td {{
        border:1px solid #000;
        padding:5px 4px;
        vertical-align:top;
        word-break:break-word;
    }}
    th {{
        background:#e6e6e6;
        text-align:center;
    }}
    tr {{ page-break-inside:avoid; }}
    @media print {{
        @page {{ size: landscape; margin: 8mm; }}
        body {{ padding:0; }}
        .toolbar {{ display:none !important; }}
        .cabecalho {{ border:1px solid #000; }}
        table {{ font-size:8px; }}
        th, td {{ padding:4px 3px; }}
    }}
</style>
</head>
<body>
    <div class="toolbar">
        <button class="btn" onclick="window.print()">🖨️ Imprimir Relatório Geral</button>
    </div>

    <div class="cabecalho">
        <div class="titulo">STANG - RELATÓRIO GERAL DE ORDENS DE SERVIÇO</div>
        <div class="meta">Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} | Total de O.S.: {len(df_rel)}</div>
        <div class="meta">Filtros aplicados: {htmlmod.escape(str(filtros_descricao))}</div>
    </div>

    <div class="resumo"><b>Registros apresentados:</b> {len(df_rel)}</div>

    <table>
        <thead><tr>{cabecalhos}</tr></thead>
        <tbody>{''.join(linhas_html)}</tbody>
    </table>
</body>
</html>
"""
# --- FUNÇÃO PARA GERAR O HTML COMPLETO DA OS DA IMPRESSÃO ---
def gerar_html_os_impressao(os_row):
    equipamento_val = os_row['Equipamento'] if pd.notna(os_row.get('Equipamento')) else 'N/A'
    solucao_val = os_row['Solucao'] if pd.notna(os_row.get('Solucao')) else ''
    itens_val = os_row['Itens_Trocados'] if pd.notna(os_row.get('Itens_Trocados')) else ''
    finalizador_val = os_row['finalizado_por'] if pd.notna(os_row.get('finalizado_por')) else ''
    data_criacao_val = str(os_row['Data_Criacao'])
    
    logo_base64 = ""
    if os.path.exists("logo.png"):
        with open("logo.png", "rb") as img_file:
            logo_base64 = base64.b64encode(img_file.read()).decode()

    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ background-color: #ffffff; color: #000000; margin: 0; padding: 10px; font-family: Arial, sans-serif; }}
        .print-btn-container {{ text-align: center; margin-bottom: 20px; }}
        .btn-imprimir {{ background-color: #007bff; color: white; border: none; padding: 12px 25px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; }}
        @media print {{ .print-btn-container {{ display: none !important; }} body {{ padding: 0; }} }}
    </style>
</head>
<body>
    <div class="print-btn-container">
        <button class="btn-imprimir" onclick="window.print()">🖨️ Imprimir O.S.</button>
    </div>
    <div style="background-color: #ffffff; color: #000000; padding: 20px; border: 2px solid #000; max-width: 800px; margin: auto;">
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000;">
            <tr>
                <td style="width: 28%; border: 1px solid #000; padding: 5px; text-align: center; vertical-align: middle;">
                    <img src="data:image/png;base64,{logo_base64}" style="max-height: 45px; max-width: 100%;">
                </td>
                <td style="width: 44%; border: 1px solid #000; text-align: center; vertical-align: middle;">
                    <h3 style="margin: 0; color: #000 !important; font-size: 15px;">Solicitação de Manutenção - Ordem de Serviço</h3>
                </td>
                <td style="width: 28%; border: 1px solid #000; padding: 5px; font-size: 11px; text-align: right; color: #000 !important; vertical-align: middle;">
                    <b>FM 12</b><br>Revisão: 02/2024
                </td>
            </tr>
        </table>
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000; font-size: 12px; color: #000 !important;">
            <tr>
                <td style="border: 1px solid #000; padding: 5px; width: 33%;"><b>Número:</b> {os_row['ID']}</td>
                <td style="border: 1px solid #000; padding: 5px; width: 34%;"><b>Data:</b> {data_criacao_val.split(' ')[0]}</td>
                <td style="border: 1px solid #000; padding: 5px; width: 33%;"><b>Hora:</b> {data_criacao_val.split(' ')[1] if len(data_criacao_val.split(' ')) > 1 else '17:00'}</td>
            </tr>
        </table>
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000; font-size: 12px; text-align: center; color: #000 !important;">
            <tr>
                <td style="border: 1px solid #000; background-color: #e0e0e0; padding: 4px; width: 50%;"><b>Tipo de Manutenção</b></td>
                <td style="border: 1px solid #000; background-color: #e0e0e0; padding: 4px; width: 50%;"><b>Prioridade de Manutenção</b></td>
            </tr>
            <tr>
                <td style="border: 1px solid #000; padding: 10px; font-size: 14px; font-weight: bold;">{os_row['Tipo_Manutencao']}</td>
                <td style="border: 1px solid #000; padding: 10px; font-size: 14px; font-weight: bold;">{os_row['Prioridade']}</td>
            </tr>
        </table>
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000; font-size: 12px; color: #000 !important;">
            <tr>
                <td style="border: 1px solid #000; padding: 5px; width: 50%;"><b>SETOR:</b> {os_row['Setor']}</td>
                <td style="border: 1px solid #000; padding: 5px; width: 50%;"><b>SOLICITANTE:</b> {os_row['Solicitante']}</td>
            </tr>
            <tr>
                <td style="border: 1px solid #000; padding: 5px;" colspan="2"><b>Equipamento:</b> {equipamento_val}</td>
            </tr>
        </table>
        <div style="border: 1px solid #000; border-top: none;">
            <div style="background-color: #e0e0e0; text-align: center; font-size: 12px; font-weight: bold; border-bottom: 1px solid #000; padding: 4px; color: #000 !important;">Descrição do Problema</div>
            <div style="padding: 10px; min-height: 70px; font-size: 13px; color: #000 !important;">{os_row['Descricao']}</div>
        </div>
        <div style="border: 1px solid #000; border-top: none;">
            <div style="background-color: #e0e0e0; text-align: center; font-size: 12px; font-weight: bold; border-bottom: 1px solid #000; padding: 4px; color: #000 !important;">Descrição da Solução</div>
            <div style="padding: 10px; min-height: 50px; font-size: 13px; color: #000 !important;">{solucao_val}</div>
        </div>
        <div style="border: 1px solid #000; border-top: none;">
            <div style="background-color: #e0e0e0; text-align: center; font-size: 12px; font-weight: bold; border-bottom: 1px solid #000; padding: 4px; color: #000 !important;">Itens Trocados</div>
            <div style="padding: 10px; min-height: 40px; font-size: 13px; color: #000 !important;">{itens_val}</div>
        </div>
        <table style="width: 100%; margin-top: 35px; font-size: 12px; border-collapse: collapse; color: #000 !important;">
            <tr>
                <td style="text-align: center; width: 50%;">__________________________________________________<br><b>Manutenção</b></td>
                <td style="text-align: center; width: 50%;">__________________________________________________<br><b>Responsável pelo Serviço ({finalizador_val})</b></td>
            </tr>
        </table>
    </div>
</body>
</html>
"""

# --- FUNÇÃO PARA APLICAR A ASSINATURA DIGITAL NO FINAL DO DOCUMENTO ---
def carimbar_assinatura_no_documento(caminho_doc, caminho_assinatura_png, nome_usuario):
    if not documento_disponivel(caminho_doc) or not documento_disponivel(caminho_assinatura_png):
        return False
    
    ext = os.path.splitext(caminho_doc)[1].lower()
    
    if ext == ".pdf" and HAS_PDF_LIBS:
        temp_path = None
        try:
            # pypdf >= 7: a pagina precisa pertencer ao PdfWriter ANTES do merge.
            # O carimbo e apenas visual: nao representa assinatura digital ICP-Brasil.
            reader = PdfReader(caminho_doc)
            if not reader.pages:
                raise ValueError("O PDF nao possui paginas para receber o carimbo.")

            writer = PdfWriter()
            writer.append(reader)  # clona as paginas preservando a ordem
            ultima_pagina = writer.pages[-1]  # pagina VINCULADA ao writer

            # Ajusta documentos com orientacao por rotacao antes de desenhar o carimbo.
            if ultima_pagina.rotation:
                ultima_pagina.transfer_rotation_to_content()
            largura = float(ultima_pagina.mediabox.width)
            altura = float(ultima_pagina.mediabox.height)

            packet = io.BytesIO()
            can = canvas.Canvas(packet, pagesize=(largura, altura))
            width_img, height_img = 160, 60
            x = max(10, largura - width_img - 40)
            y = 40
            can.drawImage(
                caminho_assinatura_png, x, y,
                width=width_img, height=height_img,
                mask="auto", preserveAspectRatio=True,
            )
            can.setFont("Helvetica-Bold", 8)
            can.drawString(x, y - 10, f"Assinado por: {nome_usuario}")
            can.drawString(x, y - 20, f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
            can.save()

            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            # A mesclagem modifica somente a pagina clonada no writer.
            ultima_pagina.merge_page(overlay_pdf.pages[0], over=True)

            # Grava em um arquivo unico temporario, no mesmo diretorio, sem
            # sobrescrever o original se houver falha no carimbo ou na gravacao.
            import tempfile
            pasta = os.path.dirname(os.path.abspath(caminho_doc))
            fd, temp_path = tempfile.mkstemp(
                prefix=".carimbo_stang_", suffix=".pdf", dir=pasta
            )
            with os.fdopen(fd, "wb") as f_out:
                writer.write(f_out)

            with open(temp_path, "rb") as f_teste:
                saida = PdfReader(f_teste)
                if len(saida.pages) != len(reader.pages):
                    raise ValueError("O PDF carimbado ficou com numero incorreto de paginas.")

            # So substitui o original apos gravar e validar o PDF de saida.
            os.replace(temp_path, caminho_doc)
            temp_path = None
            repositorio.registrar_documento(caminho_doc)
            return True
        except Exception as e:
            st.error(f"Erro ao aplicar carimbo no PDF: {e}")
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    elif ext in [".png", ".jpg", ".jpeg"]:
        try:
            doc_img = Image.open(caminho_doc).convert("RGBA")
            ass_img = Image.open(caminho_assinatura_png).convert("RGBA")
            
            ass_width = int(doc_img.width * 0.25)
            w_percent = (ass_width / float(ass_img.width))
            ass_height = int((float(ass_img.height) * float(w_percent)))
            ass_img = ass_img.resize((ass_width, ass_height), Image.Resampling.LANCZOS)
            
            pos_x = doc_img.width - ass_width - 20
            pos_y = doc_img.height - ass_height - 20
            
            doc_img.paste(ass_img, (pos_x, pos_y), ass_img)
            doc_img.convert("RGB").save(caminho_doc)
            repositorio.registrar_documento(caminho_doc)
            return True
        except Exception as e:
            st.error(f"Erro ao aplicar carimbo na Imagem: {e}")
            return False
            
    return True

# --- FUNÇÃO PARA ENVIO REAL DE E-MAIL VIA SMTP ---
def enviar_email_real(destinatario, assunto, corpo, anexos, config):
    try:
        remetente = config.get("Email_Remetente", "")
        senha_app = config.get("Senha_App", "")
        servidor_smtp = config.get("Servidor_SMTP", "smtp.gmail.com")
        porta_smtp = int(config.get("Porta_SMTP", "587"))
        nome_remetente = config.get("Nome_Remetente", "Intranet Stang")
        
        if not remetente or not senha_app:
            return False, "E-mail remetente ou Senha de App não configurados para este usuário na tela de Login!"
            
        msg = MIMEMultipart()
        msg['From'] = f"{nome_remetente} <{remetente}>"
        msg['To'] = destinatario
        msg['Subject'] = assunto
        
        msg.attach(MIMEText(corpo, 'plain'))
        
        for file_item in anexos:
            if isinstance(file_item, tuple):
                file_bytes, filename = file_item
                part = MIMEBase("application", "pdf" if str(filename).lower().endswith(".pdf") else "octet-stream")
                part.set_payload(file_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=os.path.basename(str(filename)) or "documento.pdf")
                msg.attach(part)
            elif file_item and documento_disponivel(file_item):
                with open(file_item, "rb") as attachment:
                    part = MIMEBase("application", "pdf" if str(file_item).lower().endswith(".pdf") else "octet-stream")
                    part.set_payload(attachment.read())
                encoders.encode_base64(part)
                filename = os.path.basename(file_item)
                part.add_header("Content-Disposition", "attachment", filename=filename or "documento.pdf")
                msg.attach(part)
                
        server = smtplib.SMTP(servidor_smtp, porta_smtp)
        server.starttls()
        server.login(remetente, senha_app)
        text = msg.as_string()
        server.sendmail(remetente, destinatario, text)
        server.quit()
        
        return True, "E-mail enviado com sucesso!"
    except Exception as e:
        return False, f"Falha no envio de e-mail: {str(e)}"

# --- SISTEMA DE AUTENTICAÇÃO NA TELA DE LOGIN ---
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.usuario = ""

if not st.session_state.autenticado:
    st.markdown("<br>", unsafe_allow_html=True)
    col_l1, col_l2, col_l3 = st.columns([1, 1.5, 1])
    with col_l2:
        if os.path.exists("logo.png"):
            with open("logo.png", "rb") as img_file:
                logo_b64_login = base64.b64encode(img_file.read()).decode()
            st.markdown(f'<div style="text-align: center;"><img src="data:image/png;base64,{logo_b64_login}" width="280"></div>', unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>🔐 Acesso Restrito - Intranet Stang</h2>", unsafe_allow_html=True)
        
        with st.form("form_login"):
            usuario_input = st.text_input("Usuário").strip()
            senha_input = st.text_input("Senha", type="password")
            btn_login = st.form_submit_button("Entrar no Sistema", use_container_width=True)
            
            if btn_login:
                df_u = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
                user_row = df_u[df_u["Usuario"].str.lower() == usuario_input.lower()]
                
                if user_row.empty:
                    st.error("Usuário não encontrado!")
                else:
                    row = user_row.iloc[0]
                    is_admin_db = str(row.get("Admin", "Não")) == "Sim"
                    senha_valida = verificar_senha(senha_input, row["Senha"])
                    
                    if not senha_valida:
                        st.error("Senha incorreta!")
                    else:
                        validade = str(row["Validade"])
                        acesso_liberado = True
                        if validade != "Vitalício":
                            try:
                                data_validade = datetime.strptime(validade, "%Y-%m-%d").date()
                                if datetime.now().date() > data_validade:
                                    acesso_liberado = False
                                    st.error(f"Acesso expirado em {data_validade.strftime('%d/%m/%Y')}!")
                            except:
                                pass
                                
                        if acesso_liberado:
                            st.session_state.autenticado = True
                            st.session_state.usuario = row["Usuario"]
                            st.success("Login efetuado com sucesso! Carregando...")
                            st.rerun()
                            
        with st.expander("🔑 Gerenciar Usuários, Assinatura Digital e E-mail"):
            senha_master_input = st.text_input("Insira uma senha de administrador", type="password", key="master_unlock")
            
            df_users_check_master = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
            # Gerência GLOBAL de usuários só com a senha de um administrador
            # autorizado no cadastro a TODAS as filiais ativas.
            # Um administrador local não pode atribuir filiais a si mesmo
            # (nem redefinir senhas de administradores de outras filiais).
            filiais_do_cadastro = repositorio.listar_filiais()
            admins_elegiveis = df_users_check_master[df_users_check_master["Admin"] == "Sim"]
            libera_gestao = bool(senha_master_input) and any(
                pode_gerir_todas_as_filiais(row.to_dict(), filiais_do_cadastro)
                and verificar_senha(senha_master_input, row["Senha"])
                for _, row in admins_elegiveis.iterrows()
            )
            
            if libera_gestao:
                st.success("Painel de gestão de usuários liberado:")
                
                aba_ges1, aba_ges2 = st.tabs(["➕ Cadastrar Novo Usuário", "✏️ Editar / ✍️ Assinatura PNG / 📧 Config. E-mail / 🗑️ Excluir"])
                
                with aba_ges1:
                    with st.form("form_gestao_login"):
                        st.markdown("<b>Novo Usuário a Cadastrar:</b>", unsafe_allow_html=True)
                        n_login = st.text_input("Login do Novo Usuário").strip()
                        n_senha = st.text_input("Senha do Novo Usuário", type="password")
                        n_val = st.selectbox("Validade", ["Vitalício", "Definir Data Limite"])
                        
                        n_data = datetime.now().date() + timedelta(days=30)
                        if n_val == "Definir Data Limite":
                            n_data = st.date_input("Data Limite de Acesso")
                            
                        n_admin_opt = st.selectbox("Perfil de Administrador", ["Não", "Sim"])
                            
                        st.markdown("<b>Permissões de Acesso aos Menus:</b>", unsafe_allow_html=True)
                        n_permissoes = st.multiselect(
                            "Selecione as opções que este usuário poderá acessar:",
                            options=TODOS_MENUS,
                            default=["📝 Nova O.S.", "🛒 Solicitações de Compras"]
                        )
                        
                        n_filiais = st.multiselect(
                            "Filiais autorizadas para este usuário",
                            options=[f["codigo"] for f in repositorio.listar_filiais()],
                            default=["MANDALLA"],
                            key="novo_usuario_filiais"
                        )
                        st.markdown("---")
                        st.markdown("<b>📧 Configuração Individual de E-mail (SMTP):</b>", unsafe_allow_html=True)
                        n_email = st.text_input("E-mail do Usuário", value="")
                        n_senha_app = st.text_input("Senha de App / Token SMTP", type="password", key="n_senha_app")
                        c_smtp1, c_smtp2 = st.columns(2)
                        n_servidor_smtp = c_smtp1.text_input("Servidor SMTP", value="smtp.gmail.com")
                        n_porta_smtp = c_smtp2.text_input("Porta SMTP", value="587")
                            
                        btn_cad_login = st.form_submit_button("Cadastrar Novo Usuário")
                        
                        if btn_cad_login:
                            if not n_login or not n_senha:
                                st.error("Preencha o login e a senha do novo usuário!")
                            elif not n_permissoes:
                                st.error("Selecione pelo menos uma permissão de menu para o usuário.")
                            else:
                                df_u = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
                                if n_login.lower() in df_u["Usuario"].str.lower().values:
                                    st.error("Este usuário já existe!")
                                else:
                                    val_str = "Vitalício" if n_val == "Vitalício" else str(n_data)
                                    perm_str = ",".join(n_permissoes)
                                    if not n_filiais:
                                        st.error("Selecione uma filial.")
                                        st.stop()
                                    novo_reg = {
                                        "Usuario": n_login, 
                                        "Senha": gerar_hash(n_senha), 
                                        "Validade": val_str,
                                        "Permissoes": perm_str,
                                        "Admin": n_admin_opt,
                                        "Assinatura_PNG": "None",
                                        "Email_Usuario": n_email,
                                        "Senha_App_Email": n_senha_app,
                                        "Servidor_SMTP": n_servidor_smtp,
                                        "Porta_SMTP": n_porta_smtp,
                                        "Filiais": ",".join(n_filiais)
                                    }
                                    df_u = pd.concat([df_u, pd.DataFrame([novo_reg])], ignore_index=True)
                                    repositorio.salvar_csv(df_u, ARQUIVO_USERS)
                                    st.success(f"Usuário '{n_login}' cadastrado com sucesso!")
                                    st.rerun()

                with aba_ges2:
                    df_u_atual = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
                    st.dataframe(df_u_atual[["Usuario", "Email_Usuario", "Validade", "Admin", "Assinatura_PNG"]], use_container_width=True)
                    
                    lista_usuarios_edit = df_u_atual["Usuario"].tolist()
                    if lista_usuarios_edit:
                        st.markdown("---")
                        user_selecionado = st.selectbox("Selecione o usuário para Editar / Configurar", lista_usuarios_edit)
                        row_u_edit = df_u_atual[df_u_atual["Usuario"] == user_selecionado].iloc[0]
                        
                        with st.form("form_editar_usuario"):
                            st.subheader(f"Editando Usuário: {user_selecionado}")
                            edit_senha = st.text_input("Nova Senha (deixe vazio para manter)", value="", type="password")
                            
                            val_atual_str = str(row_u_edit["Validade"])
                            is_vitalicio = val_atual_str == "Vitalício"
                            edit_val_tipo = st.selectbox("Validade", ["Vitalício", "Definir Data Limite"], index=0 if is_vitalicio else 1)
                            
                            edit_data = datetime.now().date() + timedelta(days=30)
                            if not is_vitalicio:
                                try:
                                    edit_data = datetime.strptime(val_atual_str, "%Y-%m-%d").date()
                                except:
                                    pass
                            if edit_val_tipo == "Definir Data Limite":
                                edit_data = st.date_input("Nova Data Limite", value=edit_data)
                                
                            admin_atual_str = str(row_u_edit.get("Admin", "Não"))
                            edit_admin_opt = st.selectbox("Perfil de Administrador", ["Não", "Sim"], index=0 if admin_atual_str != "Sim" else 1)
                                
                            perm_atuais_list = [p.strip() for p in str(row_u_edit["Permissoes"]).split(",") if p.strip() in TODOS_MENUS]
                            edit_permissoes = st.multiselect(
                                "Permissões de Acesso:",
                                options=TODOS_MENUS,
                                default=perm_atuais_list
                            )
                            
                            edit_filiais = st.multiselect(
                                "Filiais autorizadas para este usuário",
                                options=[f["codigo"] for f in repositorio.listar_filiais()],
                                default=[f for f in str(row_u_edit.get("Filiais", "MANDALLA")).split(",") if f in [x["codigo"] for x in repositorio.listar_filiais()]],
                                key=f"editar_usuario_filiais_{user_selecionado}"
                            )
                            st.markdown("---")
                            st.markdown("<b>📧 Configuração Individual de E-mail (SMTP):</b>", unsafe_allow_html=True)
                            edit_email = st.text_input("E-mail do Usuário", value=str(row_u_edit.get("Email_Usuario", "")))
                            edit_senha_app = st.text_input("Senha de App / Token SMTP", value=str(row_u_edit.get("Senha_App_Email", "")), type="password")
                            ce_smtp1, ce_smtp2 = st.columns(2)
                            edit_servidor_smtp = ce_smtp1.text_input("Servidor SMTP Host", value=str(row_u_edit.get("Servidor_SMTP", "smtp.gmail.com")))
                            edit_porta_smtp = ce_smtp2.text_input("Porta SMTP", value=str(row_u_edit.get("Porta_SMTP", "587")))

                            st.markdown("---")
                            st.markdown("✍️ **Assinatura Digital em PNG:**")
                            path_ass_atual = str(row_u_edit.get("Assinatura_PNG", "None"))
                            if path_ass_atual != "None" and documento_disponivel(path_ass_atual):
                                st.image(path_ass_atual, caption=f"Assinatura Atual de {user_selecionado}", width=200)
                            else:
                                st.caption("Nenhuma assinatura digital cadastrada.")
                                
                            up_assinatura_png = st.file_uploader(f"Upload da Assinatura PNG para {user_selecionado}", type=["png"])
                            
                            btn_salvar_edicao = st.form_submit_button("💾 Salvar Alterações")
                            
                            if btn_salvar_edicao:
                                if not edit_permissoes:
                                    st.error("Selecione pelo menos uma permissão de menu.")
                                else:
                                    novo_val_str = "Vitalício" if edit_val_tipo == "Vitalício" else str(edit_data)
                                    nova_perm_str = ",".join(edit_permissoes)
                                    
                                    path_salvo = path_ass_atual
                                    if up_assinatura_png is not None:
                                        file_ass_name = f"assinatura_{user_selecionado.lower()}.png"
                                        path_salvo = repositorio.caminho_documento(file_ass_name, assinatura=True)
                                        with open(path_salvo, "wb") as f:
                                            f.write(up_assinatura_png.getbuffer())
                                        repositorio.registrar_documento(path_salvo)
                                            
                                    if edit_senha:
                                        df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Senha"] = gerar_hash(edit_senha)
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Email_Usuario"] = edit_email
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Senha_App_Email"] = edit_senha_app
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Servidor_SMTP"] = edit_servidor_smtp
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Porta_SMTP"] = edit_porta_smtp
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Validade"] = novo_val_str
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Admin"] = edit_admin_opt
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Permissoes"] = nova_perm_str
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Filiais"] = ",".join(edit_filiais)
                                    df_u_atual.loc[df_u_atual["Usuario"] == user_selecionado, "Assinatura_PNG"] = path_salvo
                                    
                                    repositorio.salvar_csv(df_u_atual, ARQUIVO_USERS)
                                    st.success(f"Usuário '{user_selecionado}' atualizado!")
                                    st.rerun()

                        total_admins = len(df_u_atual[df_u_atual["Admin"] == "Sim"])
                        is_este_admin = str(row_u_edit.get("Admin")) == "Sim"
                        
                        if total_admins <= 1 and is_este_admin:
                            st.info("⚠️ Este é o único administrador ativo.")
                        else:
                            if st.button(f"🗑️ Excluir Usuário '{user_selecionado}'", type="primary"):
                                df_u_atual = df_u_atual[df_u_atual["Usuario"] != user_selecionado]
                                repositorio.salvar_csv(df_u_atual, ARQUIVO_USERS)
                                st.success(f"Usuário '{user_selecionado}' removido!")
                                st.rerun()
            elif senha_master_input != "":
                st.error("Credencial inválida ou administrador sem autorização para gerenciar todas as filiais.")
                    
    st.stop()

# --- DETERMINAR MENUS PERMITIDOS ---
df_users_check = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
user_logado_row = df_users_check[df_users_check["Usuario"].str.lower() == st.session_state.usuario.lower()]

is_user_admin = False
if not user_logado_row.empty:
    is_user_admin = str(user_logado_row.iloc[0].get("Admin", "Não")) == "Sim"

if user_logado_row.empty:
    st.session_state.autenticado = False
    st.error("Usuário não localizado no cadastro atual. Entre novamente.")
    st.stop()

if pd.notna(user_logado_row.iloc[0].get("Permissoes")) and str(user_logado_row.iloc[0]["Permissoes"]) != "":
    menus_disponiveis = [m.strip() for m in str(user_logado_row.iloc[0]["Permissoes"]).split(",") if m.strip() in TODOS_MENUS]
else:
    menus_disponiveis = TODOS_MENUS

if is_user_admin and "✍️ Painel de Assinaturas" not in menus_disponiveis:
    menus_disponiveis.append("✍️ Painel de Assinaturas")

# A filial depende EXCLUSIVAMENTE do vínculo registrado em Configurações.
# Administrador também não recebe acesso automático a outras bases.
filiais_ativas = repositorio.listar_filiais()
nomes_filiais = {item["codigo"]: item["nome"] for item in filiais_ativas}
filiais_autorizadas = filiais_permitidas(user_logado_row.iloc[0].to_dict(), filiais_ativas)
if not filiais_autorizadas:
    st.error("Seu usuário não possui uma filial ativa vinculada. Contate um administrador.")
    st.stop()
if st.session_state.get("filial_ativa") not in filiais_autorizadas:
    st.session_state.filial_ativa = filiais_autorizadas[0]
if not is_user_admin:
    menus_disponiveis = [m for m in menus_disponiveis if m not in ("🏢 Filiais e Backup", "👥 Gestão de RH")]
if is_user_admin:
    for m in ("🧰 Ferramentaria", "👥 Gestão de RH", "🏢 Filiais e Backup"):
        if m not in menus_disponiveis:menus_disponiveis.append(m)

# --- BARRA LATERAL (MENU) ---
# Ícones do cartão de acesso embutidos para funcionar sem arquivos adicionais.
_ICONES_CONTA_3D = {
    'user': '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',
    'role': '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',
    'branch': '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',
}

def _icone_conta_3d(tipo: str) -> str:
    return f'<img class="stang-id-icon" src="data:image/png;base64,{_ICONES_CONTA_3D[tipo]}" alt="" />'

with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)

    # Identificação visual apenas: as permissões reais seguem no banco.
    _nome_visivel = htmlmod.escape(str(st.session_state.usuario), quote=True)
    _perfil_visivel = "Administrador" if is_user_admin else "Usuário"
    st.markdown(
        '<div class="stang-id-card">'
        '<div class="stang-id-row">' + _icone_conta_3d("user") +
        '<div><span class="stang-id-label">Logado como</span>'
        f'<strong class="stang-id-value">{_nome_visivel}</strong></div></div>'
        '<div class="stang-id-row">' + _icone_conta_3d("role") +
        '<div><span class="stang-id-label">Perfil</span>'
        f'<strong class="stang-id-value">{_perfil_visivel}</strong></div></div>'
        '</div>', unsafe_allow_html=True
    )
    st.markdown(
        '<div class="stang-filial-label">'
        + _icone_conta_3d("branch") + '<strong>Filial de trabalho</strong></div>',
        unsafe_allow_html=True,
    )
    filial_selecionada = st.selectbox(
        "Filial de trabalho",
        options=filiais_autorizadas,
        index=filiais_autorizadas.index(st.session_state.filial_ativa),
        format_func=lambda codigo: nomes_filiais.get(codigo, codigo),
        label_visibility="collapsed",
    )
    if filial_selecionada != st.session_state.filial_ativa:
        st.session_state.filial_ativa = filial_selecionada
        st.session_state.pop("carrinho_compras", None)
        st.session_state.pop("backup_zip", None)
        st.rerun()
    # Alternância de aparência por sessão, preservada no parâmetro ?tema= da URL.
    tema_stang.controle_tema()
    st.markdown("---")
    if menus_disponiveis:
        # Ícones 3D apenas visuais: as opções originais são preservadas para
        # permissões, comparações de menu, backups e navegação.
        try:
            import menu_icones_3d as ui3d
            ui3d.aplicar_estilo(menus_disponiveis)
            _rotulo_menu = ui3d.rotulo_menu
        except (ImportError, OSError, ValueError):
            # Nunca remove os ícones padrão se houver falha de importação.
            _rotulo_menu = str
        menu = st.radio("Navegação Principal", menus_disponiveis, format_func=_rotulo_menu)
    else:
        st.warning("⚠️ Você não possui permissão para acessar nenhum menu.")
        menu = None
    
    st.markdown("---")
    
    if st.button("🔄 Atualizar Dados", use_container_width=True):
        st.rerun()
        
    if st.button("🚪 Sair / Logout", use_container_width=True):
        st.session_state.autenticado = False
        st.session_state.usuario = ""
        st.session_state.pop("filial_ativa", None)
        st.session_state.pop("backup_contas_encrypted", None)
        st.session_state.pop("backup_total_2026", None)
        st.session_state.pop("pre_restore_total_v205", None)
        st.rerun()
    st.info(f"🏢 {nomes_filiais[st.session_state.filial_ativa]}\nStatus: Conectado 🟢")
    st.caption("Versão 2.0.5.7")
    
    st.markdown("<div style='text-align: left; font-style: italic; font-size: 11px; opacity: 0.7; margin-top: 25px;'><i>By: TS tech</i></div>", unsafe_allow_html=True)

if menu is not None:
    # Os anexos permanecem no Supabase, mas só são recuperados ao serem usados.
    # A sincronização de TODOS os arquivos em cada interação deixava a aplicação lenta.
    if menu == "🧰 Ferramentaria":
        import mod_ferramentaria
        mod_ferramentaria.renderizar(st.session_state.filial_ativa, st.session_state.usuario, is_user_admin)
    elif menu == "👥 Gestão de RH" and is_user_admin:
        import mod_rh
        mod_rh.renderizar(st.session_state.filial_ativa, st.session_state.usuario, is_user_admin)
    elif menu == "🏢 Filiais e Backup" and is_user_admin:
        import mod_filiais_backup
        mod_filiais_backup.renderizar(st.session_state.filial_ativa, st.session_state.usuario)
    # --- TELA 1: CRIAR NOVA O.S. ---
    elif menu == "📝 Nova O.S.":
        ui3d.titulo("Abertura de Ordem de Serviço (O.S.)", "nova_os")
        st.markdown("Preencha os dados abaixo para registrar a solicitação de manutenção.")
        
        with st.form("form_nova_os", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                solicitante = st.text_input("Nome do Solicitante *")
                setor = st.selectbox("Setor", ["OPERAÇÃO", "MANUTENÇÃO", "PORTARIA", "ADMINISTRATIVO", "TI"])
            with col2:
                equipamento = st.text_input("Equipamento / Local")
                tipo = st.selectbox("Tipo de Manutenção", ["CORRETIVA", "PREVENTIVA", "PREDITIVA"])
            with col3:
                prioridade = st.selectbox("Prioridade", ["BAIXA", "MÉDIA", "ALTA", "URGENTE"])
                status = st.selectbox("Status Inicial", ["Em Aberto", "Em Andamento", "Finalizada"])
                
            descricao = st.text_area("Descrição Detalhada do Problema *")
            
            submit = st.form_submit_button("💾 Salvar Ordem de Serviço")
            
            if submit:
                if not solicitante or not descricao:
                    st.error("Por favor, preencha o Solicitante e a Descrição do Problema.")
                else:
                    df = carregar_banco_os()
                    novo_id = int(df["ID"].max() + 1) if not df.empty and df["ID"].max() > 0 else 1330
                    
                    finalizado_por_val = st.session_state.usuario.upper() if status == "Finalizada" else ""
                    data_termino_val = datetime.now().strftime("%d/%m/%Y") if status == "Finalizada" else ""

                    nova_linha = {
                        "ID": str(novo_id),
                        "Data_Criacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "Solicitante": solicitante.upper(),
                        "Setor": setor.upper(),
                        "Equipamento": equipamento.upper() if equipamento else "NÃO INFORMADO",
                        "Tipo_Manutencao": tipo.upper(),
                        "Prioridade": prioridade.upper(),
                        "Descricao": descricao.upper(),
                        "Solucao": "ATENDIDO E FINALIZADO" if status == "Finalizada" else "EM ANDAMENTO",
                        "Itens_Trocados": "NENHUM",
                        "finalizado_por": finalizado_por_val,
                        "Data_Termino": data_termino_val,
                        "Status": status
                    }
                    df = pd.concat([df, pd.DataFrame([nova_linha])], ignore_index=True)
                    repositorio.salvar_csv(df, ARQUIVO_OS)
                    st.success(f"Ordem de Serviço #{novo_id} gerada com sucesso!")
                    st.rerun()

        df_os_view = carregar_banco_os()
        if not df_os_view.empty:
            dias_prioridade_map = {"URGENTE": 1, "ALTA": 5, "MÉDIA": 15, "MEDIA": 15, "BAIXA": 30}
            
            def calcular_vencimento_os_view(row):
                try:
                    prio = str(row['Prioridade']).upper()
                    dias = dias_prioridade_map.get(prio, 30)
                    dt_criacao = pd.to_datetime(str(row['Data_Criacao']).split(" ")[0], format='%d/%m/%Y', errors='coerce')
                    if pd.isna(dt_criacao):
                        dt_criacao = pd.to_datetime(str(row['Data_Criacao']), errors='coerce')
                    if pd.isna(dt_criacao):
                        return datetime.now().date()
                    return (dt_criacao + timedelta(days=dias)).date()
                except:
                    return datetime.now().date()

            def determinar_status_prazo_view(row):
                status_atual = str(row['Status']).lower()
                if "finalizada" in status_atual or "concluída" in status_atual:
                    return "Finalizada 🔵"
                venc = row['Prazo_Limite']
                hoje = datetime.now().date()
                if hoje > venc:
                    return "Vencida 🔴"
                else:
                    return "No Prazo 🟢"

            df_os_view['Prazo_Limite'] = df_os_view.apply(calcular_vencimento_os_view, axis=1)
            df_os_view['Status_Prazo'] = df_os_view.apply(determinar_status_prazo_view, axis=1)

            cols_exibicao = ["ID", "Data_Criacao", "Solicitante", "Setor", "Prioridade", "Prazo_Limite", "Status_Prazo", "Status", "Equipamento", "Descricao", "finalizado_por"]
            st.dataframe(df_os_view[cols_exibicao].sort_values(by="ID", ascending=False), use_container_width=True)

    # --- TELA 2: GERENCIAR O.S. ---
    elif menu == "📋 Gerenciar O.S.":
        ui3d.titulo("Painel de Controle e Gestão de O.S.", "gerenciar_os")
        df = carregar_banco_os()
        
        if df.empty:
            st.info("Nenhuma O.S. registrada no momento.")
        else:
            aba_os_manag1, aba_os_manag2 = st.tabs([ui3d.aba("Gerenciar O.S.", "gerenciar_os"), ui3d.aba("Enviar O.S. por E-mail", "assinaturas")])
            
            # --- ABA 1: GERENCIAR O.S. ---
            with aba_os_manag1:
                dias_prioridade_map = {"URGENTE": 1, "ALTA": 5, "MÉDIA": 15, "MEDIA": 15, "BAIXA": 30}
                
                def calcular_vencimento_os(row):
                    try:
                        prio = str(row['Prioridade']).upper()
                        dias = dias_prioridade_map.get(prio, 30)
                        dt_criacao = pd.to_datetime(str(row['Data_Criacao']).split(" ")[0], format='%d/%m/%Y', errors='coerce')
                        if pd.isna(dt_criacao):
                            dt_criacao = pd.to_datetime(str(row['Data_Criacao']), errors='coerce')
                        if pd.isna(dt_criacao):
                            return datetime.now().date()
                        return (dt_criacao + timedelta(days=dias)).date()
                    except:
                        return datetime.now().date()

                def determinar_status_prazo(row):
                    status_atual = str(row['Status']).lower()
                    if "finalizada" in status_atual or "concluída" in status_atual:
                        return "Finalizada 🔵"
                    venc = row['Prazo_Limite']
                    hoje = datetime.now().date()
                    if hoje > venc:
                        return "Vencida 🔴"
                    else:
                        return "No Prazo 🟢"

                df['Prazo_Limite'] = df.apply(calcular_vencimento_os, axis=1)
                df['Status_Prazo'] = df.apply(determinar_status_prazo, axis=1)

                c1, c2, c3 = st.columns(3)
                with c1:
                    filtro_status = st.selectbox("Filtrar por Status", ["Todos"] + list(df["Status"].unique()))
                with c2:
                    filtro_setor = st.selectbox("Filtrar por Setor", ["Todos"] + list(df["Setor"].unique()))
                with c3:
                    lista_finalizado_por_opts = ["Todos"] + sorted([x for x in df["finalizado_por"].dropna().unique().tolist() if str(x).strip() != ""])
                    filtro_finalizador = st.selectbox("Filtrar por Executante", lista_finalizado_por_opts)
                    
                df_filtered = df.copy()
                if filtro_status != "Todos":
                    df_filtered = df_filtered[df_filtered["Status"] == filtro_status]
                if filtro_setor != "Todos":
                    df_filtered = df_filtered[df_filtered["Setor"] == filtro_setor]
                if filtro_finalizador != "Todos":
                    df_filtered = df_filtered[df_filtered["finalizado_por"] == filtro_finalizador]
                    
                cols_exibicao = ["ID", "Data_Criacao", "Solicitante", "Setor", "Prioridade", "Prazo_Limite", "Status_Prazo", "Status", "Equipamento", "Solucao", "Itens_Trocados", "finalizado_por"]
                st.dataframe(df_filtered[cols_exibicao].sort_values(by="ID", ascending=False), use_container_width=True)
                
                st.markdown("---")
                
                aba_os_ges1, aba_os_ges2 = st.tabs([ui3d.aba("Editar / Finalizar O.S.", "gerenciar_os"), ui3d.aba("Excluir O.S.", "gerenciar_os")])
                
                with aba_os_ges1:
                    ids_os_lista = sorted(df["ID"].tolist(), reverse=True)
                    if ids_os_lista:
                        os_selecionada_id = st.selectbox("Selecione o ID da O.S.", ids_os_lista)
                        row_edit_os = df[df["ID"] == os_selecionada_id].iloc[0]
                        
                        with st.form("form_editar_os_detalhes"):
                            st.subheader(f"Editando Ordem de Serviço #{os_selecionada_id}")
                            
                            col_e1, col_e2, col_e3 = st.columns(3)
                            with col_e1:
                                edit_solicitante = st.text_input("Solicitante", value=str(row_edit_os["Solicitante"]))
                                edit_setor = st.text_input("Setor", value=str(row_edit_os["Setor"]))
                            with col_e2:
                                edit_equip = st.text_input("Equipamento", value=str(row_edit_os["Equipamento"]))
                                
                                prio_atual = str(row_edit_os["Prioridade"]).upper()
                                idx_prio = ["BAIXA", "MÉDIA", "ALTA", "URGENTE"].index(prio_atual) if prio_atual in ["BAIXA", "MÉDIA", "ALTA", "URGENTE"] else 0
                                edit_prio = st.selectbox("Prioridade", ["BAIXA", "MÉDIA", "ALTA", "URGENTE"], index=idx_prio)
                            with col_e3:
                                status_atual_str = str(row_edit_os["Status"])
                                status_opcoes = ["Em Aberto", "Em Andamento", "Finalizada"]
                                idx_st = status_opcoes.index(status_atual_str) if status_atual_str in status_opcoes else 0
                                edit_status = st.selectbox("Status", status_opcoes, index=idx_st)
                                
                                finalizado_por_ant = str(row_edit_os["finalizado_por"]) if pd.notna(row_edit_os["finalizado_por"]) and str(row_edit_os["finalizado_por"]).strip() != "" else st.session_state.usuario.upper()
                                edit_finalizado_por = st.text_input("Responsável pelo serviço", value=finalizado_por_ant)

                            edit_desc = st.text_area("Descrição do Problema", value=str(row_edit_os["Descricao"]))
                            
                            col_e4, col_e5 = st.columns(2)
                            with col_e4:
                                sol_ant = str(row_edit_os["Solucao"]) if pd.notna(row_edit_os["Solucao"]) else ""
                                edit_solucao = st.text_area("Solução Aplicada", value=sol_ant)
                            with col_e5:
                                itens_ant = str(row_edit_os["Itens_Trocados"]) if pd.notna(row_edit_os["Itens_Trocados"]) else ""
                                edit_itens = st.text_area("Itens / Peças Trocadas", value=itens_ant)
                                
                            col_b_f1, col_b_f2 = st.columns(2)
                            with col_b_f1:
                                btn_salvar_alt = st.form_submit_button("💾 Salvar Alterações")
                            with col_b_f2:
                                btn_finalizar_direto = st.form_submit_button("✅ Finalizar O.S. Imediatamente")
                                
                            if btn_salvar_alt:
                                df.loc[df["ID"] == os_selecionada_id, "Solicitante"] = edit_solicitante.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Setor"] = edit_setor.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Equipamento"] = edit_equip.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Prioridade"] = edit_prio.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Status"] = edit_status
                                df.loc[df["ID"] == os_selecionada_id, "Descricao"] = edit_desc.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Solucao"] = edit_solucao.upper()
                                df.loc[df["ID"] == os_selecionada_id, "Itens_Trocados"] = edit_itens.upper()
                                df.loc[df["ID"] == os_selecionada_id, "finalizado_por"] = edit_finalizado_por.upper()
                                
                                if edit_status == "Finalizada":
                                    df.loc[df["ID"] == os_selecionada_id, "Data_Termino"] = datetime.now().strftime("%d/%m/%Y")
                                else:
                                    df.loc[df["ID"] == os_selecionada_id, "Data_Termino"] = ""
                                
                                df_to_save = df.drop(columns=["Prazo_Limite", "Status_Prazo"], errors="ignore")
                                repositorio.salvar_csv(df_to_save, ARQUIVO_OS)
                                st.success(f"Ordem de Serviço #{os_selecionada_id} atualizada com sucesso!")
                                st.rerun()
                                
                            if btn_finalizar_direto:
                                df.loc[df["ID"] == os_selecionada_id, "Status"] = "Finalizada"
                                df.loc[df["ID"] == os_selecionada_id, "Data_Termino"] = datetime.now().strftime("%d/%m/%Y")
                                df.loc[df["ID"] == os_selecionada_id, "finalizado_por"] = edit_finalizado_por.upper()
                                if str(df.loc[df["ID"] == os_selecionada_id, "Solucao"].values[0]) in ["", "EM ANDAMENTO", "nan"]:
                                    df.loc[df["ID"] == os_selecionada_id, "Solucao"] = "ATENDIDO E FINALIZADO"
                                    
                                df_to_save = df.drop(columns=["Prazo_Limite", "Status_Prazo"], errors="ignore")
                                repositorio.salvar_csv(df_to_save, ARQUIVO_OS)
                                st.success(f"Ordem de Serviço #{os_selecionada_id} finalizada com sucesso!")
                                st.rerun()
                
                with aba_os_ges2:
                    col_del1, col_del2 = st.columns([2, 1])
                    with col_del1:
                        os_para_excluir = st.selectbox("Selecione o ID da O.S. para Exclusão", df["ID"].tolist(), key="select_del_os")
                    with col_del2:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("🗑️ Excluir O.S. Selecionada", type="primary"):
                            df = df[df["ID"] != os_para_excluir]
                            df_to_save = df.drop(columns=["Prazo_Limite", "Status_Prazo"], errors="ignore")
                            repositorio.salvar_csv(df_to_save, ARQUIVO_OS)
                            st.success(f"Ordem de Serviço #{os_para_excluir} excluída!")
                            st.rerun()

            # --- ABA 2: ENVIAR O.S. POR E-MAIL ---
            with aba_os_manag2:
                st.markdown("### 📧 Enviar Ordens de Serviço por E-mail")
                
                df_send = df.copy()
                df_send['Dt_Parsed'] = pd.to_datetime(df_send['Data_Criacao'], format='%d/%m/%Y %H:%M', errors='coerce')
                if df_send['Dt_Parsed'].isna().all():
                    df_send['Dt_Parsed'] = pd.to_datetime(df_send['Data_Criacao'], errors='coerce')
                
                df_send['Ano'] = df_send['Dt_Parsed'].dt.year
                df_send['Mes_Ano'] = df_send['Dt_Parsed'].dt.strftime('%m/%Y')
                df_send['Dia'] = df_send['Dt_Parsed'].dt.date
                
                # Filtros globais: número, setor e dia, mês, ano
                fg_col1, fg_col2, fg_col3, fg_col4, fg_col5 = st.columns(5)
                
                with fg_col1:
                    num_opts = ["Todos"] + sorted(df_send['ID'].astype(str).unique().tolist(), key=lambda x: int(x) if x.isdigit() else x)
                    f_num = st.selectbox("Número (ID)", num_opts, key="send_f_num")
                with fg_col2:
                    setor_opts = ["Todos"] + sorted([s for s in df_send['Setor'].dropna().unique().tolist() if str(s).strip() != ""])
                    f_setor = st.selectbox("Setor", setor_opts, key="send_f_setor")
                with fg_col3:
                    dias_opts = ["Todos"] + sorted([str(d) for d in df_send['Dia'].dropna().unique().tolist()])
                    f_dia = st.selectbox("Dia Exato", dias_opts, key="send_f_dia")
                with fg_col4:
                    meses_opts = ["Todos"] + sorted([str(m) for m in df_send['Mes_Ano'].dropna().unique().tolist()])
                    f_mes = st.selectbox("Mês/Ano", meses_opts, key="send_f_mes")
                with fg_col5:
                    anos_opts = ["Todos"] + sorted([str(int(a)) for a in df_send['Ano'].dropna().unique() if pd.notna(a)])
                    f_ano = st.selectbox("Ano", anos_opts, key="send_f_ano")
                    
                if f_num != "Todos":
                    df_send = df_send[df_send['ID'].astype(str) == f_num]
                if f_setor != "Todos":
                    df_send = df_send[df_send['Setor'] == f_setor]
                if f_dia != "Todos":
                    df_send = df_send[df_send['Dia'].astype(str) == f_dia]
                if f_mes != "Todos":
                    df_send = df_send[df_send['Mes_Ano'] == f_mes]
                if f_ano != "Todos":
                    df_send = df_send[df_send['Ano'].astype(str) == f_ano]
                    
                st.markdown("---")
                
                col_m_left, col_m_right = st.columns([1.3, 1])
                
                with col_m_left:
                    st.markdown("#### 1. Selecione as Ordens de Serviço")
                    if df_send.empty:
                        st.info("Nenhuma O.S. encontrada para os filtros aplicados.")
                        os_selecionadas_ids = []
                    else:
                        df_send['Selecionar'] = False
                        edited_df = st.data_editor(
                            df_send[["Selecionar", "ID", "Data_Criacao", "Solicitante", "Setor", "Prioridade", "Status"]],
                            hide_index=True,
                            use_container_width=True,
                            key="editor_os_email"
                        )
                        os_selecionadas_ids = edited_df[edited_df['Selecionar'] == True]['ID'].tolist()
                        st.caption(f"Total de O.S. selecionadas: **{len(os_selecionadas_ids)}**")

                with col_m_right:
                    st.markdown("#### 2. Janela de Envio por E-mail")
                    
                    df_u_send = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
                    row_logged_send = df_u_send[df_u_send["Usuario"].str.lower() == st.session_state.usuario.lower()]
                    email_user_default = ""
                    if not row_logged_send.empty:
                        email_user_default = str(row_logged_send.iloc[0].get("Email_Usuario", ""))

                    with st.form("form_janela_envio_os_email"):
                        para_input = st.text_input("Para:", value=email_user_default if email_user_default else "financeiro@stang.com.br")
                        assunto_input = st.text_input("Assunto:", value="Envio de Ordens de Serviço - Intranet Stang")
                        
                        corpo_padrao = "Prezado(a),\n\nSegue(m) em anexo a(s) Ordem(ns) de Serviço solicitada(s) em formato PDF.\n\nAtenciosamente,\nEquipe de Manutenção Stang"
                        mensagem_input = st.text_area("Mensagem:", value=corpo_padrao, height=140)
                        
                        btn_disparar_email = st.form_submit_button("🚀 Enviar E-mail", use_container_width=True)
                        
                        if btn_disparar_email:
                            if not para_input:
                                st.error("Informe o e-mail do destinatário.")
                            elif not os_selecionadas_ids:
                                st.warning("Selecione pelo menos uma Ordem de Serviço na tabela ao lado para enviar.")
                            elif not HAS_PDF_LIBS:
                                st.error("Bibliotecas de PDF (reportlab / pypdf) não estão instaladas no servidor!")
                            else:
                                if row_logged_send.empty:
                                    st.error("Configurações do usuário não foram encontradas.")
                                else:
                                    u_dict = row_logged_send.iloc[0].to_dict()
                                    cfg_mail = {
                                        "Email_Remetente": u_dict.get("Email_Usuario", ""),
                                        "Senha_App": u_dict.get("Senha_App_Email", ""),
                                        "Servidor_SMTP": u_dict.get("Servidor_SMTP", "smtp.gmail.com"),
                                        "Porta_SMTP": u_dict.get("Porta_SMTP", "587"),
                                        "Nome_Remetente": st.session_state.usuario
                                    }
                                    
                                    anexos_os_envio = []
                                    
                                    for os_id_item in os_selecionadas_ids:
                                        row_os_match = df[df["ID"] == os_id_item].iloc[0]
                                        pdf_bytes = gerar_pdf_os(row_os_match)
                                        if pdf_bytes:
                                            nome_arquivo_anexo = f"OS_{os_id_item}.pdf"
                                            anexos_os_envio.append((pdf_bytes, nome_arquivo_anexo))
                                    
                                    sucesso_send, msg_send = enviar_email_real(
                                        destinatario=para_input,
                                        assunto=assunto_input,
                                        corpo=mensagem_input,
                                        anexos=anexos_os_envio,
                                        config=cfg_mail
                                    )
                                    
                                    if sucesso_send:
                                        st.success(f"E-mail enviado com sucesso para {para_input} com o(s) PDF(s) anexado(s)!")
                                    else:
                                        st.error(f"Erro ao enviar e-mail: {msg_send}")

    # --- TELA 3: EMISSÃO E RELATÓRIOS DE O.S. ---
    elif menu == "🖨️ Imprimir O.S.":
        ui3d.titulo("Emissão e Relatórios de O.S.", "imprimir_os")
        df = carregar_banco_os()

        if df.empty:
            st.warning("Não há O.S. cadastradas para impressão.")
        else:
            tab_imp1, tab_imp2 = st.tabs([ui3d.aba("Imprimir O.S.", "imprimir_os"), ui3d.aba("Relatório Geral de O.S.", "dashboard")])

            with tab_imp1:
                lista_os = (
                    df["ID"].astype(str) + " - " +
                    df["Solicitante"].astype(str) + " (" +
                    df["Setor"].astype(str) + ")"
                )
                os_selecionada = st.selectbox("Selecione a O.S. desejada:", lista_os)

                id_selecionado = int(str(os_selecionada).split(" - ")[0])
                os_row = df[df["ID"] == id_selecionado].iloc[0]

                st.markdown("---")
                print_html = gerar_html_os_impressao(os_row)
                components.html(print_html, height=920, scrolling=True)

            with tab_imp2:
                st.subheader("Filtros para o Relatório de Ordens de Serviço (O.S.)")

                df_rel_os = df.copy()
                df_rel_os["Dt_Parsed"] = pd.to_datetime(
                    df_rel_os["Data_Criacao"],
                    format="%d/%m/%Y %H:%M",
                    errors="coerce",
                    dayfirst=True
                )
                if df_rel_os["Dt_Parsed"].isna().all():
                    df_rel_os["Dt_Parsed"] = pd.to_datetime(
                        df_rel_os["Data_Criacao"],
                        errors="coerce",
                        dayfirst=True
                    )

                df_rel_os["Ano"] = df_rel_os["Dt_Parsed"].dt.year
                df_rel_os["Mes"] = df_rel_os["Dt_Parsed"].dt.month
                df_rel_os["Dia"] = df_rel_os["Dt_Parsed"].dt.day
                df_rel_os["Mes_Ano"] = df_rel_os["Dt_Parsed"].dt.strftime("%m/%Y")

                col_f1, col_f2, col_f3, col_f4 = st.columns(4)
                with col_f1:
                    status_opts = ["Todos"] + sorted(
                        [str(x) for x in df_rel_os["Status"].dropna().unique() if str(x).strip()]
                    )
                    filtro_status_rep = st.selectbox("Status", status_opts, key="rep_status")
                with col_f2:
                    anos_opts = ["Todos"] + sorted(
                        [str(int(a)) for a in df_rel_os["Ano"].dropna().unique()],
                        reverse=True
                    )
                    filtro_ano_rep = st.selectbox("Ano", anos_opts, key="rep_ano")
                with col_f3:
                    meses_opts = ["Todos"] + sorted(
                        [str(x) for x in df_rel_os["Mes_Ano"].dropna().unique()],
                        reverse=True
                    )
                    filtro_mes_rep = st.selectbox("Mês/Ano", meses_opts, key="rep_mes")
                with col_f4:
                    dias_opts = ["Todos"] + sorted(
                        [str(x) for x in df_rel_os["Dt_Parsed"].dropna().dt.strftime("%d/%m/%Y").unique()],
                        reverse=True
                    )
                    filtro_dia_rep = st.selectbox("Dia Exato", dias_opts, key="rep_dia")

                df_f_rep = df_rel_os.copy()
                if filtro_status_rep != "Todos":
                    df_f_rep = df_f_rep[df_f_rep["Status"].astype(str) == filtro_status_rep]
                if filtro_ano_rep != "Todos":
                    df_f_rep = df_f_rep[df_f_rep["Ano"].astype("Int64").astype(str) == filtro_ano_rep]
                if filtro_mes_rep != "Todos":
                    df_f_rep = df_f_rep[df_f_rep["Mes_Ano"].astype(str) == filtro_mes_rep]
                if filtro_dia_rep != "Todos":
                    df_f_rep = df_f_rep[
                        df_f_rep["Dt_Parsed"].dt.strftime("%d/%m/%Y") == filtro_dia_rep
                    ]

                st.markdown("---")
                st.markdown(f"**Total de O.S. encontradas:** {len(df_f_rep)}")

                filtros_desc = (
                    f"Status={filtro_status_rep}; Ano={filtro_ano_rep}; "
                    f"Mês/Ano={filtro_mes_rep}; Dia={filtro_dia_rep}"
                )

                if not df_f_rep.empty:
                    col_rel1, col_rel2 = st.columns(2)

                    with col_rel1:
                        pdf_rel_geral = gerar_pdf_relatorio_os(df_f_rep)
                        if pdf_rel_geral:
                            st.download_button(
                                "📥 Baixar PDF do Relatório Geral",
                                data=pdf_rel_geral,
                                file_name=f"Relatorio_Geral_OS_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                                mime="application/pdf",
                                use_container_width=True
                            )
                        else:
                            st.warning("Bibliotecas de PDF não disponíveis. A impressão pelo navegador continua disponível.")

                    with col_rel2:
                        st.markdown(
                            """
                            <div style="padding-top:28px;">
                                <b>🖨️ Impressão:</b> use o botão abaixo no painel de impressão.
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

                    st.dataframe(
                        df_f_rep[
                            [
                                "ID", "Data_Criacao", "Solicitante", "Setor",
                                "Equipamento", "Tipo_Manutencao", "Prioridade",
                                "Status", "Solucao", "finalizado_por"
                            ]
                        ].sort_values(by="ID", ascending=False),
                        use_container_width=True,
                        height=420
                    )

                    st.markdown("#### 🖨️ Visualização para Impressão")
                    print_rel_html = gerar_html_relatorio_os_impressao(
                        df_f_rep,
                        filtros_descricao=filtros_desc
                    )
                    components.html(print_rel_html, height=820, scrolling=True)
                else:
                    st.info("Nenhuma O.S. encontrada para os filtros selecionados.")
    # --- TELA 4: FORMULÁRIOS E PRAZOS (FMS) ---
    elif menu == "📅 Formulários e Prazos (FMs)":
        ui3d.titulo("Gestão de Conformidade de Formulários (FMs)", "fms")
        tab_fm1, tab_fm2, tab_fm3, tab_fm4 = st.tabs([ui3d.aba("Registrar / Renovar FM", "fms"), ui3d.aba("Excluir FM", "fms"), ui3d.aba("Painel de Prazos e Status", "dashboard"), ui3d.aba("Biblioteca de FMs (PDF)", "fms")])
        
        dias_dict = {
            "Diário (1 dia)": 1,
            "Semanal (7 dias)": 7, 
            "Quinzenal (15 dias)": 15, 
            "Mensal (30 dias)": 30, 
            "Bimestral (60 dias)": 60,
            "Trimestral (90 dias)": 90,
            "Semestral (180 dias)": 180
        }
        
        with tab_fm1:
            col_f_reg1, col_f_reg2 = st.columns(2)
            
            with col_f_reg1:
                st.subheader("Registrar Novo FM")
                with st.form("form_fm_stang", clear_on_submit=True):
                    fm_nome = st.text_input("Nome/Código do FM (Ex: FM 01 - Gerador)")
                    data_realizada = st.date_input("Data de Realização", value=datetime.now())
                    periodo_nome = st.selectbox("Período de Vencimento", list(dias_dict.keys()))
                    
                    if st.form_submit_button("Salvar e Calcular Prazo", use_container_width=True):
                        if not fm_nome:
                            st.error("Preencha o nome do FM!")
                        else:
                            df_fms = repositorio.ler_csv(ARQUIVO_FMS, dtype=str)
                            nova_fm = {
                                "FM": fm_nome.upper(), 
                                "Data_Realizada": str(data_realizada), 
                                "Periodo": periodo_nome, 
                                "Dias_Prazo": str(dias_dict[periodo_nome])
                            }
                            df_fms = df_fms[df_fms["FM"].str.upper() != fm_nome.strip().upper()]
                            df_fms = pd.concat([df_fms, pd.DataFrame([nova_fm])], ignore_index=True)
                            repositorio.salvar_csv(df_fms, ARQUIVO_FMS)
                            st.success(f"Formulário '{fm_nome.upper()}' cadastrado com sucesso!")
                            st.rerun()
                            
            with col_f_reg2:
                st.subheader("🔄 Renovar FM")
                df_fms_existente = repositorio.ler_csv(ARQUIVO_FMS, dtype=str)
                if df_fms_existente.empty:
                    st.info("Nenhum FM cadastrado para renovar.")
                else:
                    lista_fms_cadastrados = df_fms_existente["FM"].unique().tolist()
                    fm_escolhido = st.selectbox("Selecione o FM que foi refeito", lista_fms_cadastrados, key="select_fm_renovacao")
                    
                    row_fm_ant = df_fms_existente[df_fms_existente["FM"] == fm_escolhido]
                    per_ant_idx = 0
                    if not row_fm_ant.empty:
                        p_str = str(row_fm_ant.iloc[0]["Periodo"])
                        if p_str in list(dias_dict.keys()):
                            per_ant_idx = list(dias_dict.keys()).index(p_str)
                            
                    with st.form("form_renovar_fm"):
                        nova_data_realizada = st.date_input("Nova Data de Realização", value=datetime.now())
                        novo_periodo_nome = st.selectbox("Período de Vencimento", list(dias_dict.keys()), index=per_ant_idx)
                        
                        btn_renovar = st.form_submit_button("🔄 Atualizar Vencimento", use_container_width=True)
                        
                        if btn_renovar:
                            df_fms_existente.loc[df_fms_existente["FM"] == fm_escolhido, "Data_Realizada"] = str(nova_data_realizada)
                            df_fms_existente.loc[df_fms_existente["FM"] == fm_escolhido, "Periodo"] = novo_periodo_nome
                            df_fms_existente.loc[df_fms_existente["FM"] == fm_escolhido, "Dias_Prazo"] = str(dias_dict[novo_periodo_nome])
                            
                            repositorio.salvar_csv(df_fms_existente, ARQUIVO_FMS)
                            st.success(f"FM '{fm_escolhido}' atualizado com sucesso!")
                            st.rerun()

        with tab_fm2:
            st.subheader("🗑️ Excluir Formulário (FM)")
            df_fms_exc = repositorio.ler_csv(ARQUIVO_FMS, dtype=str)
            if df_fms_exc.empty:
                st.info("Nenhum FM cadastrado.")
            else:
                lista_fms_exc = sorted(df_fms_exc["FM"].unique().tolist())
                with st.form("form_excluir_fm"):
                    fm_para_excluir = st.selectbox("Selecione o FM que deseja excluir", lista_fms_exc)
                    btn_conf_exc_fm = st.form_submit_button("🗑️ Excluir FM Selecionado", type="primary", use_container_width=True)
                    
                    if btn_conf_exc_fm:
                        df_fms_exc = df_fms_exc[df_fms_exc["FM"] != fm_para_excluir]
                        repositorio.salvar_csv(df_fms_exc, ARQUIVO_FMS)
                        st.success(f"Formulário '{fm_para_excluir}' excluído com sucesso!")
                        st.rerun()

        with tab_fm3:
            st.subheader("Painel de Prazos e Status dos FMs")
            st.caption("Os modelos PDF para download, inclusão ou exclusão estão na aba 📁 Biblioteca de FMs (PDF).")
            df_fms = repositorio.ler_csv(ARQUIVO_FMS, dtype=str)

            if not df_fms.empty:
                hoje = datetime.now().date()

                df_fms["Data_Realizada"] = pd.to_datetime(
                    df_fms["Data_Realizada"],
                    errors="coerce",
                    dayfirst=True
                ).dt.date

                df_fms["Dias_Prazo_int"] = pd.to_numeric(
                    df_fms["Dias_Prazo"],
                    errors="coerce"
                ).fillna(0).astype(int)

                df_fms["Vencimento"] = df_fms.apply(
                    lambda row: (
                        row["Data_Realizada"] + timedelta(days=int(row["Dias_Prazo_int"]))
                        if pd.notna(row["Data_Realizada"]) else hoje
                    ),
                    axis=1
                )
                df_fms["Dias_Restantes"] = df_fms["Vencimento"].apply(
                    lambda x: (x - hoje).days
                )
                df_fms["Status"] = df_fms["Dias_Restantes"].apply(
                    lambda x: "Atrasado 🔴" if x < 0 else "No Prazo 🟢"
                )

                st.dataframe(
                    df_fms[
                        [
                            "FM", "Data_Realizada", "Periodo",
                            "Vencimento", "Dias_Restantes", "Status"
                        ]
                    ],
                    use_container_width=True
                )

                ui3d.titulo("Comparativo de Prazo × Dias Restantes por FM", "fms", nivel=3)

                df_graf_fm = df_fms[
                    ["FM", "Dias_Prazo_int", "Dias_Restantes", "Status"]
                ].copy()

                df_graf_fm["Dias_Restantes_Grafico"] = df_graf_fm["Dias_Restantes"]
                df_graf_fm["FM"] = df_graf_fm["FM"].astype(str)

                fig_fm = go.Figure()
                fig_fm.add_trace(
                    go.Bar(
                        y=df_graf_fm["FM"],
                        x=df_graf_fm["Dias_Prazo_int"],
                        name="Prazo total (dias)",
                        orientation="h",
                        text=df_graf_fm["Dias_Prazo_int"],
                        textposition="auto",
                        marker=dict(
                            line=dict(width=0),
                            opacity=0.82
                        )
                    )
                )
                fig_fm.add_trace(
                    go.Bar(
                        y=df_graf_fm["FM"],
                        x=df_graf_fm["Dias_Restantes_Grafico"],
                        name="Dias restantes",
                        orientation="h",
                        text=df_graf_fm["Dias_Restantes_Grafico"],
                        textposition="auto",
                        marker=dict(
                            line=dict(width=0),
                            opacity=0.82
                        )
                    )
                )
                fig_fm.add_vline(x=0, line_width=1)
                fig_fm.update_layout(
                    barmode="group",
                    height=max(360, 48 * len(df_graf_fm)),
                    margin=dict(l=10, r=10, t=35, b=10),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(
                        title="Dias",
                        showgrid=False,
                        zeroline=False
                    ),
                    yaxis=dict(
                        title="",
                        showgrid=False,
                        autorange="reversed"
                    ),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="left",
                        x=0
                    )
                )

                st.plotly_chart(
                    tema_stang.estilizar_grafico(fig_fm),
                    use_container_width=True,
                    config={"displaylogo": False}
                )
            else:
                st.info("Nenhum FM cadastrado.")
        with tab_fm4:
            import mod_fm_biblioteca
            mod_fm_biblioteca.renderizar(st.session_state.filial_ativa, is_user_admin)

    # --- TELA 5: SOLICITAÇÕES DE COMPRAS ---
    elif menu == "🛒 Solicitações de Compras":
        ui3d.titulo("Solicitações de Materiais e Insumos", "compras")
        
        if "carrinho_compras" not in st.session_state:
            st.session_state.carrinho_compras = []
            
        abas_compras = st.tabs([ui3d.aba("Gerenciar Solicitações", "compras"), ui3d.aba("Nova Solicitação", "nova_os"), ui3d.aba("Imprimir Ordem de Compra", "imprimir_os")])
        tab_comp1, tab_comp2, tab_comp3 = abas_compras
        
        with tab_comp1:
            st.markdown("### Gerenciar Solicitações e Anexar Orçamento")
            df_ger_c = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)
            if df_ger_c.empty:
                st.info("Nenhuma solicitação de compra cadastrada.")
            else:
                def formatar_status_compra(val):
                    val_str = str(val).lower()
                    if "realizada" in val_str:
                        return "Compra Realizada 🟢"
                    elif "recusada" in val_str:
                        return "Compra Recusada 🔴"
                    else:
                        return "Compra em Aberta 🟠"
                
                def formatar_orcamento(val):
                    if pd.isna(val) or val == "None" or str(val).strip() == "":
                        return "None"
                    return "✅ Anexado"

                df_view = df_ger_c.copy()
                df_view['Status_Visual'] = df_view['Status'].apply(formatar_status_compra)
                df_view['Orcamento_Assinado'] = df_view['Orcamento_Assinado'].apply(formatar_orcamento)
                df_view['Data_Apenas'] = df_view['Data_Solicitacao'].astype(str).str.split(" ").str[0]

                col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns(5)
                
                with col_f1:
                    opts_data = ["Todas"] + sorted([d for d in df_view['Data_Apenas'].dropna().unique() if str(d).strip() != ""])
                    filtro_data = st.selectbox("📅 Data", opts_data, key="f_comp_data")
                with col_f2:
                    opts_solic = ["Todos"] + sorted([s for s in df_view['Solicitante'].dropna().unique() if str(s).strip() != ""])
                    filtro_solic = st.selectbox("👤 Solicitante", opts_solic, key="f_comp_solic")
                with col_f3:
                    opts_cat = ["Todas"] + sorted([c for c in df_view['Categoria'].dropna().unique() if str(c).strip() != ""])
                    filtro_cat = st.selectbox("📁 Categoria", opts_cat, key="f_comp_cat")
                with col_f4:
                    opts_item = ["Todos"] + sorted([i for i in df_view['Item'].dropna().unique() if str(i).strip() != ""])
                    filtro_item = st.selectbox("📦 Item", opts_item, key="f_comp_item")
                with col_f5:
                    opts_status = ["Todos"] + sorted([st_v for st_v in df_view['Status_Visual'].dropna().unique() if str(st_v).strip() != ""])
                    filtro_status_v = st.selectbox("🚦 Status", opts_status, key="f_comp_status")

                if filtro_data != "Todas":
                    df_view = df_view[df_view['Data_Apenas'] == filtro_data]
                if filtro_solic != "Todos":
                    df_view = df_view[df_view['Solicitante'] == filtro_solic]
                if filtro_cat != "Todas":
                    df_view = df_view[df_view['Categoria'] == filtro_cat]
                if filtro_item != "Todos":
                    df_view = df_view[df_view['Item'] == filtro_item]
                if filtro_status_v != "Todos":
                    df_view = df_view[df_view['Status_Visual'] == filtro_status_v]

                st.dataframe(df_view[["ID_Compra", "Data_Solicitacao", "Solicitante", "Setor", "Categoria", "Item", "Quantidade", "Status_Visual", "Orcamento_Assinado"]], use_container_width=True, height=200)
                
                ids_compras = sorted(df_ger_c["ID_Compra"].unique().tolist(), reverse=True)
                
                st.markdown("---")
                
                if is_user_admin:
                    col_ges_1, col_ges_2 = st.columns(2)
                    
                    with col_ges_1:
                        st.markdown("#### 📎 Gerenciar Orçamento Anexado")
                        id_anexo = st.selectbox("ID da Solicitação", ids_compras, key="select_id_anexo_gerir")
                        
                        row_atual_anexo = df_ger_c[df_ger_c["ID_Compra"] == str(id_anexo)]
                        path_atual = "None"
                        if not row_atual_anexo.empty:
                            path_atual = row_atual_anexo.iloc[0].get("Orcamento_Assinado", "None")
                        
                        tem_anexo = pd.notna(path_atual) and str(path_atual).strip() != "None" and str(path_atual).strip() != "" and documento_disponivel(str(path_atual))
                        
                        with st.form("form_anexar_orcamento", clear_on_submit=False):
                            arquivo_anexo = st.file_uploader("Documento (PDF/Img)", type=["pdf", "png", "jpg", "jpeg"])
                            excluir_atual = st.checkbox("🗑️ Excluir atual", value=False, disabled=not tem_anexo)
                            if tem_anexo:
                                st.caption(f"Atual: {os.path.basename(str(path_atual))}")
                            else:
                                st.caption("Nenum anexo atual.")
                            
                            col_b1, col_b2 = st.columns(2)
                            btn_anexar = col_b1.form_submit_button("💾 Salvar", use_container_width=True)
                            btn_remover_apenas = col_b2.form_submit_button("🗑️ Remover", use_container_width=True)
                            
                            if btn_anexar:
                                acao_realizada = False
                                if excluir_atual and tem_anexo:
                                    try:
                                        if documento_disponivel(str(path_atual)):
                                            repositorio.remover_documento(str(path_atual))
                                    except:
                                        pass
                                    df_ger_c.loc[df_ger_c["ID_Compra"] == str(id_anexo), "Orcamento_Assinado"] = "None"
                                    acao_realizada = True
                                
                                if arquivo_anexo is not None:
                                    if tem_anexo and not excluir_atual:
                                        try:
                                            if documento_disponivel(str(path_atual)):
                                                repositorio.remover_documento(str(path_atual))
                                        except:
                                            pass
                                            
                                    file_name = f"pedido_{id_anexo}_{arquivo_anexo.name}"
                                    save_path = repositorio.caminho_documento(file_name)
                                    
                                    with open(save_path, "wb") as f:
                                        f.write(arquivo_anexo.getbuffer())
                                    repositorio.registrar_documento(save_path)
                                        
                                    df_ger_c.loc[df_ger_c["ID_Compra"] == str(id_anexo), "Orcamento_Assinado"] = save_path
                                    acao_realizada = True
                                
                                if acao_realizada:
                                    df_to_save = df_ger_c.drop(columns=["Status_Visual", "Data_Apenas"], errors="ignore")
                                    repositorio.salvar_csv(df_to_save, ARQUIVO_COMPRAS)
                                    st.success("Salvo com sucesso!")
                                    st.rerun()

                            if btn_remover_apenas:
                                if tem_anexo:
                                    try:
                                        if documento_disponivel(str(path_atual)):
                                            repositorio.remover_documento(str(path_atual))
                                    except:
                                        pass
                                    df_ger_c.loc[df_ger_c["ID_Compra"] == str(id_anexo), "Orcamento_Assinado"] = "None"
                                    df_to_save = df_ger_c.drop(columns=["Status_Visual", "Data_Apenas"], errors="ignore")
                                    repositorio.salvar_csv(df_to_save, ARQUIVO_COMPRAS)
                                    st.success("Removido!")
                                    st.rerun()
                                else:
                                    st.warning("Sem anexo.")

                    with col_ges_2:
                        st.markdown("#### ⚙️ Status, Itens & Exclusão")
                        id_pedido_status = st.selectbox("ID Pedido", ids_compras, key="sel_status_compra")
                        
                        rows_pedido = df_ger_c[df_ger_c["ID_Compra"] == str(id_pedido_status)]
                        status_atual_p = rows_pedido.iloc[0]["Status"] if not rows_pedido.empty else "Compra em Aberta"
                        
                        with st.form("form_mudar_status_compra"):
                            status_opcs = ["Compra em Aberta 🟠", "Compra Realizada 🟢", "Compra Recusada 🔴"]
                            idx_st_p = 0
                            if "Realizada" in status_atual_p: idx_st_p = 1
                            elif "Recusada" in status_atual_p: idx_st_p = 2
                            
                            novo_status_pedido = st.selectbox("Novo Status", status_opcs, index=idx_st_p)
                            
                            st.markdown("<b>Editar Itens e Quantidades do Pedido:</b>", unsafe_allow_html=True)
                            item_edits = []
                            for idx_r, r_val in rows_pedido.reset_index().iterrows():
                                st.markdown(f"Item #{idx_r+1}: <b>{r_val['Item']}</b> ({r_val['Categoria']})", unsafe_allow_html=True)
                                nova_qtd = st.number_input(f"Qtd para {r_val['Item']}", min_value=1, value=int(float(r_val['Quantidade'])) if str(r_val['Quantidade']).replace('.','',1).isdigit() else 1, key=f"qty_{id_pedido_status}_{idx_r}")
                                novo_nome_item = st.text_input(f"Nome do Item {idx_r+1}", value=r_val['Item'], key=f"name_{id_pedido_status}_{idx_r}")
                                item_edits.append({"index_original": r_val['index'], "novo_nome": novo_nome_item, "nova_qtd": nova_qtd})
                            
                            col_st1, col_st2 = st.columns(2)
                            btn_salvar_status = col_st1.form_submit_button("💾 Salvar Alterações", use_container_width=True)
                            btn_exc_compra = col_st2.form_submit_button("🗑️ Excluir Pedido", type="primary", use_container_width=True)
                            
                            if btn_salvar_status:
                                if "Realizada" in novo_status_pedido:
                                    status_limpo = "Compra Realizada"
                                elif "Recusada" in novo_status_pedido:
                                    status_limpo = "Compra Recusada"
                                else:
                                    status_limpo = "Compra em Aberta"

                                df_ger_c.loc[df_ger_c["ID_Compra"] == str(id_pedido_status), "Status"] = status_limpo
                                
                                for ed in item_edits:
                                    orig_idx = ed["index_original"]
                                    df_ger_c.loc[orig_idx, "Item"] = str(ed["novo_nome"]).upper()
                                    df_ger_c.loc[orig_idx, "Quantidade"] = str(ed["nova_qtd"])
                                
                                df_to_save = df_ger_c.drop(columns=["Status_Visual", "Data_Apenas"], errors="ignore")
                                repositorio.salvar_csv(df_to_save, ARQUIVO_COMPRAS)
                                st.success("Status e itens atualizados com sucesso!")
                                st.rerun()
                                
                            if btn_exc_compra:
                                df_ger_c = df_ger_c[df_ger_c["ID_Compra"] != str(id_pedido_status)]
                                df_to_save = df_ger_c.drop(columns=["Status_Visual", "Data_Apenas"], errors="ignore")
                                repositorio.salvar_csv(df_to_save, ARQUIVO_COMPRAS)
                                st.success("Pedido excluído com sucesso!")
                                st.rerun()
                else:
                    st.info("🔒 Gerenciamento restrito a administradores.")

        with tab_comp2:
            st.markdown("### Adicione os itens desejados na solicitação:")
            with st.form("form_add_item_compra", clear_on_submit=True):
                col_i1, col_i2, col_i3 = st.columns([2, 1.5, 1])
                with col_i1:
                    item_input = st.text_input("Nome do Material / Item *")
                with col_i2:
                    cat_input = st.selectbox("Categoria", ["Material de Escritório", "Item Operacional", "Material de Limpeza"])
                with col_i3:
                    qtd_input = st.number_input("Quantidade", min_value=1, value=1)
                    
                btn_add_item = st.form_submit_button("➕ Adicionar Item à Lista", use_container_width=True)
                if btn_add_item:
                    if not item_input:
                        st.error("Informe o nome do material!")
                    else:
                        st.session_state.carrinho_compras.append({
                            "Item": item_input.upper(),
                            "Categoria": cat_input,
                            "Quantidade": int(qtd_input)
                        })
                        st.success(f"Item '{item_input.upper()}' adicionado!")
            
            if st.session_state.carrinho_compras:
                st.markdown("#### Itens Atuais na Solicitação:")
                df_carrinho = pd.DataFrame(st.session_state.carrinho_compras)
                st.dataframe(df_carrinho, use_container_width=True)
                
                if st.button("🗑️ Limpar Lista de Itens"):
                    st.session_state.carrinho_compras = []
                    st.rerun()
                
                st.markdown("---")
                with st.form("form_finalizar_pedido"):
                    st.markdown("#### Dados Finais da Solicitação:")
                    col_f1, col_f2 = st.columns(2)
                    with col_f1:
                        solicitante_c = st.text_input("Nome do Solicitante *")
                        setor_c = st.selectbox("Setor", ["OPERAÇÃO", "MANUTENÇÃO", "PORTARIA", "ADMINISTRATIVO", "TI"])
                    with col_f2:
                        obs_c = st.text_area("Observações / Justificativa Geral")
                        
                    submit_final_compra = st.form_submit_button("💾 Salvar e Registrar Pedido Completo", use_container_width=True)
                    if submit_final_compra:
                        if not solicitante_c:
                            st.error("Preencha o nome do Solicitante!")
                        else:
                            df_c = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)
                            if not df_c.empty and "ID_Compra" in df_c.columns:
                                ids_nums = pd.to_numeric(df_c["ID_Compra"], errors="coerce").dropna()
                                novo_id_c = int(ids_nums.max() + 1) if not ids_nums.empty else 501
                            else:
                                novo_id_c = 501
                            # PDFs resgatados de pedidos ainda ausentes do CSV (ex.: 538/539)
                            # reservam o ID, para não criar um pedido novo com número histórico.
                            import mod_resgate_2026
                            ids_reservados = mod_resgate_2026.ids_reservados_em_anexos(st.session_state.filial_ativa)
                            if ids_reservados:
                                novo_id_c = max(novo_id_c, max(ids_reservados) + 1)
                                
                            data_hora_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                            
                            novas_linhas = []
                            for it in st.session_state.carrinho_compras:
                                novas_linhas.append({
                                    "ID_Compra": str(novo_id_c),
                                    "Data_Solicitacao": data_hora_atual,
                                    "Solicitante": solicitante_c.upper(),
                                    "Setor": setor_c.upper(),
                                    "Categoria": it["Categoria"],
                                    "Item": it["Item"],
                                    "Quantidade": str(it["Quantidade"]),
                                    "Observacoes": obs_c.upper() if obs_c else "",
                                    "Status": "Compra em Aberta",
                                    "Orcamento_Assinado": "None",
                                    "NF_Anexada": "None",
                                    "Boleto_Anexado": "None",
                                    "Assinado_Por": "None",
                                    "Email_Enviado": "Não"
                                })
                            
                            df_c = pd.concat([df_c, pd.DataFrame(novas_linhas)], ignore_index=True)
                            repositorio.salvar_csv(df_c, ARQUIVO_COMPRAS)
                            st.session_state.carrinho_compras = []
                            st.success(f"Solicitação de Compra #{novo_id_c} registrada com sucesso!")
                            st.rerun()

        with tab_comp3:
            df_c_print = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)
            if df_c_print.empty:
                st.warning("Nenhuma solicitação de compra cadastrada para impressão.")
            else:
                ids_disponiveis = sorted(df_c_print["ID_Compra"].unique().tolist(), reverse=True)
                
                col_print1, col_print2 = st.columns([2, 1])
                with col_print1:
                    pedido_sel_id = st.selectbox("Selecione o ID do Pedido de Compra:", ids_disponiveis)
                
                itens_pedido = df_c_print[df_c_print["ID_Compra"] == str(pedido_sel_id)]
                row_c_base = itens_pedido.iloc[0]
                
                with col_print2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    anexo_path = row_c_base.get("Orcamento_Assinado", "None")
                    if pd.notna(anexo_path) and str(anexo_path).strip() != "None" and str(anexo_path).strip() != "" and documento_disponivel(str(anexo_path)):
                        with open(anexo_path, "rb") as file:
                            st.download_button(
                                label="📥 Baixar Orçamento Anexado",
                                data=file,
                                file_name=os.path.basename(str(anexo_path)),
                                mime="application/octet-stream",
                                use_container_width=True
                            )
                    else:
                        st.info("Nenhum orçamento anexado a este pedido.")
                
                st.markdown("---")
                
                logo_base64 = ""
                if os.path.exists("logo.png"):
                    with open("logo.png", "rb") as img_file:
                        logo_base64 = base64.b64encode(img_file.read()).decode()
                        
                linhas_tabela_html = ""
                for _, row in itens_pedido.iterrows():
                    linhas_tabela_html += f"""
                    <tr>
                        <td style="border: 1px solid #000; padding: 8px; width: 60%;"><b>{row['Item']}</b> ({row['Categoria']})</td>
                        <td style="border: 1px solid #000; padding: 8px; width: 40%; text-align: center;">{row['Quantidade']}</td>
                    </tr>
                    """
                        
                print_compra_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ background-color: #ffffff; color: #000000; margin: 0; padding: 10px; font-family: Arial, sans-serif; }}
        .print-btn-container {{ text-align: center; margin-bottom: 20px; }}
        .btn-imprimir {{ background-color: #007bff; color: white; border: none; padding: 12px 25px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; }}
        @media print {{ .print-btn-container {{ display: none !important; }} body {{ padding: 0; }} }}
    </style>
</head>
<body>
    <div class="print-btn-container">
        <button class="btn-imprimir" onclick="window.print()">🖨️ Imprimir Ordem de Solicitação de Compra</button>
    </div>
    <div style="background-color: #ffffff; color: #000000; padding: 20px; border: 2px solid #000; max-width: 800px; margin: auto;">
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000;">
            <tr>
                <td style="width: 28%; border: 1px solid #000; padding: 5px; text-align: center; vertical-align: middle;">
                    <img src="data:image/png;base64,{logo_base64}" style="max-height: 45px; max-width: 100%;">
                </td>
                <td style="width: 44%; border: 1px solid #000; text-align: center; vertical-align: middle;">
                    <h3 style="margin: 0; color: #000 !important; font-size: 15px;">Ordem de Solicitação de Compras</h3>
                </td>
                <td style="width: 28%; border: 1px solid #000; padding: 5px; font-size: 11px; text-align: right; color: #000 !important; vertical-align: middle;">
                    <b>LOG-COMPRAS</b>
                </td>
            </tr>
        </table>
        <table style="width: 100%; border-collapse: collapse; border: 1px solid #000; font-size: 12px; color: #000 !important; margin-top: 5px;">
            <tr>
                <td style="border: 1px solid #000; padding: 5px; width: 33%;"><b>ID Pedido:</b> #{row_c_base['ID_Compra']}</td>
                <td style="border: 1px solid #000; padding: 5px; width: 34%;"><b>Data:</b> {row_c_base['Data_Solicitacao']}</td>
                <td style="border: 1px solid #000; padding: 5px; width: 33%;"><b>Status:</b> {row_c_base['Status']}</td>
            </tr>
            <tr>
                <td style="border: 1px solid #000; padding: 5px;" colspan="2"><b>Solicitante:</b> {row_c_base['Solicitante']}</td>
                <td style="border: 1px solid #000; padding: 5px;"><b>Setor:</b> {row_c_base['Setor']}</td>
            </tr>
        </table>
        <div style="margin-top: 15px;">
            <table style="width: 100%; border-collapse: collapse; font-size: 12px; color: #000 !important;">
                <thead>
                    <tr style="background-color: #e0e0e0;">
                        <th style="border: 1px solid #000; padding: 8px; text-align: left;">Item / Material</th>
                        <th style="border: 1px solid #000; padding: 8px; text-align: center;">Quantidade</th>
                    </tr>
                </thead>
                <tbody>
                    {linhas_tabela_html}
                </tbody>
            </table>
        </div>
        <div style="border: 1px solid #000; margin-top: 15px;">
            <div style="background-color: #e0e0e0; text-align: center; font-size: 12px; font-weight: bold; border-bottom: 1px solid #000; padding: 4px; color: #000 !important;">Observações / Justificativa</div>
            <div style="padding: 10px; min-height: 50px; font-size: 13px; color: #000 !important;">{row_c_base.get('Observacoes', '')}</div>
        </div>
        <table style="width: 100%; margin-top: 40px; font-size: 12px; border-collapse: collapse; color: #000 !important;">
            <tr>
                <td style="text-align: center; width: 50%;">__________________________________________________<br><b>Solicitante ({row_c_base['Solicitante']})</b></td>
                <td style="text-align: center; width: 50%;">__________________________________________________<br><b>Aprovação Compras / Gerência</b></td>
            </tr>
        </table>
    </div>
</body>
</html>
"""
                components.html(print_compra_html, height=750, scrolling=True)

    # --- TELA 6: PAINEL DE ASSINATURAS ---
    elif menu == "✍️ Painel de Assinaturas":
        ui3d.titulo("Painel de Assinaturas (NF e Boleto)", "assinaturas")

        df_ass = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)
        if df_ass.empty:
            st.info("Nenhuma compra registrada para assinar.")
        else:
            ids_ass = sorted(df_ass["ID_Compra"].dropna().astype(str).unique().tolist(),
                             key=lambda x: (int(x) if x.isdigit() else -1, x), reverse=True)
            resumo_opcoes = {
                id_p: _resumo_etapas_compra(df_ass[df_ass["ID_Compra"].astype(str) == id_p])
                for id_p in ids_ass
            }
            def exibir_pedido_opcao(id_p):
                feitos, total = resumo_opcoes[id_p]
                return (f'{id_p} — ✅ OK | Enviado ({total}/{total})'
                        if feitos == total else f'{id_p} — ⏳ Pendente ({feitos}/{total})')

            col_sel1, col_sel2 = st.columns([2.5, 1])

            with col_sel1:
                id_sel_ass = st.selectbox("Selecione o Número do Pedido (Orçamento):", ids_ass,
                                          key="sel_ass_id", format_func=exibir_pedido_opcao)
                feitas, total_etapas = resumo_opcoes[id_sel_ass]
                if feitas == total_etapas:
                    st.caption('✅ Cinco etapas confirmadas no cadastro: orçamento, NF, boleto, assinatura e e-mail.')
                else:
                    st.caption(f'⏳ {feitas} de {total_etapas} etapas confirmadas. Os documentos serão conferidos ao abrir o pedido.')
                rows_ass = df_ass[df_ass["ID_Compra"] == str(id_sel_ass)]
                row_base_ass = rows_ass.iloc[0]

            df_u_check = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
            row_u_logged = df_u_check[df_u_check["Usuario"].str.lower() == st.session_state.usuario.lower()]
            ass_png_path = "None"
            if not row_u_logged.empty:
                ass_png_path = str(row_u_logged.iloc[0].get("Assinatura_PNG", "None"))
            # Verificação leve, sem baixar a assinatura em todos os cliques.
            caminhos_pedido = [
                str(row_base_ass.get("Orcamento_Assinado", "None")),
                str(row_base_ass.get("NF_Anexada", "None")),
                str(row_base_ass.get("Boleto_Anexado", "None")),
                ass_png_path,
            ]
            existencia_docs = repositorio.documentos_existentes(caminhos_pedido)
            tem_assinatura_png = bool(existencia_docs.get(ass_png_path, False))

            with col_sel2:
                st.markdown("<br>", unsafe_allow_html=True)
                btn_assinar_agora = st.button("✍️ Assinar Doc", disabled=not tem_assinatura_png, use_container_width=True)
                if btn_assinar_agora:
                    # A assinatura será transferida somente durante a operação real.
                    if not documento_disponivel(ass_png_path):
                        st.error("Assinatura PNG indisponível. Recarregue e tente novamente.")
                        st.stop()
                    documentos_para_assinar = [
                        str(row_base_ass.get("Orcamento_Assinado", "None")),
                        str(row_base_ass.get("NF_Anexada", "None")),
                        str(row_base_ass.get("Boleto_Anexado", "None"))
                    ]

                    assinaram_algo = False
                    for doc in documentos_para_assinar:
                        if doc != "None" and documento_disponivel(doc):
                            res = carimbar_assinatura_no_documento(doc, ass_png_path, st.session_state.usuario)
                            if res:
                                assinaram_algo = True

                    if assinaram_algo:
                        df_ass.loc[df_ass["ID_Compra"] == str(id_sel_ass), "Assinado_Por"] = st.session_state.usuario
                        repositorio.salvar_csv(df_ass, ARQUIVO_COMPRAS)
                        st.success(f"Documentos do Pedido #{id_sel_ass} assinados digitalmente por {st.session_state.usuario}!")
                        st.rerun()
                    else:
                        st.error("Nenhum documento válido encontrado/anexado para assinar.")

            p_orc = row_base_ass.get("Orcamento_Assinado", "None")
            p_nf = row_base_ass.get("NF_Anexada", "None")
            p_bol = row_base_ass.get("Boleto_Anexado", "None")
            p_ass = row_base_ass.get("Assinado_Por", "None")
            p_email = row_base_ass.get("Email_Enviado", "Não")

            # Metadados em uma consulta, sem transferir conteúdo de até três arquivos.
            has_orc = bool(existencia_docs.get(str(p_orc), False))
            has_nf = bool(existencia_docs.get(str(p_nf), False))
            has_bol = bool(existencia_docs.get(str(p_bol), False))
            has_signature = pd.notna(p_ass) and str(p_ass).strip() != "None" and str(p_ass).strip() != ""
            has_email = str(p_email).strip().lower().startswith("sim")

            val_orc = '<span class="badge-success">✅ Anexado</span>' if has_orc else '<span class="badge-danger">❌ Pendente</span>'
            val_nf = '<span class="badge-success">✅ Anexada</span>' if has_nf else '<span class="badge-danger">❌ Pendente</span>'
            val_bol = '<span class="badge-success">✅ Anexado</span>' if has_bol else '<span class="badge-danger">❌ Pendente</span>'
            val_ass = f'<span class="badge-success">✅ Por {p_ass}</span>' if has_signature else '<span class="badge-warning">⚠️ Não Assinado</span>'
            val_email = '<span class="badge-success">✅ Enviado</span>' if has_email else '<span class="badge-danger">❌ Não Enviado</span>'

            st.markdown(f"""
            <div class="status-card-container">
                <div class="status-card">
                    <div class="status-card-title">Orçamento</div>
                    <div class="status-card-value">{val_orc}</div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">Nota Fiscal</div>
                    <div class="status-card-value">{val_nf}</div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">Boleto</div>
                    <div class="status-card-value">{val_bol}</div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">Assinatura Digital</div>
                    <div class="status-card-value">{val_ass}</div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">E-mail</div>
                    <div class="status-card-value">{val_email}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            itens_do_pedido_str = ", ".join([f"{r['Item']} (Qtd: {r['Quantidade']})" for _, r in rows_ass.iterrows()])
            solicitante_pedido = row_base_ass.get("Solicitante", "N/A")
            setor_pedido = row_base_ass.get("Setor", "N/A")
            obs_pedido = row_base_ass.get("Observacoes", "Sem observações")

            st.info(f"📋 **Descrição do Pedido #{id_sel_ass}:**\n\n"
                    f"* **Solicitante:** {solicitante_pedido} | **Setor:** {setor_pedido}\n"
                    f"* **Itens:** {itens_do_pedido_str}\n"
                    f"* **Observações:** {obs_pedido}")

            st.markdown("---")

            col_upload, col_acoes = st.columns(2)

            with col_upload:
                with st.form("form_up_nf_bol"):
                    st.markdown("**Upload de Nota Fiscal e Boleto**")
                    up_nf = st.file_uploader("Upload da Nota Fiscal (NF)", type=["pdf", "png", "jpg", "jpeg"])
                    up_bol = st.file_uploader("Upload do Boleto", type=["pdf", "png", "jpg", "jpeg"])

                    if st.form_submit_button("Salvar Documentos"):
                        salvou_algo = False
                        if up_nf is not None:
                            nf_name = f"nf_{id_sel_ass}_{up_nf.name}"
                            nf_path = repositorio.caminho_documento(nf_name)
                            with open(nf_path, "wb") as f:
                                f.write(up_nf.getbuffer())
                            repositorio.registrar_documento(nf_path)
                            df_ass.loc[df_ass["ID_Compra"] == str(id_sel_ass), "NF_Anexada"] = nf_path
                            salvou_algo = True

                        if up_bol is not None:
                            bol_name = f"boleto_{id_sel_ass}_{up_bol.name}"
                            bol_path = repositorio.caminho_documento(bol_name)
                            with open(bol_path, "wb") as f:
                                f.write(up_bol.getbuffer())
                            repositorio.registrar_documento(bol_path)
                            df_ass.loc[df_ass["ID_Compra"] == str(id_sel_ass), "Boleto_Anexado"] = bol_path
                            salvou_algo = True

                        if salvou_algo:
                            repositorio.salvar_csv(df_ass, ARQUIVO_COMPRAS)
                            st.success("Documentos salvos com sucesso!")
                            st.rerun()
                        else:
                            st.warning("Selecione ao menos um arquivo para salvar.")

            with col_acoes:
                if not tem_assinatura_png:
                    st.warning("⚠️ Seu usuário não possui uma assinatura em PNG cadastrada na tela de Login.")

                with st.form("form_enviar_email_docs"):
                    st.markdown("**📧 Enviar Documentos por E-mail:**")
                    dest_email = st.text_input("E-mail do Destinatário", value="financeiro@stang.com.br")
                    assunto_email = st.text_input(
                        "Assunto do E-mail *",
                        value="",
                        placeholder="Digite o assunto que deve aparecer ao destinatário",
                        key=f"assunto_email_pedido_{id_sel_ass}",
                    )
                    mensagem_email = st.text_area(
                        "Mensagem do E-mail",
                        value="",
                        height=100,
                        placeholder="Escreva livremente a mensagem (opcional)",
                        key=f"mensagem_email_pedido_{id_sel_ass}",
                    )

                    btn_enviar_e = st.form_submit_button("🚀 Enviar E-mail com Anexos", use_container_width=True)

                    if btn_enviar_e:
                        if not dest_email.strip():
                            st.error("Informe o e-mail do destinatário.")
                        elif not assunto_email.strip():
                            st.error("Informe o assunto do e-mail antes de enviar.")
                        elif "\r" in assunto_email or "\n" in assunto_email:
                            st.error("O assunto do e-mail deve ocupar apenas uma linha.")
                        else:
                            df_u_l = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
                            row_u_l = df_u_l[df_u_l["Usuario"].str.lower() == st.session_state.usuario.lower()]

                            if row_u_l.empty:
                                st.error("Dados de usuário não encontrados para envio.")
                            else:
                                u_row_dict = row_u_l.iloc[0].to_dict()
                                cfg_e_dict = {
                                    "Email_Remetente": u_row_dict.get("Email_Usuario", ""),
                                    "Senha_App": u_row_dict.get("Senha_App_Email", ""),
                                    "Servidor_SMTP": u_row_dict.get("Servidor_SMTP", "smtp.gmail.com"),
                                    "Porta_SMTP": u_row_dict.get("Porta_SMTP", "587"),
                                    "Nome_Remetente": st.session_state.usuario
                                }

                                anexos_envio = [
                                    str(row_base_ass.get("Orcamento_Assinado", "None")),
                                    str(row_base_ass.get("NF_Anexada", "None")),
                                    str(row_base_ass.get("Boleto_Anexado", "None"))
                                ]

                                anexos_referenciados = [a for a in anexos_envio if a and a.strip().lower() not in ("none", "nan")]
                                anexos_faltantes = [a for a in anexos_referenciados if not documento_disponivel(a)]
                                if anexos_faltantes:
                                    st.error("Um ou mais documentos vinculados não puderam ser recuperados. O e-mail NÃO foi enviado.")
                                    st.stop()
                                if not anexos_referenciados:
                                    st.error("Não há documentos anexados a este pedido. O e-mail NÃO foi enviado.")
                                    st.stop()

                                sucesso_e, msg_e = enviar_email_real(
                                    destinatario=dest_email.strip(),
                                    assunto=assunto_email.strip(),
                                    corpo=mensagem_email,
                                    anexos=anexos_envio,
                                    config=cfg_e_dict
                                )

                                if sucesso_e:
                                    df_ass.loc[df_ass["ID_Compra"] == str(id_sel_ass), "Email_Enviado"] = f"Sim | {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                                    repositorio.salvar_csv(df_ass, ARQUIVO_COMPRAS)
                                    st.success(f"E-mail enviado com sucesso para {dest_email}!")
                                    st.rerun()
                                else:
                                    st.error(f"Erro no envio: {msg_e}")

            st.markdown("---")
            st.markdown("#### 📄 Visualização de Documentos Vinculados ao Pedido:")

            c_doc1, c_doc2, c_doc3 = st.columns(3)
            with c_doc1:
                exibir_documento(row_base_ass.get("Orcamento_Assinado", "None"), "Orçamento")
            with c_doc2:
                exibir_documento(row_base_ass.get("NF_Anexada", "None"), "Nota Fiscal")
            with c_doc3:
                exibir_documento(row_base_ass.get("Boleto_Anexado", "None"), "Boleto")

    # --- TELA 6: DASHBOARD ---
    elif menu == "📊 Dashboard":
        ui3d.titulo("Dashboard Gerencial", "dashboard")
        st.caption("Filtros globais aplicados às janelas de Compras e O.S.")

        # Carregamento independente das duas bases para não alterar a área de Compras.
        df_dash_os = carregar_banco_os()
        df_dash_comp = repositorio.ler_csv(ARQUIVO_COMPRAS, dtype=str)

        def _parse_data_dashboard(serie):
            return pd.to_datetime(
                serie.astype(str),
                errors="coerce",
                dayfirst=True
            )

        if not df_dash_os.empty:
            df_dash_os["Data_Dashboard"] = _parse_data_dashboard(df_dash_os["Data_Criacao"])
        else:
            df_dash_os["Data_Dashboard"] = pd.Series(dtype="datetime64[ns]")

        if not df_dash_comp.empty:
            df_dash_comp["Data_Dashboard"] = _parse_data_dashboard(df_dash_comp["Data_Solicitacao"])
        else:
            df_dash_comp["Data_Dashboard"] = pd.Series(dtype="datetime64[ns]")

        anos = set()
        meses = set()
        dias = set()

        for base in (df_dash_os, df_dash_comp):
            datas_validas = base["Data_Dashboard"].dropna()
            anos.update(datas_validas.dt.year.astype(int).tolist())
            meses.update(datas_validas.dt.month.astype(int).tolist())
            dias.update(datas_validas.dt.day.astype(int).tolist())

        MESES_PT = {
            1: "01 - Janeiro", 2: "02 - Fevereiro", 3: "03 - Março",
            4: "04 - Abril", 5: "05 - Maio", 6: "06 - Junho",
            7: "07 - Julho", 8: "08 - Agosto", 9: "09 - Setembro",
            10: "10 - Outubro", 11: "11 - Novembro", 12: "12 - Dezembro"
        }

        col_g1, col_g2, col_g3 = st.columns(3)

        with col_g1:
            filtro_ano_dash = st.selectbox(
                "📅 Ano",
                ["Todos"] + [str(a) for a in sorted(anos, reverse=True)],
                key="dash_ano"
            )

        with col_g2:
            mes_opts_dash = ["Todos"] + [
                MESES_PT[m] for m in sorted(meses)
                if m in MESES_PT
            ]
            filtro_mes_dash = st.selectbox(
                "🗓️ Mês",
                mes_opts_dash,
                key="dash_mes"
            )

        with col_g3:
            filtro_dia_dash = st.selectbox(
                "🔢 Dia",
                ["Todos"] + [str(d) for d in sorted(dias)],
                key="dash_dia"
            )

        def _aplicar_filtros_globais(df):
            base = df.copy()
            if base.empty:
                return base

            if filtro_ano_dash != "Todos":
                base = base[
                    base["Data_Dashboard"].dt.year.fillna(-1).astype(int)
                    == int(filtro_ano_dash)
                ]

            if filtro_mes_dash != "Todos":
                mes_num = int(filtro_mes_dash.split(" - ")[0])
                base = base[
                    base["Data_Dashboard"].dt.month.fillna(-1).astype(int)
                    == mes_num
                ]

            if filtro_dia_dash != "Todos":
                base = base[
                    base["Data_Dashboard"].dt.day.fillna(-1).astype(int)
                    == int(filtro_dia_dash)
                ]
            return base

        df_dash_os_f = _aplicar_filtros_globais(df_dash_os)
        df_dash_comp_f = _aplicar_filtros_globais(df_dash_comp)

        # Estilo Power BI: gráficos sem fundo.
        def _layout_fig(fig, altura=360, legenda=True):
            fig.update_layout(
                height=altura,
                margin=dict(l=10, r=10, t=55, b=15),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(size=11),
                showlegend=legenda,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="left",
                    x=0
                )
            )
            return tema_stang.estilizar_grafico(fig)

        # JANELA 1 — O.S.
        aba_dash_os, aba_dash_comp = st.tabs([
            ui3d.aba("Setor O.S.", "gerenciar_os"),
            ui3d.aba("Setor Compras", "compras")
        ])

        with aba_dash_os:
            ui3d.titulo("Indicadores de Ordens de Serviço", "gerenciar_os", nivel=3)

            k1, k2, k3, k4 = st.columns(4)
            total_os = len(df_dash_os_f)
            finalizadas_os = (
                df_dash_os_f["Status"].astype(str).str.contains("Finalizada", case=False, na=False).sum()
                if not df_dash_os_f.empty else 0
            )
            abertas_os = (
                df_dash_os_f["Status"].astype(str).str.contains("Em Aberto", case=False, na=False).sum()
                if not df_dash_os_f.empty else 0
            )
            andamento_os = (
                df_dash_os_f["Status"].astype(str).str.contains("Em Andamento", case=False, na=False).sum()
                if not df_dash_os_f.empty else 0
            )

            k1.metric("Total O.S.", total_os)
            k2.metric("Finalizadas", int(finalizadas_os))
            k3.metric("Em Andamento", int(andamento_os))
            k4.metric("Em Aberto", int(abertas_os))

            if df_dash_os_f.empty:
                st.info("Nenhuma O.S. encontrada para os filtros globais.")
            else:
                c_os1, c_os2 = st.columns(2)

                with c_os1:
                    d_status_os = (
                        df_dash_os_f.assign(
                            Status=df_dash_os_f["Status"].fillna("Sem Status").astype(str)
                        )
                        .groupby("Status")
                        .size()
                        .reset_index(name="Quantidade")
                    )
                    fig_os_status = px.pie(
                        d_status_os,
                        names="Status",
                        values="Quantidade",
                        hole=0.58,
                        title="O.S. por Status"
                    )
                    fig_os_status.update_traces(
                        textposition="inside",
                        textinfo="percent+label"
                    )
                    st.plotly_chart(
                        _layout_fig(fig_os_status, altura=380),
                        use_container_width=True,
                        config={"displaylogo": False}
                    )

                with c_os2:
                    d_setor_os = (
                        df_dash_os_f.assign(
                            Setor=df_dash_os_f["Setor"].fillna("Sem Setor").astype(str)
                        )
                        .groupby("Setor")
                        .size()
                        .reset_index(name="Quantidade")
                        .sort_values("Quantidade", ascending=True)
                    )
                    fig_os_setor = px.bar(
                        d_setor_os,
                        x="Quantidade",
                        y="Setor",
                        orientation="h",
                        text="Quantidade",
                        title="O.S. por Setor"
                    )
                    st.plotly_chart(
                        _layout_fig(fig_os_setor, altura=380, legenda=False),
                        use_container_width=True,
                        config={"displaylogo": False}
                    )

                d_prio_os = (
                    df_dash_os_f.assign(
                        Prioridade=df_dash_os_f["Prioridade"].fillna("Sem Prioridade").astype(str)
                    )
                    .groupby("Prioridade")
                    .size()
                    .reset_index(name="Quantidade")
                )
                d_prio_os["Ordem"] = d_prio_os["Prioridade"].map({
                    "URGENTE": 1, "ALTA": 2, "MÉDIA": 3, "MEDIA": 3, "BAIXA": 4
                }).fillna(5)
                d_prio_os = d_prio_os.sort_values("Ordem")

                fig_os_prio = px.bar(
                    d_prio_os,
                    x="Prioridade",
                    y="Quantidade",
                    text="Quantidade",
                    title="O.S. por Prioridade"
                )
                st.plotly_chart(
                    _layout_fig(fig_os_prio, altura=360, legenda=False),
                    use_container_width=True,
                    config={"displaylogo": False}
                )

        # JANELA 2 — COMPRAS
        with aba_dash_comp:
            ui3d.titulo("Indicadores de Solicitações de Compras", "compras", nivel=3)

            def _status_compra_dashboard(valor):
                v = str(valor).lower()
                if "realizada" in v:
                    return "Compra Realizada 🟢"
                if "recusada" in v:
                    return "Compra Recusada 🔴"
                return "Compra em Aberta 🟠"

            k5, k6, k7, k8 = st.columns(4)
            total_comp = len(df_dash_comp_f)
            realizadas_comp = (
                df_dash_comp_f["Status"].apply(_status_compra_dashboard).str.contains("Realizada", na=False).sum()
                if not df_dash_comp_f.empty else 0
            )
            recusadas_comp = (
                df_dash_comp_f["Status"].apply(_status_compra_dashboard).str.contains("Recusada", na=False).sum()
                if not df_dash_comp_f.empty else 0
            )
            abertas_comp = (
                df_dash_comp_f["Status"].apply(_status_compra_dashboard).str.contains("Aberta", na=False).sum()
                if not df_dash_comp_f.empty else 0
            )

            k5.metric("Total Compras", total_comp)
            k6.metric("Realizadas", int(realizadas_comp))
            k7.metric("Em Aberto", int(abertas_comp))
            k8.metric("Recusadas", int(recusadas_comp))

            if df_dash_comp_f.empty:
                st.info("Nenhuma solicitação de compra encontrada para os filtros globais.")
            else:
                c_cp1, c_cp2 = st.columns(2)

                d_status_cp = (
                    df_dash_comp_f.assign(
                        Status_Visual=df_dash_comp_f["Status"].apply(_status_compra_dashboard)
                    )
                    .groupby("Status_Visual")
                    .size()
                    .reset_index(name="Quantidade")
                )
                fig_cp_status = px.pie(
                    d_status_cp,
                    names="Status_Visual",
                    values="Quantidade",
                    hole=0.58,
                    title="Compras por Status"
                )
                fig_cp_status.update_traces(
                    textposition="inside",
                    textinfo="percent+label"
                )

                with c_cp1:
                    st.plotly_chart(
                        _layout_fig(fig_cp_status, altura=380),
                        use_container_width=True,
                        config={"displaylogo": False}
                    )

                d_cat_cp = (
                    df_dash_comp_f.assign(
                        Categoria=df_dash_comp_f["Categoria"].fillna("Sem Categoria").astype(str)
                    )
                    .groupby("Categoria")
                    .size()
                    .reset_index(name="Quantidade")
                    .sort_values("Quantidade", ascending=True)
                    .tail(12)
                )
                fig_cp_cat = px.bar(
                    d_cat_cp,
                    x="Quantidade",
                    y="Categoria",
                    orientation="h",
                    text="Quantidade",
                    title="Compras por Categoria (até 12)"
                )

                with c_cp2:
                    st.plotly_chart(
                        _layout_fig(fig_cp_cat, altura=380, legenda=False),
                        use_container_width=True,
                        config={"displaylogo": False}
                    )

        st.caption(
            "Os filtros de Dia, Mês e Ano acima são globais para as duas janelas do Dashboard. "
            "As áreas de Solicitações de Compras, Painel de Assinaturas e demais painéis existentes não foram alteradas."
        )
