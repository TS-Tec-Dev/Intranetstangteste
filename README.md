# Intranet Stang v2.0.1 — Resgate seguro de compras, O.S., FMs e anexos

**NOVO NA V2.0.1:** A tela **🏢 Filiais e Backup → 📤 Importar resgate 2026**
recebe o ZIP `IntranetStang_Backup_Consolidado_19-10-2026.zip`.

**ESTE ZIP DE CÓDIGO NÃO CONTÉM DADOS EMPRESARIAIS.** Os CSVs, credenciais,
orçamentos, notas e PDFs de clientes/fornecedores devem ser mantidos fora do GitHub,
preferencialmente em armazenamento privado. Não envie o backup consolidado para o
repositório GitHub público.

## Como implantar SEM APAGAR o banco existente

1. **Antes de fazer qualquer commit no GitHub**, guarde o ZIP de resgate consolidado
   no computador. Verifique se o app antigo ainda tem dados mais recentes que o resgate;
   exporte-os primeiro, pois nenhuma atualização recupera arquivos que já desapareceram.
2. Configure `DATABASE_URL` como Secret no painel do Streamlit usando PostgreSQL
   persistente (ex. Supabase), conforme orientações abaixo. Essa etapa é OBRIGATÓRIA
   para que o banco sobreviva a novas publicações do GitHub.
3. Configure também `BOOTSTRAP_ADMIN_PASSWORD` (12+ caracteres) caso esteja abrindo
   um banco **novo sem usuários**. Nesse caso, o login inicial é `admin` com essa senha.
   A exportação visual de usuários não contém hashes de senha nem permissões;
   portanto os usuários não são recriados a partir dessa planilha.
4. Coloque no GitHub **somente os arquivos deste ZIP de código**; envie também arquivos
   auxiliares de layout. Nunca suba `banco_usuarios.csv`, `banco_compras.csv`,
   `banco_os.csv`, `banco_fms.csv` ou `pedido_*.pdf/png/jpeg`.
   Para arquivos CSV/PDF que já foram commitados, `.gitignore` não os remove do histórico;
   retire-os do GitHub e troque credenciais expostas.
5. Depois do deploy, entre como administrador, selecione a filial **MANDALLA** e
   abra **🏢 Filiais e Backup → 📤 Importar resgate 2026**.
6. Carregue o **ZIP de resgate consolidado** (separado deste pacote de código),
   leia a simulação e baixe um backup do estado atual da filial pelo botão dessa tela.
7. Marque a confirmação e clique **✅ Importar dados recuperados**.
8. Verifique abas O.S., FMs, solicitações, anexos, relatórios e dashboards.
   Faça um novo backup ZIP após validar o resultado. Se o banco existente já tinha
   registros mais novos, o importador não troca os valores existentes por valores antigos.

## Dados conhecidos do resgate de 19/10/2026

- Exportação de compras: 62 linhas referentes a 36 números de pedido, de 501 a 537
  (número 511 não estava presente na exportação).
- Exportação de O.S.: 51 ordens, preservando dados existentes do banco de destino.
- Exportação de FMs: 14 formulários, conciliados pelo número de FM (ex.: `FM 03`),
  dando preferência apenas a uma data de realização **mais recente**.
- Anexos: 29 documentos únicos por pedido+conteúdo, para 29 pedidos distintos.
  Desses, os anexos 538 e 539 **não possuem linhas de compras no CSV disponibilizado**.
  Seus documentos ficam preservados e os números são reservados para não reutilização,
  mas seus dados de cadastro não são inventados.
- O relatório de usuários não traz senhas, permissões ou credenciais completas.
  O importador **não altera** o banco de usuários (global a todas as filiais).
- Exportação de compras não contém Observações, NF, Boleto, Assinaturas ou
  marcadores completos de email. Campos ausentes **não são inventados**;
  os anexos vão para o campo legado de documento/orçamento recuperado, respeitando
  vínculos já existentes, sem garantia de classificação original.

## Comportamento da importação

- Importa e concilia os registros com dados antigos, não executa restauração destrutiva.
- Não substitui O.S. já cadastradas; completa apenas campos em branco.
- Não substitui o status de compras existentes; importa status das compras novas.
- Atualiza FM existente **somente** se a data do arquivo for mais nova do que a data salva.
- Não elimina anexos já existentes; preserva as duplicidades entre pedidos e
  remove cópias repetidas do **mesmo pedido e mesmo conteúdo**.
- Salva em transação no PostgreSQL e mantém histórico de revisão dos CSVs atualizados.
- Ao importar o mesmo ZIP novamente, não duplica o conteúdo já identificado.
- Durante a importação, evite novos lançamentos simultâneos na mesma filial.

## Implantação, filiais, ferramental, segurança, limitações



1. Salve fora do GitHub os arquivos CSV e anexos **mais recentes**. O ZIP obtido do repositório pode estar desatualizado em relação à instância em execução no Streamlit Cloud.
2. Configure um banco PostgreSQL persistente **antes da primeira execução em produção**. Sem `DATABASE_URL`, é usado SQLite local apenas para desenvolvimento. SQLite não é persistente no Streamlit Community Cloud.
3. Se o repositório original está público, altere imediatamente as senhas de todos os usuários e credenciais SMTP eventualmente expostas, preferencialmente torne o repositório privado. O histórico do GitHub pode continuar expondo credenciais antigas.
4. Execute um backup ZIP de verificação após a primeira migração e salve-o fora do serviço de banco de dados.

### Conectar ao Supabase

