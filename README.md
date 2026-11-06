# FrenzelTech Ecommerce — gestão inteligente para e-commerce

Plataforma SaaS multiempresa (multi-tenant) para lojistas e pequenos negócios administrarem o e-commerce:
precificação, markup, margem de contribuição, ponto de equilíbrio, estoque, ponto de pedido, fornecedores,
compras, vendas, clientes, financeiro, marketing e relatórios — com um painel exclusivo para o SUPER ADMIN.

Interface em português do Brasil, valores em R$, datas DD/MM/AAAA e cálculos financeiros em `Decimal`.

## Stack

Python 3.12+ · Django 5.2 LTS · PostgreSQL 16 · Django Templates + Bootstrap 5 + HTMX + Chart.js ·
Gunicorn · WhiteNoise · django-storages (S3-compatible) · ReportLab (PDF) · Railway.

## Módulos

| Módulo | O que faz |
|---|---|
| **Contas** | Cadastro, login por e-mail com limite de tentativas, logout, recuperação/alteração de senha, perfil |
| **Empresas** | Cadastro obrigatório da empresa após o cadastro do usuário, dados e configurações, equipe com perfis (administrador, gerente, operador) e módulos por usuário, convites por link seguro, depósitos, tokens de API |
| **Dashboard** | 12 indicadores e 7 gráficos calculados com os dados reais; filtros hoje, 7/30 dias, mês atual/anterior, ano, período personalizado; meta do mês |
| **Produtos** | Cadastro completo (custos, preço, imagem, estoque mín./máx., reposição), busca, categorias, arquivar/excluir, importação/exportação CSV, histórico de preço e custo, estrutura para variações (`parent` + `variant_attributes`) |
| **Precificação** | Calculadora (preço mínimo, sugerido, markup %, multiplicador, margem sobre o custo, MC R$/%), simulador com gráfico, salvar simulação e aplicar o preço ao produto |
| **Ponto de equilíbrio** | Produto individual ou mix ponderado (inclusive a partir das vendas reais), gráfico custo-volume-lucro, cenários (+10/20/30% de vendas, aumento de despesas fixas, melhora de margem, meta de resultado) |
| **Estoque** | Entradas, saídas, ajustes por contagem, devoluções, perdas, transferências entre depósitos (opcional), histórico imutável, reservas de pedidos, disponível = saldo − reservado, valor do estoque, alertas |
| **Ponto de pedido** | Consumo médio pelo histórico (ou manual), prazo, estoque de segurança, sugestão de compra e geração de pedidos de compra em rascunho por fornecedor |
| **Fornecedores e compras** | Pedido de compra, envio, recebimento parcial/total, entrada controlada no estoque, custo médio ponderado com rateio de frete, conta a pagar automática, histórico por fornecedor |
| **Vendas** | Pedido manual ou CSV, canais (loja própria, Mercado Livre, Shopee, Amazon, Instagram, WhatsApp, outros), status com regras de estoque e financeiro, API para integrações futuras |
| **Clientes** | Cadastro mínimo, histórico, total comprado, última compra, LGPD (exportação JSON e anonimização) |
| **Financeiro** | Contas a pagar/receber, despesas fixas mensais (geração de lançamentos), categorias, vencimentos, pagamentos/recebimentos, fluxo de caixa e comparativo mensal, metas |
| **Marketing** | Campanhas com conversão, CPC, CAC, ROAS e ticket médio; comparativo e gráficos |
| **Relatórios** | 13 relatórios com filtros (período, produto, categoria, canal) e exportação CSV/PDF |
| **Plataforma (SUPER ADMIN)** | Dashboard de usuários/empresas/acessos/planos, gestão de usuários (bloqueio, convite, link de redefinição), empresas (plano, suspensão), planos SaaS e auditoria |

### Regras de negócio principais

- **Preço sugerido** = (custo unitário total + despesas variáveis fixas por venda) ÷ (1 − % despesas variáveis − % margem desejada).
  Denominador ≤ 0 é rejeitado com mensagem explicativa.
