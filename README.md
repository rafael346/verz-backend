# verz-backend

API da casa de leilões online **verz**. Leiloeiros montam lotes com obras de domínio público do Metropolitan Museum;
licitantes depositam saldo simulado, dão lances ao vivo e recebem um comprovante público do arremate. Qualquer pessoa
audita, sem login, se o histórico de lances foi adulterado.

Django 6.1 + Django REST Framework, PostgreSQL 17, uvicorn (ASGI, por causa do tempo real). O front fica no
repositório `verz-frontend`. As decisões do projeto, com o porquê de cada uma, estão em `../DECISOES.md`.

## Como rodar (Docker)

Os dois repositórios ficam lado a lado (o Compose constrói o front a partir de `../verz-frontend`):

```
verz/
├── verz-backend/    ← este repositório (docker-compose.yml)
└── verz-frontend/
```

1. Crie o `.env.docker` a partir do exemplo e **troque as duas chaves**:

   ```bash
   cp .env.docker.example .env.docker
   python -c "import secrets; print(secrets.token_urlsafe(50))"   # rode duas vezes: SECRET_KEY e AUDIT_HMAC_KEY
   ```

   Com `DEBUG=False`, o backend não sobe com as chaves de exemplo: elas são públicas, e com a `AUDIT_HMAC_KEY`
   conhecida quem altera o banco consegue refazer a cadeia de lances. Guarde a `AUDIT_HMAC_KEY` fora do banco e dos
   backups dele.

2. Suba tudo:

   ```bash
   docker compose up --build
   ```

3. Abra **http://localhost:8080**. O nginx é a única porta de entrada: `/api/` vai para o Django, o resto para o Next.

4. Carregue os dados de demonstração (contas e lotes em todas as fases; ver abaixo):

   ```bash
   docker compose exec backend python manage.py seed_demo
   ```

Serviços: `db` (Postgres, só em `127.0.0.1:5432`), `backend` (API e tempo real; aplica as migrações e cria a tabela
de cache ao subir), `worker` (encerra e liquida lotes vencidos, emite comprovantes e limpa sessões expiradas),
`frontend` e `nginx`.

Para desenvolver o front com recarga automática, rode `npm run dev` em `verz-frontend` e suba o resto sem o serviço
do front:

```bash
FRONTEND_UPSTREAM=host.docker.internal:3000 docker compose up db backend worker nginx
```

## Dados de demonstração

```bash
docker compose exec backend python manage.py seed_demo   # sem Docker: python manage.py seed_demo
```

O comando cria (ou reaproveita) três contas e um conjunto de lotes com obras reais do Met, e imprime os endereços
de cada lote. **Senha de todas as contas: `leilao-demo-2026`.**

| Conta | Papel | Para ver |
|---|---|---|
| `demo.leiloeiro@example.com` | leiloeiro | o painel: total recebido, criação de lotes, resultado de cada lote |
| `demo.ana@example.com` | licitante | a sala, a carteira com saldo comprometido e o comprovante do arremate |
| `demo.bia@example.com` | licitante | a outra ponta da disputa (abra numa janela anônima e dispute com a Ana) |

| Lote | O que mostra |
|---|---|
| Ao vivo, termina em 3 dias, 3 lances | disputa: "ganhando/superado", posição, histórico e saldo comprometido |
| Ao vivo, termina em 10 minutos | contagem em destaque; um lance nos últimos 2 minutos prorroga o fim |
| Agendado, abre em 2 horas | vitrine "agendados" e a sala antes de abrir |
| Encerrado e arrematado pela Ana | liquidação, Meus arremates, comprovante público e auditoria íntegra |
| Encerrado sem lances | lote que fecha sem vencedor |

- **Só para ambiente local:** a senha está neste README, então essas contas não podem existir num servidor público.
- **Rodar de novo** cria um conjunto novo de lotes, com horários a partir de agora. Os antigos ficam e vão encerrando
  sozinhos (lances são imutáveis, não há como apagá-los). As senhas voltam para a de demonstração, e as licitantes
  recebem R$ 10.000 sempre que tiverem menos de R$ 5.000 disponíveis.
- **O Met precisa estar no ar:** o comando busca as 4 obras (dados e imagem) uma de cada vez, antes de gravar
  qualquer coisa. Se o museu falhar, nada é criado e aparece "Acervo do Met indisponível agora; tente de novo em
  alguns minutos". As obras ficam no cache por 24 h, então rodar de novo logo depois quase não chama o museu.

## Como rodar sem Docker (só a API)

Precisa de Python 3.13 e de um Postgres (o do Compose serve: `docker compose up db`).

