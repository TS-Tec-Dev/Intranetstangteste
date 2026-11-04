"""Visualização de imagens e PDFs de pedidos da Intranet Stang.

Correção v2.0.5.1 (03/11/2026)
- Busca documentos diretamente na tabela de anexos da filial.
- Apresenta imagens e páginas PDF como data:image embutido na própria página;
  isso evita depender do endpoint /media de st.image, que pode falhar de forma
  intermitente em sessões/reexecuções do Streamlit Community Cloud.
- Mantém o download do arquivo ORIGINAL; a conversão só ocorre na prévia.
- Não altera documentos, tabelas, hashes, permissões nem caminho de backup.
"""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import streamlit as st
from sqlalchemy import select

import infra_persistencia as repo


REVISAO_VISUALIZADOR = 'v2.0.5.1-2026-11-03'
MAX_PIXELS_ENTRADA = 45_000_000
MAX_LARGURA_PREVIA = 1200
MAX_ALTURA_PREVIA = 1700


def _documento_bytes(caminho: str) -> bytes | None:
    """Obtém o anexo do escopo autorizado; só aceita fallback legado seguro."""
    caminho = repo._doc_valid(caminho)
    path = Path(caminho)
    partes = path.parts
    if len(partes) >= 4 and partes[:2] == ('.runtime_data', 'cache'):
        if partes[2] not in (repo._active(), repo.GLOBAL):
            return None

    escopo = repo.GLOBAL if 'uploads_assinaturas/' in caminho else repo._active()
    with repo.engine().connect() as conn:
        conteudo = conn.execute(
            select(repo.docs_tb.c.conteudo).where(
                (repo.docs_tb.c.filial == escopo)
                & (repo.docs_tb.c.caminho == caminho)
            )
        ).scalar_one_or_none()
    if conteudo is not None:
        return bytes(conteudo)

    # Arquivo legado permitido apenas na filial original ou no escopo GLOBAL.
    if (repo._active() == repo.DEFAULT_FILIAL or escopo == repo.GLOBAL) and path.is_file():
        if path.resolve().is_relative_to(repo.BASE_DIR.resolve()):
            return path.read_bytes()
    return None


def _identificar_formato(data: bytes) -> str:
    """Detecta pelo conteúdo, e não pela extensão do arquivo."""
    if data.startswith(b'%PDF-'):
        return 'pdf'
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            return 'image' if img.format in ('PNG', 'JPEG', 'WEBP', 'TIFF', 'GIF', 'BMP') else 'outro'
    except (OSError, ValueError, SyntaxError):
        return 'outro'


