"""Aplica assunto e corpo editáveis ao Intranet.py existente, sem substituir o restante.
Uso: python aplicar_patch_seguro.py [CAMINHO/DO/Intranet.py]
"""
from pathlib import Path
import sys
import ast
import shutil

arquivo = Path(sys.argv[1] if len(sys.argv)>1 else 'Intranet.py').resolve()
if not arquivo.is_file():
    raise SystemExit(f'Arquivo não encontrado: {arquivo}')
original = arquivo.read_text(encoding='utf-8')
if 'key=f"assunto_email_pedido_{id_sel_ass}"' in original:
    raise SystemExit('Essa atualização já está instalada. Nada foi alterado.')

old_form = '''                    dest_email = st.text_input("E-mail do Destinatário", value="financeiro@stang.com.br")
                    obs_email = st.text_area("Observações do E-mail", value="", height=100)
'''
new_form = '''                    dest_email = st.text_input("E-mail do Destinatário", value="financeiro@stang.com.br")
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
'''
old_check = '''                        if not dest_email:
                            st.error("Informe o e-mail do destinatário.")
                        else:
                            df_u_l = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
'''
new_check = '''                        if not dest_email.strip():
                            st.error("Informe o e-mail do destinatário.")
                        elif not assunto_email.strip():
                            st.error("Informe o assunto do e-mail antes de enviar.")
                        elif "\\r" in assunto_email or "\\n" in assunto_email:
                            st.error("O assunto do e-mail deve ocupar apenas uma linha.")
                        else:
                            df_u_l = repositorio.ler_csv(ARQUIVO_USERS, dtype=str)
'''
old_send = '''                                corpo_dinamico = f"Documentação referente ao Pedido de Compra #{id_sel_ass}.\\n\\nObservações: {obs_email}" if obs_email else f"Documentação referente ao Pedido de Compra #{id_sel_ass}."

                                sucesso_e, msg_e = enviar_email_real(
                                    destinatario=dest_email,
                                    assunto=f"Documentação do Pedido de Compra #{id_sel_ass} - Intranet Stang",
                                    corpo=corpo_dinamico,
'''
new_send = '''                                sucesso_e, msg_e = enviar_email_real(
                                    destinatario=dest_email.strip(),
                                    assunto=assunto_email.strip(),
                                    corpo=mensagem_email,
'''
novo = original
for label,antigo,novo_bloco in (
    ('formulário',old_form,new_form),
    ('validação',old_check,new_check),
    ('envio de e-mail',old_send,new_send),
):
    if novo.count(antigo) != 1:
        raise SystemExit(f'Não foi possível localizar o bloco de {label} uma única vez. Nenhum arquivo foi alterado.')
    novo=novo.replace(antigo,novo_bloco,1)

ast.parse(novo)
backup = arquivo.with_suffix('.py.antes_assunto.bak')
if backup.exists():
    raise SystemExit(f'Já existe um backup em {backup}. Mova-o antes de continuar. Nenhum arquivo foi alterado.')
shutil.copy2(arquivo,backup)
arquivo.write_text(novo,encoding='utf-8')
print('Atualizado:',arquivo)
print('Backup do original:', backup)
print('Verificação de sintaxe: OK')