- **Markup (%)** = (preço − custo) ÷ custo × 100 · **Multiplicador** = preço ÷ custo. Markup ≠ margem.
- **Ponto de equilíbrio** = despesas fixas ÷ índice de MC (R$) e despesas fixas ÷ MC unitária (unidades).
- **Ponto de pedido** = consumo médio diário × prazo de reposição + estoque de segurança.
- **Pedido de venda**: pendente/aguardando pagamento → reserva; pago (ou posterior) → baixa e receita;
  cancelado → libera reserva ou estorna estoque (+ estorno financeiro se pago); devolvido → retorna ao estoque (opcional).
  **Pedido pendente nunca é receita.**
- **Faturamento → lucro bruto (−CMV) → margem de contribuição (−despesas variáveis) → resultado operacional (−despesas fixas).**
- O saldo de estoque só muda por movimentação registrada (`apps/inventory/services.py`), em transação atômica
  com `SELECT … FOR UPDATE` e constraints no banco que impedem saldo negativo.

## Arquitetura

Monólito modular Django (`apps/`), um app por domínio:

```
config/            settings (variáveis de ambiente), urls, wsgi
apps/core/         base multi-tenant (TenantModel/for_company), permissões, CRUD genérico, auditoria,
                   Decimal/BRL, períodos, CSV/PDF, rate limit, API REST, healthz, comandos (setup_plans, seed_demo)
apps/accounts/     usuário (login por e-mail), autenticação, convites/links de senha, ensure_superadmin
apps/companies/    empresas, planos, vínculos/perfis/módulos, depósitos, tokens de API, middleware da empresa ativa
apps/platform/     painel SUPER ADMIN
apps/products/     categorias, produtos, histórico de preços, importação/exportação
apps/pricing/      calculations.py e breakeven.py (funções puras), simulações
apps/inventory/    movimentações, saldos por depósito, reservas, ponto de pedido, alertas
apps/purchasing/   fornecedores, pedidos de compra, recebimento
apps/customers/    clientes (LGPD)
apps/sales/        pedidos de venda, fluxo de status, importação CSV
apps/finance/      categorias, lançamentos, despesas fixas, metas, indicadores
apps/marketing/    campanhas e métricas
apps/reports/      central de relatórios (registry.py)
apps/dashboard/    métricas e dashboard
templates/  static/  tests/
```

**Isolamento entre empresas (multi-tenant):** a empresa ativa vem sempre do vínculo do usuário
(`CurrentCompanyMiddleware`), nunca da URL ou do formulário. Toda consulta usa `Model.objects.for_company(...)`;
objetos de outra empresa retornam 404; formulários restringem chaves estrangeiras à empresa
(`CompanyScopedFormMixin`); a API identifica a empresa pelo token. Os testes em `tests/test_isolation.py` cobrem isso.

## Rodando localmente

Pré-requisitos: Python 3.12+ e PostgreSQL 14+ (recomendado). Para um teste rápido sem PostgreSQL, use
`DJANGO_DEBUG=1` sem `DATABASE_URL` (SQLite **somente** em desenvolvimento — produção exige PostgreSQL).

1. Instale as dependências:

```bash
python -m venv .venv
```
```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```
```bash
pip install -r requirements.txt
```

2. Configure as variáveis de ambiente:

```bash
cp .env.example .env
```

Edite o `.env`: `DJANGO_DEBUG=1`, `DATABASE_URL=postgres://usuario:senha@localhost:5432/gestao_saas`.

3. Crie o banco de dados:

```bash
createdb gestao_saas
```

4. Execute as migrations e crie os planos iniciais:

```bash
python manage.py migrate
```
```bash
python manage.py setup_plans
```

5. (Opcional) Dados de demonstração — empresa “[DEMONSTRAÇÃO] Loja Exemplo”, identificada e removível:

```bash
python manage.py seed_demo --email demo@exemplo.com --password "escolha-uma-senha-forte"
```

Remover depois: `python manage.py seed_demo --remove`.

6. Inicie o servidor:

```bash
python manage.py runserver
```

Acesse http://localhost:8000 → crie uma conta → cadastre a empresa. Sem `EMAIL_HOST`, os e-mails
(recuperação de senha, convites) aparecem no console.