```bash
python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # troque as chaves, como acima
python manage.py migrate
python manage.py createcachetable
uvicorn config.asgi:application --port 8000
python manage.py run_settlement   # em outro terminal: a liquidação dos lotes
python manage.py seed_demo        # opcional: dados de demonstração
```

No `.env`, use `127.0.0.1` (não `localhost`) no `DATABASE_URL`: o Compose publica o Postgres só no IPv4 do loopback,
e `localhost` tenta o IPv6 antes, o que deixa cada conexão esperando 30 s.

## Testes

```bash
pytest           # 223 testes; usa o Postgres do DATABASE_URL (cria e apaga um banco de teste)
```

Os testes cobrem as regras de lance (incremento, saldo comprometido, anti-sniping, corrida entre lances), a carteira
imutável, a liquidação, o tempo real (SSE com retomada), a busca no Met com o museu fora do ar, e a auditoria
detectando cada tipo de adulteração feita direto no banco.

## Como funciona

| App | O que faz |
|---|---|
| `accounts` | Cadastro e login por sessão (cookie HttpOnly + CSRF), papéis leiloeiro e licitante. |
| `wallet` | Livro-razão só de inserção (trigger impede alterar); saldo = soma; comprometido = lances que você lidera. |
| `catalog` | Cliente do Met: timeout, novas tentativas, cache e só obras de domínio público com imagem. |
| `auctions` | Lotes, lances, anti-sniping, liquidação, comprovante, auditoria e tempo real (SSE + `LISTEN/NOTIFY`). |

Principais rotas (todas sob `/api/`):

| Rota | Quem |
|---|---|
| `auth/register/`, `auth/login/`, `auth/logout/`, `auth/me/`, `auth/csrf/` | todos |
| `catalog/search/?q=` | leiloeiro |
| `lots/` (vitrine e criação), `lots/mine/`, `lots/mine/summary/`, `lots/{id}/cancel/` | vitrine: todos; o resto: leiloeiro |
| `lots/{id}/`, `lots/{id}/bids/` (GET), `lots/{id}/events/` (SSE), `lots/{id}/image/` | todos |
| `lots/{id}/bids/` (POST), `lots/participating/`, `wallet/deposit/` | licitante |
| `wallet/` | logado |
| `lots/{id}/audit/`, `receipts/{token}/` | todos, sem login |

**Resiliência:** os lotes guardam uma cópia da obra (dados e imagem). Vitrine, salas, auditoria e comprovante
funcionam com a API do museu fora do ar; só a busca do leiloeiro fica indisponível, com mensagem clara.

**Cancelamento e estorno:** o leiloeiro cancela lote agendado ou ao vivo (com lances, informando o motivo) e lote
arrematado até 7 dias depois. Antes do arremate, o saldo reservado de quem estava ganhando é liberado na hora; depois,
o valor volta ao arrematante e sai do saldo do leiloeiro, em lançamentos que o banco impede de repetir. O motivo aparece
para os licitantes, no comprovante e na auditoria.

**Histórico à prova de adulteração:** cada lance é selado com HMAC-SHA256 encadeado ao anterior, e o estado do lote
tem um selo próprio. A chave fica fora do banco, triggers impedem alterar ou apagar lances e comprovantes, e a
auditoria pública recalcula tudo a cada visita. Lote com selo quebrado não recebe lances nem é liquidado.

## Segurança (OWASP)

- Senhas com Argon2, mínimo de 10 caracteres e bloqueio de senhas comuns; mesma resposta para e-mail inexistente e
  senha errada.
- Sessão HttpOnly, SameSite=Lax e Secure fora do `DEBUG`; CSRF exigido em toda escrita, inclusive de visitante.
- Limites de tentativa por IP e por e-mail no login, e por usuário em lance, cadastro, criação de lote e busca.
  O IP real vem do nginx (`NUM_PROXIES=1`), que sobrescreve o `X-Forwarded-For`; o backend não tem porta pública.
- Acesso negado por padrão; cada rota declara quem pode, e as consultas filtram pelo dono.
- Busca de imagens só em `images.metmuseum.org`, sem seguir redirecionamentos, com limite de tamanho e conferência do
  tipo real do arquivo (contra SSRF).
- Eventos de segurança no log (`verz.security`): login falho (e-mail só como hash), lance recusado por integridade e
  auditoria com problema.
- Pseudônimos por lote na vitrine, na auditoria e no comprovante: nenhum e-mail aparece em público.
- Antes de publicar: `python manage.py check --deploy`, TLS na frente do nginx e `SECURE_SSL_REDIRECT=True`.
