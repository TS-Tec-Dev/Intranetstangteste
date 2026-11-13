"""Aplica somente ajustes de layout na v2.0.6, sobre arquivos atuais locais.

Uso (Python 3): python ajustar_quadros.py
Não altera módulos de dados ou Secrets. Mantém cópias .antes_v2061.
"""
from pathlib import Path
import ast
import shutil

ROOT = Path(__file__).resolve().parent

OLD_COMPRA = '''                df_view['Data_Apenas'] = df_view['Data_Solicitacao'].astype(str).str.split(" ").str[0]

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

'''
NEW_COMPRA = '''                # Os filtros legados da aba de compras foram removidos:
                # a pesquisa da tabela moderna faz a filtragem por todos os campos,
                # sem duplicar controles e sem alterar o CSV ou o Supabase.

'''

OLD_CONTROLES = '''    controles = st.columns([5, 1.5]) if busca else [None, None]
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
'''
NEW_CONTROLES = '''    # Busca, quantidade de registros e página ocupam UMA ÚNICA linha.
    # O seletor de página não cria uma linha em branco à esquerda.
    if busca:
        c_busca, c_tamanho, c_pagina = st.columns([5.2, 1.35, 1.65], gap='small')
        with c_busca:
            termo = st.text_input(
                'Buscar registros',
                placeholder='Buscar por código, nome, item ou descrição...',
                key=f'stg_busca_{chave}', label_visibility='collapsed')
    else:
        c_tamanho, c_pagina = st.columns([1, 1], gap='small')
        termo = ''

    if busca:
        with c_tamanho:
            tamanho = st.selectbox(
                'Linhas por página', [8, 12, 20, 50],
                index=[8, 12, 20, 50].index(por_pagina) if por_pagina in (8,12,20,50) else 1,
                key=f'stg_tamanho_{chave}',
                format_func=lambda n: f'{n} linhas', label_visibility='collapsed')
    else:
        tamanho = max(1, int(por_pagina))

    filtrado = preparar_tabela(df, termo, ordenar_por=ordenar_por, decrescente=decrescente)
    total = len(filtrado)
    paginas = max(1, math.ceil(total / tamanho))
    chave_pagina = f'stg_pagina_{chave}'
    pagina_atual = st.session_state.get(chave_pagina, 1)
    if not isinstance(pagina_atual, int) or not 1 <= pagina_atual <= paginas:
        # Ao reduzir os resultados da busca, retornar à primeira página.
        st.session_state[chave_pagina] = 1
    with c_pagina:
        pagina = st.selectbox(
            'Página', list(range(1, paginas + 1)),
            key=chave_pagina, format_func=lambda p: f'Página {p}/{paginas}',
            label_visibility='collapsed', disabled=(paginas == 1))
'''

REPLACES = {
    'Intranet.py':(OLD_COMPRA, NEW_COMPRA),
    'quadros_modernos.py':(OLD_CONTROLES, NEW_CONTROLES),
}

def aplicar(raiz: Path = ROOT) -> list[Path]:
    saidas = {}
    for nome,(antigo,novo) in REPLACES.items():
        arquivo = raiz / nome
        origem = arquivo.read_text(encoding='utf-8')
        n = origem.count(antigo)
        if n != 1:
            raise RuntimeError(f'{nome}: trecho esperado encontrado {n} vezes; nenhum arquivo foi alterado. Confira a versao atual.')
        conteudo = origem.replace(antigo, novo)
        ast.parse(conteudo, filename=nome)
        saidas[arquivo] = conteudo
    for arquivo in saidas:
        shutil.copy2(arquivo, str(arquivo)+'.antes_v2061')
    for arquivo, conteudo in saidas.items():
        arquivo.write_text(conteudo, encoding='utf-8')
    return list(saidas)

if __name__ == '__main__':
    for arquivo in aplicar():
        print('Atualizado:', arquivo.name)
