"""Emite ficha de conferência física (somente leitura) do estoque da filial.

Este módulo não grava nem movimenta estoque. O PDF é gerado a partir do
inventário vigente fornecido pela camada de persistência.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


AZUL = colors.HexColor('#15385A')
CINZA = colors.HexColor('#E9EEF3')
BORDA = colors.HexColor('#AAB6C4')
CINZA_TEXTO = colors.HexColor('#35465B')


def _paragrafo(valor, style):
    texto = escape(str(valor if valor is not None else ''), quote=False)
    return Paragraph(texto.replace('\n', '<br/>'), style)


def formatar_quantidade(valor):
    """Formata saldo decimal para a folha impressa, sem arredondar inteiros."""
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return str(valor or '0')
    if not d.is_finite():
        return '-'
    texto = format(d, ',.3f')
    texto = texto.rstrip('0').rstrip('.') if '.' in texto else texto
    return texto.replace(',', '@').replace('.', ',').replace('@', '.')


def _secao(story, titulo, itens, *, cegas, extras, estilos, largura):
    cabeca = [
        'Código', 'Descrição / item', 'Localização', 'Unid.',
        'Qtd. sistema', 'Qtd. física', 'Diferença', 'Observações da inspeção'
    ]
    colunas = [72, 208, 106, 43, 77, 75, 59, 107]
    assert sum(colunas) <= largura
    dados = [[_paragrafo(s, estilos['colunas']) for s in cabeca]]
    for registro in itens:
        colunas_dados = [
            registro.get('codigo', ''), registro.get('descricao', ''),
            registro.get('localizacao', ''), registro.get('unidade', ''),
            '' if cegas else formatar_quantidade(registro.get('estoque', 0)),
            ' ', ' ', ' ',
        ]
        dados.append([_paragrafo(s, estilos['celula']) for s in colunas_dados])
    for _ in range(extras if itens else max(extras, 8)):
        dados.append([_paragrafo('', estilos['celula']) for _ in range(8)])

    story.append(_paragrafo(titulo, estilos['secao']))
    story.append(Spacer(1, 5))
    tabela = Table(dados, colWidths=colunas, repeatRows=1, hAlign='LEFT',
                   rowHeights=[30] + [None]*(len(dados)-1))
    tabela.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), AZUL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7F9FC')]),
    ]))
    story.append(tabela)
    story.append(Spacer(1, 14))


def gerar_formulario_conferencia(
    filial: str,
    itens: list[dict],
    *,
    categorias: tuple[str, ...] = ('MATERIAL',),
    incluir_inativos: bool = False,
    contagem_cega: bool = False,
    linhas_extras: int = 5,
    usuario: str = '',
    nome_filial: str | None = None,
    agora: datetime | None = None,
) -> bytes:
    """Gera PDF A4 paisagem. Não altera dados do Supabase.

    :param itens: saída de repo.listar_itens(filial, tipo), uma lista por categoria.
    :param contagem_cega: oculta a quantidade esperada para a contagem independente.
    """
    categorias = tuple(categorias)
    if not categorias or any(tipo not in ('MATERIAL', 'FERRAMENTA') for tipo in categorias):
        raise ValueError('Tipo de estoque não suportado')
    if not (0 <= linhas_extras <= 25):
        raise ValueError('Linhas extras deve estar entre 0 e 25')
    itens_validos = [dict(r) for r in itens if r.get('tipo') in categorias
                    and str(r.get('filial', filial)) == str(filial)
                    and (incluir_inativos or int(r.get('ativo', 1)) == 1)]
    itens_validos.sort(key=lambda r: (r['tipo'], str(r.get('descricao', '')).casefold(), str(r.get('codigo', '')).casefold()))
    if len(itens_validos) > 4000:
        raise ValueError('Muitos itens para uma só impressão. Divida a conferência em categorias.')
    agora = agora or datetime.now(ZoneInfo('America/Sao_Paulo'))
    out = BytesIO()
    w, h = landscape(A4)
    lm, rm = 36, 36
    doc = SimpleDocTemplate(
        out, pagesize=(w, h), leftMargin=lm, rightMargin=rm,
        topMargin=91, bottomMargin=48,
        title='Conferência física de estoque - Stang', author='Intranet Stang',
    )
    largura = w - lm - rm
    estilos = {
        'secao': ParagraphStyle('Secao', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=AZUL),
        'colunas': ParagraphStyle('Colunas', fontName='Helvetica-Bold', fontSize=7.6, leading=9.1,
                                  textColor=colors.white, alignment=TA_LEFT),
        'celula': ParagraphStyle('Celulas', fontName='Helvetica', fontSize=8, leading=10,
                                 textColor=colors.black),
        'info': ParagraphStyle('Info', fontName='Helvetica', fontSize=9, leading=13,
                                textColor=CINZA_TEXTO),
        'rodape': ParagraphStyle('Rodape', fontName='Helvetica', fontSize=8, leading=11,
                                  textColor=CINZA_TEXTO),
    }
    story = [
        _paragrafo('FILIAL / UNIDADE: ' + (nome_filial or filial), estilos['info']),
        Spacer(1, 3),
        _paragrafo('Data da contagem: ____/____/________   Hora: ________   '
                   'Setor / local vistoriado: _______________________________________________', estilos['info']),
        Spacer(1, 3),
        _paragrafo('Responsável pela conferência: ___________________________________________   '
                   'Acompanhante: _______________________________________', estilos['info']),
        _paragrafo('Relatório emitido pelo login: ' + (usuario or 'não informado'), estilos['rodape']),
        Spacer(1, 9),
        HRFlowable(width='100%', thickness=0.7, color=BORDA),
        Spacer(1, 12),
    ]
    for tipo in categorias:
        da_categoria = [r for r in itens_validos if r['tipo'] == tipo]
        titulo = ('MATERIAIS' if tipo == 'MATERIAL' else 'FERRAMENTAS') + f'  -  {len(da_categoria)} itens cadastrados'
        _secao(story, titulo, da_categoria, cegas=contagem_cega,
               extras=linhas_extras, estilos=estilos, largura=largura)

    story.extend([
        Spacer(1, 6),
        _paragrafo('Divergências / avarias / itens não localizados / ações necessárias:', estilos['secao']),
        Spacer(1, 5),
        _paragrafo('________________________________________________________________________________________________________________________________________', estilos['rodape']),
        Spacer(1, 8),
        _paragrafo('________________________________________________________________________________________________________________________________________', estilos['rodape']),
        Spacer(1, 17),
        _paragrafo('Conferente: ___________________________________________    '
                   'Responsável pelo estoque: ___________________________________________', estilos['info']),
        Spacer(1, 6),
        _paragrafo('Assinatura: ___________________________________________    '
                   'Assinatura: _________________________________________________', estilos['info']),
        Spacer(1, 8),
        _paragrafo('Este formulário é para contagem física. Preencher os campos NÃO altera o saldo no sistema. '
                   'Após a inspeção, diferenças devem ser tratadas por movimento autorizado na Intranet.', estilos['rodape']),
    ])

    etiqueta = 'MATERIAIS + FERRAMENTAS' if len(categorias) == 2 else (
        'MATERIAIS' if categorias[0] == 'MATERIAL' else 'FERRAMENTAS')
    emitido = agora.strftime('%d/%m/%Y às %H:%M')

    def cabecalho(canvas, pdf):
        canvas.saveState()
        canvas.setStrokeColor(BORDA)
        canvas.setLineWidth(0.7)
        y = h - 36
        canvas.setFont('Helvetica-Bold', 12)
        canvas.setFillColor(AZUL)
        canvas.drawString(lm, y, 'STANG  |  CONTROLE DE ESTOQUE')
        canvas.setFont('Helvetica-Bold', 12)
        canvas.drawRightString(w-rm, y, 'FORMULÁRIO DE CONFERÊNCIA FÍSICA')
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(CINZA_TEXTO)
        canvas.drawString(lm, y-18, 'Categoria: ' + etiqueta + ('  |  CONTAGEM CEGA' if contagem_cega else ''))
        canvas.drawRightString(w-rm, y-18, 'Emitido em ' + emitido)
        canvas.line(lm, y-28, w-rm, y-28)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(CINZA_TEXTO)
        canvas.line(lm, 34, w-rm, 34)
        canvas.drawString(lm, 21, 'Relatório gerado pela Intranet Stang - conferência manual / sem alteração de estoque')
        canvas.drawRightString(w-rm, 21, f'Página {pdf.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=cabecalho, onLaterPages=cabecalho)
    return out.getvalue()
