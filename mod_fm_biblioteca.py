"""Biblioteca de modelos PDF de Formulários (FMs), persistidos no PostgreSQL.

Não depende do disco do Streamlit nem dos PDFs no repositório GitHub.
O acervo é independente do calendário de vencimentos ``banco_fms.csv``.
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import PurePosixPath

import pandas as pd
import streamlit as st
from sqlalchemy import select, delete, insert

import infra_persistencia as repo

PREFIXO = "uploads_fms/"
MAX_PDF = 20 * 1024 * 1024
MAX_ZIP = 70 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_ARQUIVOS = 100


def codigo_fm(texto: str) -> str:
    encontrado = re.search(r"(?i)\bFM[\s._-]*(\d{1,3})(?!\d)", str(texto))
    if not encontrado or int(encontrado.group(1)) < 1:
        raise ValueError("O nome precisa começar com um código, por exemplo: FM 01 - Gerador.pdf")
    return f"FM {int(encontrado.group(1)):02d}"


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"(?i)^FM[\s._-]*\d{1,3}\s*", "", texto).strip(" -_.")
    texto = re.sub(r"[^a-zA-Z0-9_-]+", "_", texto).strip("_")
    return texto[:95] or "Formulario"


def _caminho(codigo: str, titulo: str) -> str:
    return f"{PREFIXO}{codigo.replace(' ', '_')}__{_slug(titulo)}.pdf"


def _codigo_do_caminho(caminho: str) -> str:
    nome = PurePosixPath(caminho).name
    return codigo_fm(nome.split("__", 1)[0])


def _titulo_do_caminho(caminho: str) -> str:
    nome = PurePosixPath(caminho).stem
    return nome.partition("__")[2].replace("_", " ") or "Formulário"


def listar(filial: str) -> list[dict]:
    """Lista apenas os PDFs da filial informada, sem ler bytes grandes."""
    with repo.engine().connect() as conn:
        regs = conn.execute(select(repo.docs_tb.c.caminho, repo.docs_tb.c.alterado_em).where(
            (repo.docs_tb.c.filial == filial) & repo.docs_tb.c.caminho.startswith(PREFIXO)
        ).order_by(repo.docs_tb.c.caminho)).mappings().all()
    resultado = []
    for reg in regs:
        try:
            codigo = _codigo_do_caminho(reg['caminho'])
        except ValueError:
            continue
        resultado.append({
            'codigo': codigo, 'titulo': _titulo_do_caminho(reg['caminho']),
            'caminho': reg['caminho'], 'alterado_em': reg['alterado_em']
        })
    return sorted(resultado, key=lambda r: (int(r['codigo'].split()[-1]), r['titulo']))


def obter(filial: str, caminho: str) -> bytes:
    if not str(caminho).startswith(PREFIXO):
        raise ValueError('Documento inválido')
    with repo.engine().connect() as conn:
        dados = conn.execute(select(repo.docs_tb.c.conteudo).where(
            (repo.docs_tb.c.filial == filial) & (repo.docs_tb.c.caminho == caminho)
        )).scalar_one_or_none()
    if dados is None:
        raise FileNotFoundError('PDF não encontrado nesta filial.')
    return bytes(dados)


def _validar_pdf(nome: str, dados: bytes) -> tuple[str, str]:
    if not str(nome).lower().endswith('.pdf'):
        raise ValueError(f'{nome}: apenas PDF é aceito.')
    if not dados.startswith(b'%PDF-'):
        raise ValueError(f'{nome}: o conteúdo não é um PDF válido.')
    if not 0 < len(dados) <= MAX_PDF:
        raise ValueError(f'{nome}: PDF vazio ou maior que 20 MB.')
    codigo = codigo_fm(PurePosixPath(nome).stem)
    titulo = re.sub(r'(?i)^FM[\s._-]*\d{1,3}\s*', '', PurePosixPath(nome).stem).strip(' -_.')
    return codigo, titulo or 'Formulario'


def importar(filial: str, arquivos: list[tuple[str, bytes]], substituir: bool = False) -> dict:
    """Importa vários PDFs em uma transação. Evita alterações parciais."""
    if not arquivos or len(arquivos) > MAX_ARQUIVOS:
        raise ValueError('Selecione de 1 a 100 PDFs por importação.')
    if sum(len(dados) for _, dados in arquivos) > MAX_TOTAL:
        raise ValueError('Total de PDFs acima do limite de 100 MB.')
    planos: dict[str, tuple[str, bytes]] = {}
    for nome, dados in arquivos:
        codigo, titulo = _validar_pdf(nome, dados)
        if codigo in planos:
            raise ValueError(f'Código {codigo} repetido na mesma importação.')
        planos[codigo] = (_caminho(codigo, titulo), dados)
    criados, atualizados, ignorados = 0, 0, 0
    with repo.engine().begin() as conn:
        existentes = conn.execute(select(repo.docs_tb.c.caminho).where(
            (repo.docs_tb.c.filial == filial) & repo.docs_tb.c.caminho.startswith(PREFIXO)
        )).scalars().all()
        por_codigo = {}
        for antigo in existentes:
            try:
                por_codigo.setdefault(_codigo_do_caminho(antigo), []).append(antigo)
            except ValueError:
                continue
        for codigo, (caminho, dados) in planos.items():
            if codigo in por_codigo and not substituir:
                ignorados += 1
                continue
            for anterior in por_codigo.get(codigo, []):
                conn.execute(delete(repo.docs_tb).where(
                    (repo.docs_tb.c.filial == filial) & (repo.docs_tb.c.caminho == anterior)
                ))
            conn.execute(insert(repo.docs_tb).values(
                filial=filial, caminho=caminho, conteudo=dados, alterado_em=repo._now()
            ))
            if codigo in por_codigo:
                atualizados += 1
            else:
                criados += 1
    return {'adicionados': criados, 'substituidos': atualizados, 'ignorados': ignorados}


def excluir(filial: str, caminho: str) -> None:
    if not str(caminho).startswith(PREFIXO):
        raise ValueError('Documento inválido')
    with repo.engine().begin() as conn:
        result = conn.execute(delete(repo.docs_tb).where(
            (repo.docs_tb.c.filial == filial) & (repo.docs_tb.c.caminho == caminho)
        ))
        if result.rowcount != 1:
            raise ValueError('O formulário não existe nesta filial.')


def _ler_zip(dados: bytes) -> list[tuple[str, bytes]]:
    if len(dados) > MAX_ZIP:
        raise ValueError('ZIP acima do limite de 70 MB.')
    arquivos = []
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            infos = [x for x in z.infolist() if not x.is_dir() and x.filename.lower().endswith('.pdf')]
            if len(infos) > MAX_ARQUIVOS:
                raise ValueError('O ZIP contém mais de 100 PDFs.')
            if sum(i.file_size for i in infos) > MAX_TOTAL:
                raise ValueError('Os PDFs do ZIP ultrapassam 100 MB descompactados.')
            # Só descompacta arquivos PDF depois de verificar o tamanho declarado.
            for info in infos:
                p = PurePosixPath(info.filename.replace('\\', '/'))
                if '..' in p.parts or info.file_size > MAX_PDF:
                    raise ValueError('O ZIP possui caminho inválido ou PDF acima de 20 MB.')
                arquivos.append((p.name, z.read(info)))
    except zipfile.BadZipFile as e:
        raise ValueError('O arquivo enviado não é um ZIP válido.') from e
    return arquivos


def _montar_zip(filial: str, modelos: list[dict]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for modelo in modelos:
            z.writestr(PurePosixPath(modelo['caminho']).name, obter(filial, modelo['caminho']))
    return saida.getvalue()


def renderizar(filial: str, administrador: bool = False) -> None:
    st.subheader('📁 Biblioteca de modelos de Formulários (FM)')
    st.caption('PDFs armazenados permanentemente no banco externo, separados por filial. O calendário de vencimentos é controlado nas outras abas.')
    documentos = listar(filial)
    st.metric('Modelos PDF disponíveis', len(documentos))
    if documentos:
        busca = st.text_input('Pesquisar código ou nome do formulário', key='busca_biblioteca_fms')
        visiveis = [r for r in documentos if busca.casefold() in f"{r['codigo']} {r['titulo']}".casefold()]
        if visiveis:
            st.dataframe(pd.DataFrame([{'Código': r['codigo'], 'Nome do formulário': r['titulo']} for r in visiveis]),
                         hide_index=True, use_container_width=True)
            opcoes = {f"{r['codigo']} — {r['titulo']}":r for r in visiveis}
            selecionado = st.selectbox('Selecione um FM para baixar', list(opcoes), key='selecionar_biblioteca_fm')
            modelo = opcoes[selecionado]
            st.download_button('📥 Baixar PDF selecionado', data=obter(filial, modelo['caminho']),
                file_name=PurePosixPath(modelo['caminho']).name, mime='application/pdf',
                key='download_fm_pdf', use_container_width=True)
        else:
            st.info('Nenhum modelo corresponde à pesquisa.')
        with st.expander('📦 Download de todos os modelos em ZIP'):
            if st.checkbox('Preparar ZIP de todos os modelos PDF desta filial', key='preparar_zip_fm'):
                st.download_button('📦 Baixar biblioteca completa ZIP', data=_montar_zip(filial, documentos),
                                   file_name=f'Biblioteca_FMs_{filial}.zip', mime='application/zip',
                                   key='download_biblioteca_zip')
    else:
        st.info('Nenhum PDF cadastrado nesta filial. O administrador pode adicionar o acervo abaixo.')

    if not administrador:
        st.caption('🔒 A inclusão, substituição e exclusão de PDFs está disponível para administradores.')
        return

    st.divider()
    st.markdown('#### ➕ Adicionar modelos PDF')
    st.caption('Nomeie cada PDF com o número do FM (ex.: "FM 19 Inspeção de Bombas.pdf"). Você também pode importar o ZIP dos 17 modelos fornecidos.')
    pdfs = st.file_uploader('Adicionar PDF(s) individualmente', type=['pdf'], accept_multiple_files=True, key='fm_pdfs_individuais')
    zipped = st.file_uploader('Ou importar vários FMs em um arquivo ZIP', type=['zip'], key='fm_zip_em_lote')
    substituir = st.checkbox('Substituir PDFs existentes quando o código FM já estiver cadastrado', value=False, key='fm_substituir')
    if st.button('💾 Salvar modelos de FM no Supabase', type='primary', key='fm_salvar_docs'):
        try:
            if pdfs and zipped:
                raise ValueError('Envie os PDFs individuais OU um ZIP por vez.')
            entrada = ([(p.name, p.getvalue()) for p in pdfs] if pdfs else _ler_zip(zipped.getvalue()) if zipped else [])
            resultado = importar(filial, entrada, substituir=substituir)
            st.success(f"Concluído: {resultado['adicionados']} adicionados, {resultado['substituidos']} substituídos, {resultado['ignorados']} já existentes ignorados.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))
        except Exception:
            st.error('Não foi possível gravar os PDFs. Confira a conexão com o Supabase e os arquivos.')

    if documentos:
        st.divider()
        st.markdown('#### 🗑️ Excluir modelo PDF')
        st.caption('A exclusão do PDF não remove o FM do controle de vencimentos nem o histórico já cadastrado.')
        opcoes = {f"{r['codigo']} — {r['titulo']}":r for r in documentos}
        escolhido = st.selectbox('Modelo a excluir', list(opcoes), key='fm_doc_excluir')
        with st.form('confirmar_exclusao_doc_fm'):
            ok = st.checkbox('Confirmo a exclusão do PDF selecionado')
            if st.form_submit_button('🗑️ Excluir PDF'):
                if not ok:
                    st.error('Marque a confirmação para excluir.')
                else:
                    try:
                        excluir(filial, opcoes[escolhido]['caminho'])
                        st.success('PDF excluído do acervo desta filial.')
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
