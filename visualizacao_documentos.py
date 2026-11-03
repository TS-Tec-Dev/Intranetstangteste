"""Visualizador seguro de anexos: lê o documento da filial e renderiza PDF no servidor.

Não utiliza <object data:application/pdf;base64> nem URLs públicas para os anexos.
O download mantém o binário original, e a visualização não depende do plugin PDF
ou das permissões de data-URI do navegador.
"""
from __future__ import annotations

import io
from pathlib import Path

import streamlit as st
from sqlalchemy import select
import infra_persistencia as repo


def _documento_bytes(caminho: str) -> bytes | None:
    caminho = repo._doc_valid(caminho)
    path = Path(caminho)
    # Não aceitar um documento em cache de outra filial, mesmo se o caminho
    # tiver sido gravado incorretamente em um registro da filial atual.
    partes = path.parts
    if len(partes) >= 4 and partes[:2] == ('.runtime_data', 'cache'):
        filial_no_caminho = partes[2]
        if filial_no_caminho not in (repo._active(), repo.GLOBAL):
            return None
    escopo = repo.GLOBAL if 'uploads_assinaturas/' in caminho else repo._active()
    with repo.engine().connect() as conn:
        raw = conn.execute(select(repo.docs_tb.c.conteudo).where(
            (repo.docs_tb.c.filial == escopo) & (repo.docs_tb.c.caminho == caminho)
        )).scalar_one_or_none()
    if raw is not None:
        return bytes(raw)
    # Compatibilidade com documentos originais anteriores à migração.
    # Não procurar arquivos legados de outras filiais.
    if (repo._active() == repo.DEFAULT_FILIAL or escopo == repo.GLOBAL) and path.is_file():
        base = repo.BASE_DIR.resolve()
        if path.resolve().is_relative_to(base):
            return path.read_bytes()
    return None


def _pdf_primeira_pagina(pdf: bytes, numero: int = 0):
    import fitz  # PyMuPDF, já listado nas dependências do projeto.
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        if doc.page_count < 1:
            raise ValueError('PDF sem páginas.')
        if numero < 0 or numero >= doc.page_count:
            raise ValueError('Página fora do intervalo do PDF.')
        pix = doc.load_page(numero).get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        return pix.tobytes('png'), doc.page_count


def exibir_documento(caminho, titulo: str, *, chave: str):
    ausentes = ('', 'none', 'nan', 'null')
    if caminho is None or str(caminho).strip().lower() in ausentes:
        st.caption(f'{titulo}: não anexado.')
        return
    try:
        raw = _documento_bytes(str(caminho))
    except (ValueError, OSError):
        raw = None
    if not raw:
        st.warning(f'{titulo}: arquivo não encontrado na filial selecionada. Verifique o vínculo do documento.')
        return

    nome = Path(str(caminho)).name
    is_pdf = raw.startswith(b'%PDF-')
    if not is_pdf:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                imagem_tipo = image.format
                image.verify()
            is_image = imagem_tipo in ('PNG', 'JPEG', 'WEBP', 'TIFF', 'GIF', 'BMP')
        except (OSError, ValueError, SyntaxError):
            is_image = False
    else:
        is_image = False
    mime = 'application/pdf' if is_pdf else ('image/png' if is_image else 'application/octet-stream')
    # Se o arquivo original foi nomeado incorretamente, respeitar o conteúdo.
    if is_pdf and not nome.lower().endswith('.pdf'):
        nome += '.pdf'
    st.download_button(f'📥 Baixar {titulo}', data=raw, file_name=nome, mime=mime,
                       use_container_width=True, key=f'download_{chave}')
    ver = st.checkbox(f'👁️ Pré-visualizar {titulo}', key=f'preview_{chave}')
    if not ver:
        return
    if is_image:
        st.image(raw, caption=nome, use_container_width=True)
    elif is_pdf:
        try:
            import fitz
            with fitz.open(stream=raw, filetype='pdf') as doc:
                paginas = doc.page_count
            if paginas < 1:
                st.warning('O PDF não contém páginas para exibição.')
                return
            pagina = st.selectbox(f'Página de {titulo}', options=list(range(1, paginas + 1)),
                                  key=f'pagina_{chave}', format_func=lambda x: f'Página {x} / {paginas}')
            render, _ = _pdf_primeira_pagina(raw, pagina - 1)
            st.image(render, caption=f'{titulo} — página {pagina}/{paginas}', use_container_width=True)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            st.warning('Não foi possível gerar a imagem desta página. Você ainda pode baixar o PDF original.')
    else:
        st.info('Formato de documento sem pré-visualização. Use o botão de download.')