1. Crie um projeto no [Supabase](https://supabase.com/) e defina uma senha longa no banco. Use a **URI de conexão PostgreSQL pelo pooler** (preferencialmente modo `Session`, IPv4) mostrada no painel do seu projeto. **Não é** a URL de API nem a chave `anon`.
2. No painel do app no Streamlit Community Cloud, abra **Settings → Secrets**.
3. Cole (substitua os campos abaixo pelas credenciais do projeto; senha codificada para URL quando possuir caracteres especiais):

```toml
DATABASE_URL = "postgresql://postgres.<PROJECT_REF>:<PASSWORD>@<POOLER_HOST>:5432/postgres?sslmode=require"
BOOTSTRAP_ADMIN_PASSWORD = "DEFINIR_UMA_SENHA_FORTE_COM_12_CARACTERES_OU_MAIS"
```

4. No repositório GitHub, **adicione/substitua** `Intranet.py`, `infra_persistencia.py`, `mod_ferramentaria.py`, `mod_filiais_backup.py`, `seguranca_senhas.py`, `requirements.txt` e os arquivos auxiliares. A pasta `.streamlit` com `config.toml` deve continuar existindo. Não faça upload de um `secrets.toml` com senhas reais.
5. Aguarde a implantação automática do Streamlit. As tabelas `intr_*` são criadas automaticamente no PostgreSQL e habilitam Row Level Security (RLS) quando o usuário do banco tem permissão.
6. Entre como usuário antigo, vá ao menu **🏢 Filiais e Backup** e prepare um ZIP da base Mandalla. Confira a listagem de O.S./FMs/compras e os anexos. Só depois registre as demais filiais e vincule usuários.

**Atenção:** a senha `BOOTSTRAP_ADMIN_PASSWORD` só é usada para uma instalação **nova sem arquivo de usuários antigo**. Se já existem usuários no CSV, seus logins são migrados. Recomenda-se alterar todos os acessos antigos após a entrada.

## Gestão de filiais

O código `MANDALLA` é criado automaticamente como primeira filial. Administradores têm acesso às unidades cadastradas. No gerenciamento de usuários (na tela de login), há o campo **Filiais autorizadas para este usuário**. Uma pessoa pode ser associada a uma ou mais bases; o seletor de filial aparece na barra lateral após autenticação. Ao trocar a filial, os quatro bancos operacionais passam a apontar ao escopo dessa unidade (contas de usuário permanecem globais).

## Ferramentas e materiais

A aba **Ferramentas** registra itens de patrimônio, empréstimos e devoluções por responsável. A aba **Materiais** controla consumo, entradas, saídas e estoque mínimo. Movimentações são gravadas em uma transação no banco e saída maior que o saldo é recusada. Devolução de empréstimo não pode exceder o que o responsável tem pendente. Correções manuais de saldo são exclusivas do perfil administrador pela interface. O histórico pode ser exportado em CSV.

## Backups e restauração

O administrador abre **🏢 Filiais e Backup → Backup ZIP**, escolhe se deseja incluir também usuários e assinaturas globais e baixa o ZIP. Os dados da ferramentaria, o histórico de movimentos, os quatro bancos e os anexos armazenados são incluídos conforme o escopo. A restauração valida o manifesto e só aceita um backup da **mesma filial**; atualiza os registros e restaura o inventário de maneira transacional.

Para proteção contra falha/perda do próprio banco do Supabase, guarde backups ZIP fora do Supabase (computador corporativo, cofre ou unidade de rede com política de retenção). O histórico de 20 revisões fica no mesmo banco e não protege contra perda total dele. O banco nesta versão **não agenda cópias externas sozinho**.

## Atualizar pelo GitHub

A atualização da aplicação se dá pela implantação automática do Streamlit após *push* na branch de produção. Como os registros e PDFs já migrados ficam no banco externo, não são substituídos pelos CSVs incluídos numa versão nova do código. Quando a migração for conferida, **remova os CSVs sensíveis do controle de versões** e mantenha somente backups privados; não apague os registros do banco. O `requirements.txt` instala `SQLAlchemy` e `psycopg2-binary` além das bibliotecas antigas.

## Limitações e observações de segurança

- Este pacote passou por testes locais com SQLite, importação conservadora, isolamento de filiais, inventário, conflitos, senhas e restauração. **Não foi validado contra um servidor Supabase real nem pela interface Streamlit no ambiente deste desenvolvimento**, porque não há credenciais nem acesso à instância do usuário.
- A importação automática inicial usa **os arquivos contidos neste ZIP/repositório**. Registros que só existiam em uma instância antiga efêmera do Streamlit e nunca foram exportados não podem ser recuperados por este pacote.
- Arquivos de PDF/assinatura são gravados na tabela PostgreSQL para reduzir mudanças na aplicação antiga. Para alto volume de documentos, recomenda-se futura migração para Supabase Storage privado ou objeto S3 com permissões de acesso.
- Mesmo com RLS, é importante proteger e limitar a conta PostgreSQL usada pelo backend, jamais expor `DATABASE_URL` no GitHub ou ao navegador.
- Futuras mudanças no esquema das tabelas PostgreSQL podem exigir migrações SQL explícitas; `create_all` não altera colunas existentes. Faça backup antes de cada atualização relevante.

### Executar localmente

```bash
py -m pip install -r requirements.txt
py -m streamlit run Intranet.py
```

A aplicação local sem `DATABASE_URL` cria `.runtime_data/intranet_local.sqlite3` (pasta ignorada pelo Git) para desenvolvimento, nunca para hospedagem de produção no Streamlit Cloud.
