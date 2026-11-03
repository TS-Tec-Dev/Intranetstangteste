# Intranet Stang v2.0.5 — Backup unificado, status e visualização

## Arquivos do pacote

Envie **todos os sete arquivos** para a raiz do repositório **de teste** `Intranetstangteste`:

- `Intranet.py` — seletor de pedidos com status 5/5 e visualização de anexos.
- `visualizacao_documentos.py` — módulo novo; prévia de PDF como imagem e leitura de imagens do Supabase.
- `mod_filiais_backup.py` — substitui múltiplas abas por Exportar e Importar backup.
- `backup_unificado.py` — módulo novo; um único backup criptografado.
- `backup_contas.py` — compatibilidade da restauração de contas com a transação geral.
- `infra_persistencia.py` — restauração transacional de filiais, RH e anexos.
- `requirements.txt` — assegura que `PyMuPDF` esteja instalado para renderizar páginas do PDF.

**Não sobrescreva**: `.streamlit/secrets.toml`, arquivos `banco_*.csv`, documentos,
`controle_acesso_filiais.py`, `mod_rh.py`, `mod_ferramentaria.py`, `mod_fm_biblioteca.py`,
`migrar_usuarios_anterior.py`, `tema_stang.py` ou `seguranca_senhas.py`.

## Antes do upload

1. Faça backup ZIP atual da filial e `.stangbak` das contas, usando a versão atualmente instalada.
2. Guarde os dois arquivos em local privado. **Não envie arquivos de dados para o GitHub.**
3. Publique os sete arquivos somente no repositório de teste.
4. Se a aplicação não atualizar automaticamente, use Streamlit Cloud → Manage app → Reboot app.
5. Mantenha a `DATABASE_URL` e os Secrets como estão; não recrie o banco.

## Como funciona

### Backup — duas abas

`🏢 Filiais e Backup → 📤 Exportar backup / 📥 Importar backup`.

- **Exportar**: solicita senha atual de administrador geral (autorizado a todas as filiais), senha de proteção de pelo menos 14 caracteres e confirmação. Gera UM arquivo `.stangtotal` criptografado com AES-GCM. Inclui todos os usuários, filiais, CSVs, PDFs, assinaturas, ferramentaria e RH.
- **Importar**: confira a integridade e prévia, gere e BAIXE primeiro um backup dos dados atuais, marque a confirmação e digite `RESTAURAR`. A importação usa uma transação de banco para todos os dados. Se falhar durante a operação, as gravações são revertidas.

**ATENÇÃO:** backups antigos `.zip` e `.stangbak` não são aceitos diretamente no novo importador `.stangtotal`. Guarde-os como recuperação de segurança da versão anterior. A restauração de contas mescla os logins e mantém eventuais usuários extras não presentes no arquivo; já os dados operacionais das filiais no arquivo são substituídos.

A função completa só é liberada para administradores com autorização explícita a TODAS as filiais, preservando as restrições anteriores. Use preferencialmente fora do horário com novos lançamentos, pois o backup consulta mais de uma tabela.

O novo formato suporta até 195 MB de arquivo criptografado para ser compatível com o limite de 200 MB do uploader. Para volume maior, será necessário um fluxo de backup externo próprio.

### Painel de Assinaturas

Ao abrir a lista de pedidos verá `✅ OK | Enviado (5/5)` quando estiverem preenchidos no cadastro: orçamento, nota fiscal, boleto, assinatura e registro de e-mail enviado. Se faltar algo, `⏳ Pendente (x/5)`. O status da lista usa **metadados** do pedido; ao selecionar, a aplicação confere a disponibilidade dos anexos no banco.

Para **pré-visualização de PDF**, selecione o pedido e marque `👁️ Pré-visualizar` em Orçamento/NF/Boleto. O servidor transforma a página selecionada em imagem. Isso evita o `object data:` em Base64, que alguns browsers bloqueiam. É possível continuar baixando o arquivo original sem alterações.

### Cuidados

- O programa não modifica as regras de compras, RH, FMs, estoques e e-mail.
- O arquivo `.stangtotal` contém credenciais SMTP dentro da criptografia; mantenha a senha de proteção em local separado.
- Os registros continuam no Supabase; atualizar o GitHub não apaga o banco.
- Valide primeiro no app de teste. Faça backup antes de migrar a versão para o endereço oficial.
- Os erros JavaScript `Failed to fetch dynamically imported module` são do carregamento de componentes e podem exigir limpeza de cache/reboot e verificação de rede; esta atualização melhora a prévia PDF, mas não corrige falhas de distribuição dos arquivos JS do Streamlit.

## Testes locais

- Geração e leitura do backup de duas filiais.
- Senha incorreta rejeitada.
- Restauração dos documentos e usuários existentes.
- Falha simulada no meio da restauração: rollback da transação.
- Resumo do pedido: 5/5, 4/5 e registros divergentes.
- Renderização da primeira página de um PDF FM original em PNG.

**Não foi testado contra o Supabase de produção nem publicado no seu GitHub.**