## Testes

```bash
python manage.py test tests
```

São 125 testes: cadastro/login/limite de tentativas, recuperação e alteração de senha, permissões por perfil e
módulo, recursos por plano, isolamento entre empresas (telas, formulários, exportações e API), produtos e
importação CSV, cálculo de preço/markup/margem (incluindo denominador inválido, valores negativos e ausentes),
ponto de equilíbrio (produto e mix, MC zero/negativa), ponto de pedido, entradas/saídas/reservas de estoque,
vendas (reserva, pagamento, cancelamento, devolução, transições inválidas), compras (recebimento parcial e custo
médio), indicadores financeiros, todos os relatórios em CSV/PDF, painel SUPER ADMIN e health check.

Localmente os testes rodam em SQLite se `DATABASE_URL` não estiver definida; o workflow
`.github/workflows/ci.yml` executa a suíte contra **PostgreSQL 16** e roda `check --deploy`.

## Publicação no Railway

1. Suba o repositório para o GitHub e crie um projeto no Railway a partir dele. O `railway.json` usa o
   `Dockerfile` (build com `collectstatic`) e define o health check em `/healthz`.
2. Adicione um serviço **PostgreSQL** ao projeto. No serviço web, crie a variável
   `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Cadastre as variáveis (Variables) do serviço web — no mínimo:
   - `DJANGO_SECRET_KEY` (gere com `python -c "import secrets; print(secrets.token_urlsafe(64))"`)
   - `DJANGO_DEBUG=0`
   - `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` (o domínio `*.up.railway.app` é incluído automaticamente)
   - E-mail SMTP (`EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`) para recuperação de senha e convites
4. **Imagens dos produtos (armazenamento persistente):** o disco do container é efêmero.
   - **Cloudinary (recomendado):** no painel do Cloudinary, em *API Keys*, copie a **API environment variable**
     e cadastre `CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME` (só o valor, sem repetir o nome).
     Ou cadastre as três variáveis `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` e `CLOUDINARY_API_SECRET`.
     Opcional: `CLOUDINARY_FOLDER` (padrão `frenzeltech-ecommerce`). As imagens são entregues por HTTPS com
     formato e qualidade otimizados automaticamente (`f_auto`, `q_auto`).
   - **Alternativa S3** (usada só sem Cloudinary): Railway Bucket, R2 ou AWS S3 com as variáveis `S3_*`
     (URLs assinadas).
   - Última opção: Railway Volume montado em `MEDIA_ROOT` + `SERVE_MEDIA_FROM_DISK=1` (apenas 1 réplica).
5. Deploy: ao iniciar, `scripts/start.sh` executa `migrate`, `setup_plans` e `ensure_superadmin` e só então
   sobe o Gunicorn. O `/healthz` responde 503 se o banco estiver inacessível ou houver migração pendente.
   Remova qualquer *Custom Start Command* configurado no painel para usar o do `railway.json`.
6. Domínio próprio: Settings → Networking → Custom Domain; inclua o domínio em `DJANGO_ALLOWED_HOSTS`,
   `DJANGO_CSRF_TRUSTED_ORIGINS` (com `https://`) e `SITE_URL`. O HTTPS é automático.
7. Mais de uma réplica: configure `REDIS_URL` (cache compartilhado dos limites de tentativa).

### Criando o primeiro SUPER ADMIN

Escolha uma opção:

- **Por variáveis (sem terminal):** cadastre `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` e
  (opcional) `DJANGO_SUPERUSER_FULL_NAME` no serviço web e faça o deploy. O comando `ensure_superadmin` cria o
  usuário se ele não existir e nunca altera um usuário existente. **Depois do primeiro acesso, apague
  `DJANGO_SUPERUSER_PASSWORD` das variáveis.**
- **Pelo terminal:** localmente `python manage.py createsuperuser`; no Railway, `railway ssh` e depois
  `python manage.py createsuperuser`.

O SUPER ADMIN acessa `/plataforma/`. Ele também pode cadastrar uma empresa própria (`/empresa/cadastro/`).

## Segurança