def _preparar_imagem(data: bytes) -> tuple[bytes, str]:
    """Normaliza foto digitalizada (incluindo orientação EXIF) para prévia pequena."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > MAX_PIXELS_ENTRADA:
            raise ValueError('Imagem muito grande para pré-visualização.')
        imagem = ImageOps.exif_transpose(original)
        imagem.thumbnail((MAX_LARGURA_PREVIA, MAX_ALTURA_PREVIA), Image.Resampling.LANCZOS)
        # Fundo branco: imagens em PNG com transparência não desaparecem no tema escuro.
        if imagem.mode in ('RGBA', 'LA') or 'transparency' in imagem.info:
            fundo = Image.new('RGB', imagem.size, 'white')
            if imagem.mode != 'RGBA':
                imagem = imagem.convert('RGBA')
            fundo.paste(imagem, mask=imagem.getchannel('A'))
            imagem = fundo
        else:
            imagem = imagem.convert('RGB')
        buf = io.BytesIO()
        # Texto/diagramas em PNG mantêm nitidez; fotos em JPEG reduzem transferência.
        fmt = 'PNG' if original.format in ('PNG', 'BMP', 'GIF') else 'JPEG'
        if fmt == 'PNG':
            imagem.save(buf, format='PNG', optimize=True)
            return buf.getvalue(), 'image/png'
        imagem.save(buf, format='JPEG', quality=90, optimize=True)
        return buf.getvalue(), 'image/jpeg'


def _total_paginas(pdf: bytes) -> int:
    import fitz
    with fitz.open(stream=pdf, filetype='pdf') as documento:
        return documento.page_count


def _renderizar_pdf(pdf: bytes, indice: int) -> tuple[bytes, str]:
    """Rasteriza UMA página por vez; não devolve HTML/PDF incorporado."""
    import fitz
    with fitz.open(stream=pdf, filetype='pdf') as documento:
        if not 0 <= indice < documento.page_count:
            raise ValueError('Página não disponível.')
        pagina = documento.load_page(indice)
        # Mantém a imagem legível sem gerar previews gigantes no navegador.
        escala = min(
            1.45,
            MAX_LARGURA_PREVIA / max(pagina.rect.width, 1),
            MAX_ALTURA_PREVIA / max(pagina.rect.height, 1),
        )
        imagem = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala), alpha=False, colorspace=fitz.csRGB)
        return imagem.tobytes('png'), 'image/png'


def _mostrar_inline(imagem: bytes, mime: str, legenda: str) -> None:
    """HTML estático com data URI: não usa o MediaFileHandler de st.image.

    O HTML recebe apenas uma imagem re-encodada pelo Pillow/PyMuPDF;
    nomes de arquivo nunca viram código HTML sem escape.
    """
    if mime not in ('image/png', 'image/jpeg'):
        raise ValueError('Formato visual não permitido.')
    encoded = base64.b64encode(imagem).decode('ascii')
    alt = html.escape(legenda, quote=True)
    st.markdown(
        '<div style="width:100%;padding:6px;background:#fff;'
        'border:1px solid rgba(100,116,139,.3);border-radius:9px;box-sizing:border-box">'
        f'<img src="data:{mime};base64,{encoded}" alt="{alt}" '
        'style="display:block;width:100%;height:auto;object-fit:contain;'
        'max-height:1600px;margin:0 auto" loading="eager">'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption(legenda)


def exibir_documento(caminho, titulo: str, *, chave: str):
    ausentes = ('', 'none', 'nan', 'null')
    if caminho is None or str(caminho).strip().lower() in ausentes:
        st.caption(f'{titulo}: não anexado.')
        return
    try:
        bruto = _documento_bytes(str(caminho))
    except (ValueError, OSError):
        bruto = None
    if not bruto:
        st.warning(f'{titulo}: arquivo não encontrado na filial selecionada. Verifique o vínculo do documento.')
        return

    nome = Path(str(caminho)).name
    tipo = _identificar_formato(bruto)
    # Mantém o binário no download, inclusive o PDF assinado e/ou a foto original.
    nome_download = nome
    if tipo == 'pdf' and not nome.lower().endswith('.pdf'):
        nome_download += '.pdf'
    tipo_mime = ('application/pdf' if tipo == 'pdf' else
                 'image/jpeg' if tipo == 'image' and bruto[:3] == b'\xff\xd8\xff' else
                 'image/png' if tipo == 'image' and bruto[:8] == b'\x89PNG\r\n\x1a\n' else
                 'application/octet-stream')
    st.download_button(f'📥 Baixar {titulo}', data=bruto, file_name=nome_download,
                       mime=tipo_mime, use_container_width=True, key=f'download_{chave}')

    ver = st.checkbox(f'👁️ Pré-visualizar {titulo}', key=f'preview_{chave}')
    if not ver:
        return
    try:
        if tipo == 'image':
            preview, mime = _preparar_imagem(bruto)
            _mostrar_inline(preview, mime, nome)
        elif tipo == 'pdf':
            paginas = _total_paginas(bruto)
            if paginas == 0:
                st.warning('Este PDF não possui páginas.')
                return
            pagina = st.selectbox(
                f'Página de {titulo}', options=range(1, paginas + 1),
                key=f'pagina_{chave}', format_func=lambda n: f'Página {n} / {paginas}',
            )
            preview, mime = _renderizar_pdf(bruto, pagina - 1)
            _mostrar_inline(preview, mime, f'{titulo} — página {pagina}/{paginas}')
        else:
            st.warning('Não foi possível identificar um PDF ou imagem válida neste arquivo. Use o download original.')
    except Exception as exc:
        # Um documento defeituoso não pode derrubar o painel inteiro.
        # Não mostrar bytes/caminho do arquivo ou a URI do banco em mensagens públicas.
        st.warning(f'Não foi possível gerar a prévia de {titulo}. O arquivo original continua disponível para download.')
        st.caption(f'Detalhe técnico: {type(exc).__name__}.')
