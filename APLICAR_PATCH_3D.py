"""Alternativa para quem alterou Intranet.py após a versão-base dos ícones 3D.
Execute na pasta de um Intranet.py atual e do menu_icones_3d.py.
Não altera nenhum dado, apenas um bloco visual no código.
"""
from pathlib import Path
import shutil

alvo=Path('Intranet.py')
if not alvo.exists():
    raise SystemExit('Não encontrei Intranet.py na pasta atual.')
if not Path('menu_icones_3d.py').exists():
    raise SystemExit('Coloque menu_icones_3d.py na mesma pasta.')
texto=alvo.read_text(encoding='utf-8')
if 'menu_icones_3d.aplicar_estilo(menus_disponiveis)' in texto:
    raise SystemExit('Os ícones 3D já estão integrados; não apliquei novamente.')
procurar='''    if menus_disponiveis:
        menu = st.radio("Navegação Principal", menus_disponiveis)
'''
substituir='''    if menus_disponiveis:
        # Apenas aparência. Os valores do menu continuam intactos.
        try:
            import menu_icones_3d
            menu_icones_3d.aplicar_estilo(menus_disponiveis)
            _rotulo_menu = menu_icones_3d.rotulo_menu
        except (ImportError, OSError, ValueError):
            _rotulo_menu = str
        menu = st.radio("Navegação Principal", menus_disponiveis, format_func=_rotulo_menu)
'''
if texto.count(procurar)!=1:
    raise SystemExit('Não encontrei o bloco de navegação esperado; nenhuma alteração feita.')
backup=alvo.with_name('Intranet.py.antes_menu_3d.bak')
if backup.exists():
    raise SystemExit(f'Arquivo de backup {backup} já existe; nenhuma alteração feita.')
novo=texto.replace(procurar,substituir)
compile(novo,str(alvo),'exec')
shutil.copy2(alvo,backup)
alvo.write_text(novo,encoding='utf-8')
print('Menu 3D integrado! Backup anterior:',backup)