- Senhas com hash PBKDF2 (padrão Django); nunca exibidas ou enviadas por e-mail — convites e redefinições usam
  links de uso único.
- CSRF em todos os formulários, ORM parametrizado (sem SQL manual), validação de formulários no servidor.
- Permissões verificadas no backend em cada view (módulo, perfil, recurso do plano, empresa suspensa).
- Rate limiting em login, cadastro, recuperação de senha e API.
- Produção: HTTPS obrigatório, HSTS, cookies `Secure`/`HttpOnly`/`SameSite`, CSP, `X-Frame-Options: DENY`.
- Segredos somente em variáveis de ambiente; logs em JSON com mascaramento de dados sensíveis.
- Auditoria (`AuditLog`) de operações críticas: estoque, vendas, compras, financeiro, permissões, planos,
  bloqueios, consultas do SUPER ADMIN a empresas, exportações e ações LGPD.
- Exportações CSV neutralizam fórmulas (`=`, `+`, `-`, `@`); uploads de imagem validados pelo conteúdo.

## Assinaturas (Mercado Pago)

As assinaturas dos planos são cobradas pelo **Mercado Pago — Assinaturas** (cobrança mensal recorrente no cartão;
o cliente autoriza no checkout do Mercado Pago e os dados do cartão nunca passam pelo sistema).

**Fluxo**
1. No cadastro da empresa aparecem os planos ativos e a opção **Teste grátis** (14 dias, sem cartão — `FREE_TRIAL_DAYS`).
2. Plano pago → o sistema cria a assinatura (`POST /preapproval`) e redireciona ao checkout do Mercado Pago.
3. O acesso é liberado quando o Mercado Pago confirma `authorized` — pelo **webhook** ou pela consulta feita no retorno
   do checkout. O retorno do navegador sozinho nunca libera o acesso.
4. Fim do teste grátis sem assinatura → a gestão fica bloqueada (dados preservados) até assinar em
   Configurações → Assinatura. Quem assina durante o teste só é cobrado depois que o teste termina.
5. Cancelamento: nenhuma nova cobrança; o acesso continua até a data da próxima cobrança já paga.
6. Cupons: 50% reduz o valor mensal (pelo prazo do cupom); 100% permanente ativa o plano sem cobrança; 100% com prazo
   adia a 1ª cobrança para depois do prazo.

**Configuração**
1. Em https://www.mercadopago.com.br/developers/panel/app crie uma aplicação e copie as credenciais para
   `MERCADOPAGO_ACCESS_TOKEN` e `MERCADOPAGO_PUBLIC_KEY` (credenciais de teste começam com `TEST-`).
2. Em *Webhooks*, cadastre a URL `https://SEU-DOMINIO/assinatura/webhook/mercadopago/` com os eventos
   **Planos e assinaturas** (assinaturas e pagamentos autorizados) e copie a assinatura secreta para
   `MERCADOPAGO_WEBHOOK_SECRET`.
3. `SITE_URL` precisa ser o endereço HTTPS público (é a página de retorno do checkout).
4. Testes: use credenciais de teste e, no checkout, um **usuário comprador de teste** do Mercado Pago (o e-mail do
   pagador não pode ser o da conta vendedora).
5. Recomendado: Railway Cron diário com `python manage.py sync_subscriptions` (reconcilia situações e encerra descontos
   de cupom vencidos).

## API (integrações futuras)

Crie um token em Configurações → Integrações. Envie `Authorization: Bearer <token>`.

- `GET /api/v1/produtos/?q=` — produtos com preço, estoque, reservado e disponível
- `GET /api/v1/produtos/<sku>/`
- `POST /api/v1/pedidos/` — registra um pedido (reserva/baixa estoque conforme o status; referência duplicada → 409)

Não há integração automática com marketplaces: a API é o ponto de entrada para conectores futuros.

## Limitações conhecidas

- Assinaturas aceitam cartão (recorrência do Mercado Pago); Pix/boleto recorrentes não são suportados pelo recurso.
- Variações de produto (tamanho/cor) têm estrutura no modelo, mas ainda sem tela dedicada.
