# Registro de decisões

Este documento explica **por que** o verz é como é. Cada decisão traz o contexto que a motivou, o que foi
decidido e, quando houve, as alternativas descartadas e as consequências que ficaram.

O código cita as decisões pelo número (por exemplo, `# D18`), então os números não mudam. Quando uma decisão é revista,
o status dela aponta para a que a revê. As lacunas na numeração são decisões que saíram do documento.

**Status:** *Em vigor* (vale hoje) · *Revista em Dxx* (parte dela mudou depois) · *Substituída por Dxx* (não vale mais).

## Índice

| Tema | Decisões | Resumo 
|---|---|
| Fundamentos | [D01](#d01) API de itens (substituída) · [D02](#d02) PostgreSQL · [D03](#d03) Tempo real com SSE · [D04](#d04) Sessão por cookie · [D05](#d05) Worker de liquidação · [D06](#d06) Integridade com HMAC · [D07](#d07) Arquitetura · [D08](#d08) Modelo e regras de lance · [D09](#d09) Pseudônimo por lote |
| Backend | [D10](#d10) Contas · [D13](#d13) Carteira · [D14](#d14) Troca para o Met · [D15](#d15) Busca no acervo · [D16](#d16) Lotes e vitrine · [D17](#d17) Lances · [D18](#d18) Auditoria · [D19](#d19) Liquidação e comprovante · [D20](#d20) SSE na prática |
| Frontend | [D21](#d21) Base e nginx · [D22](#d22) Identidade visual · [D23](#d23) Vitrine · [D24](#d24) Painel do leiloeiro · [D25](#d25) Sala, carteira e arremates · [D26](#d26) Auditoria e comprovante públicos |
| Qualidade e evolução | [D27](#d27) Cache, OWASP e documentação · [D28](#d28) Dados de demonstração · [D29](#d29) Cancelamento com estorno · [D30](#d30) Testes ponta a ponta · [D31](#d31) Limites revistos · [D32](#d32) Responsividade · [D38](#d38) Tratamento de erros |
| Produção | [D36](#d36) Segredo do proxy |


# Resumo
 Esse resumo serve para contar um pouco como foi a minha trajetoria oa longo do desenvolvimento desse teste técnico.
 Como ao longo do processo tomei muitas decisões e descartei muitas fui anotando tudo e  ao final pedi para que a IA pudesse padronizar e exibir de forma mais formatada a fim de facilitar o entendimento e avaliação.
 Normalmente na escolha de tecnologias de um projeto eu faço uma avaliação antes de selecionar cada tecnologia, foi o mesmo caso desse projeto. O mesmo serve para o uso de IA, costumo fazer uma configuração especifica para cada projeto
 afim de evitar alucinações,melhorar a qualidade daquilo que e produzido, otimizar tempo e entrega sem perca de qualidade, e tambem visando a economia de tokens. Apesar de usar bastante a IA tudo aquilo que é produzido  eu reviso  e testo minuciosamente, tambem buscos possiveis falhas e  vunerabilidades em cada funncionalidade desenvolvida, sempre buscando minimizar o erro, boa parte disso nao consegui documentar pois ficaria um arquivo muito grande  e fugiria um pouco do que é pedido.
---

# Fundamentos

<a id="d01"></a>
## D01 · API pública de itens: Art Institute of Chicago

*2026-10-23 · Substituída por [D14](#d14)*

**Contexto.** O leiloeiro monta cada lote a partir de uma obra buscada numa API pública.

**Decisão.** Usar a API do Art Institute of Chicago. Ela não exige chave, tem busca por texto, imagens via IIIF e
atributos ricos (artista, data, técnica, dimensões). Obras de arte combinam com leilão.

**Alternativas descartadas.** Metropolitan Museum (busca em duas etapas, mais chamadas e mais risco de limite),
Pokémon TCG API (limite de requisições menor) e Open Library (imagens e atributos pobres).

**O que ficou.** A API externa só é consultada no painel do leiloeiro. Ao criar o lote, guardamos uma cópia dos dados
da obra (*snapshot*) no nosso banco, para o resto da plataforma não depender dela. Essa regra sobreviveu à troca de
API.

<a id="d02"></a>
## D02 · Banco de dados: PostgreSQL

*2026-10-23 · Em vigor*

**Contexto.** Lances simultâneos e saldo comprometido exigem consistência forte. Pela análise PACELC, vale pagar em
latência (EC) e em disponibilidade durante uma partição (PC) para nunca aceitar dois lances que gastem o mesmo
dinheiro.

**Decisão.** PostgreSQL, com um primário único e transações ACID. O projeto usa `SELECT ... FOR UPDATE` no lote e na
carteira, constraints e triggers que bloqueiam `UPDATE` e `DELETE` no histórico de lances.

**Alternativas descartadas.** MySQL/InnoDB (também PC/EC, mas com menos recursos de integridade e suporte um pouco
inferior no Django) e MongoDB (PA/EC por padrão, com transações entre documentos menos naturais para saldo e lance).

<a id="d03"></a>
## D03 · Tempo real: Server-Sent Events (SSE)

*2026-10-23 · Em vigor (detalhada em [D20](#d20))*

**Contexto.** A sala do lote precisa se atualizar sem recarregar a página.

**Decisão.** SSE do servidor para o navegador. O lance continua sendo um `POST` comum, e os eventos nascem de
`LISTEN/NOTIFY` do Postgres.

**Por quê.** O fluxo é assimétrico: muitos assistem e poucos dão lance. E o lance precisa de uma resposta clara, com
o motivo da recusa, o que é natural num `POST`. O navegador reconecta sozinho, e o `Last-Event-ID` permite retomar
sem perder eventos.

**Alternativas descartadas.** WebSocket com Django Channels e Redis (mais infraestrutura e mais superfície de ataque:
sequestro de WebSocket entre sites, autenticação na conexão, validação de mensagens) e polling (menos ao vivo e mais
carga).

**Consequência.** Não há Redis. Se a escala exigir, o `LISTEN/NOTIFY` pode ser trocado por Redis sem mudar o
contrato com o navegador.

<a id="d04"></a>
## D04 · Autenticação: sessão em cookie HttpOnly

*2026-10-23 · Em vigor*

**Contexto.** O Next.js precisa autenticar o usuário no Django seguindo as recomendações da OWASP.

**Decisão.** Cookie de sessão `HttpOnly`, `Secure` e `SameSite`, com proteção CSRF. Navegador e API ficam no mesmo
domínio, por meio de um proxy, então não há CORS.

**Por quê.** O JavaScript não alcança o cookie, o que protege a sessão contra roubo por XSS. Sair revoga a sessão na
hora. E o `EventSource` do SSE manda o cookie sozinho.

**Alternativa descartada.** JWT com access e refresh token: exige cuidado com armazenamento, rotação e revogação, e o
`EventSource` não envia o cabeçalho `Authorization`.

<a id="d05"></a>
## D05 · Encerramento e liquidação: worker próprio em loop

*2026-10-23 · Em vigor (detalhada em [D19](#d19))*

**Contexto.** No fim do prazo, o lote deve ser encerrado, o vencedor debitado, o leiloeiro creditado e o comprovante
emitido, mesmo que ninguém esteja olhando a sala.

**Decisão.** Um comando Django, num processo separado, verifica a cada segundo os lotes vencidos e liquida cada um numa
transação idempotente, com `SELECT ... FOR UPDATE SKIP LOCKED`. Os lances recusam lotes vencidos mesmo antes da
liquidação.

**Alternativas descartadas.** Celery com Redis (duas peças de infraestrutura para um único job) e liquidação na
primeira visita ao lote (lotes que ninguém abre ficariam sem liquidar, e o crédito do leiloeiro atrasaria).

<a id="d06"></a>
## D06 · Integridade do histórico: HMAC encadeado e âncora pública

*2026-10-23 · Em vigor (detalhada em [D18](#d18))*

**Contexto.** Qualquer alteração, remoção ou inserção posterior no histórico de lances, mesmo feita direto no banco,
precisa ser detectada pela auditoria.

**Decisão.** Cada lance guarda `HMAC-SHA256(segredo fora do banco, dados do lance + hash do lance anterior)`. O hash
final de cada lote vai no comprovante e nas respostas de lance, como âncora pública. Triggers no Postgres bloqueiam
`UPDATE` e `DELETE` como defesa extra.

**Por quê.** Sem o segredo, quem altera o banco não consegue recalcular a cadeia. E a âncora já entregue revela a
remoção de lances do fim da cadeia.

**Alternativas descartadas.** SHA-256 encadeado sem segredo (quem acessa o banco recalcula a cadeia inteira sem ser
notado) e assinatura Ed25519 (mais forte, mas exige gestão e rotação de chaves).

**Limite conhecido.** Quem tem o segredo **e** o banco consegue forjar a cadeia. Nesse caso, só as âncoras entregues
antes (comprovantes e respostas aos licitantes) denunciam. A verificação roda no servidor, porque o auditor público
não tem o segredo.

<a id="d07"></a>
## D07 · Arquitetura geral e stack

*2026-10-23 · Em vigor (a porta de entrada mudou em [D21](#d21))*

**Contexto.** O teste pede frontend e backend separados, com tempo real, segurança e resiliência a falhas da API
externa.

**Decisão.**

- **Frontend (`verz-frontend`):** Next.js (App Router) com React, TypeScript, Tailwind e `next-themes` (tema escuro).
  Páginas públicas renderizadas no servidor.
- **Backend (`verz-backend`):** Django com Django REST Framework, nas apps `accounts`, `wallet`, `catalog`, `auctions`
  (lotes, lances, auditoria, liquidação e cancelamento) e `core`.
- **Infraestrutura local:** Docker Compose com `db`, `backend`, `worker`, `frontend` e `nginx`.
- **Resiliência:** vitrine, sala, auditoria e comprovante leem só o nosso banco (snapshot da obra, D01). A busca do
  leiloeiro usa timeout, nova tentativa e cache, e mostra erro com "Tentar de novo" se o museu cair.
- **Segurança (OWASP):** Argon2 para senhas, limite de tentativas em lances e login, validação de entrada em todas as
  rotas, controle de acesso por papel no servidor, cabeçalhos de segurança com CSP, dinheiro em `Decimal` e segredos
  só em variáveis de ambiente.

<a id="d08"></a>
## D08 · Modelo de dados e regras de lance

*2026-10-23 · Em vigor (valor mínimo corrigido em [D17](#d17); cancelamento revisto em [D29](#d29))*

**Contexto.** Antes do código, era preciso fixar as entidades e as regras que garantem que saldo e lances nunca
divergem.

**Decisão.**

- **Entidades:** `User` (papel `seller` ou `bidder`), `Wallet`, `LedgerEntry` (livro-razão imutável: o saldo é a soma
  do livro), `Lot` (com o snapshot da obra), `Bid` (com `prev_hash` e `hash`, D06) e `Receipt` (com token público
  impossível de adivinhar).
- **Fase do lote:** agendado, ao vivo e encerrado são derivados do relógio do servidor. No banco fica só `open`,
  `cancelled` ou `settled`.
- **Saldo comprometido:** não é gravado. É a soma dos lances atuais dos lotes abertos em que o usuário lidera.
  Disponível = total − comprometido. Assim não existe um valor que possa divergir.
- **Lance numa transação só:** trava lote e carteira; valida o papel, se o lote já começou ou já acabou, se o líder
  está cobrindo a si mesmo, o valor mínimo e o saldo disponível; grava o lance encadeado; aplica o anti-sniping; e
  emite o evento. Cada recusa devolve um código e o motivo.
- **Anti-sniping:** todo lance aceito nos últimos 2 minutos soma 2 minutos ao fim (`ends_at += 2 min`), sem limite de
  prorrogações. Não é "agora + 2 minutos".

<a id="d09"></a>
## D09 · Privacidade: pseudônimo por lote

*2026-10-23 · Em vigor*

**Contexto.** Histórico, auditoria e comprovante são públicos, mas não podem expor quem deu cada lance.

**Decisão.** Cada licitante recebe um pseudônimo por lote (por exemplo, "Licitante #A3F9"), gerado por HMAC do usuário
e do lote.

**Por quê.** Ninguém consegue cruzar o mesmo licitante entre lotes diferentes.

**Contrapartida.** A mesma pessoa não é reconhecível de um lote para outro. Se isso fizer falta, o pseudônimo pode
passar a ser fixo por usuário.

---

# Backend

<a id="d10"></a>
## D10 · Contas e autenticação

*2026-10-23 · Em vigor (401 para quem está sem sessão desde [D38](#d38))*

**Contexto.** Cadastro e login para dois papéis, leiloeiro e licitante, resistentes a força bruta, CSRF e IP forjado.

**Decisão.**

- **Usuário próprio** (`accounts.User`), com login por e-mail e `role`. Foi criado antes da primeira migração, para
  não precisar migrar depois. O e-mail é único sem diferenciar maiúsculas (constraint em `Lower(email)`).
- **Papéis:** o cadastro é aberto, com escolha entre `seller` e `bidder`. Qualquer outro valor é recusado, e `is_staff`
  e `is_superuser` nunca vêm do cadastro. As permissões `IsSeller` e `IsBidder` ficam em `accounts/permissions.py`.
- **Rotas:** `GET /api/auth/csrf/`, `POST /api/auth/register/` (já entra logado), `POST /api/auth/login/`,
  `POST /api/auth/logout/` e `GET /api/auth/me/`.
- **CSRF em todo POST, inclusive anônimo,** o que impede um site de terceiros de fazer login em nome do visitante
  (`CsrfSessionAuthentication`). Como `login()` e `logout()` trocam o token, o front lê o cookie `csrftoken` antes de
  **cada** POST, e um teste prova isso.
- **Contra força bruta:** a mesma mensagem para e-mail inexistente e senha errada; 5 logins por minuto por IP;
  10 logins por hora por e-mail, de qualquer IP; 10 cadastros por hora. A chave do limite por e-mail usa o hash
  SHA-256 do e-mail, não o e-mail. A contrapartida: errar o e-mail de outra pessoa 10 vezes bloqueia o login dela por
  até 1 hora.
- **Senha** validada pelos validadores do Django: no mínimo 10 caracteres, não comum, não só numérica.
- **IP do cliente:** vem de `NUM_PROXIES`, com padrão 0 (o IP da conexão). O número só sobe quando há um proxy
  confiável à frente, que acrescenta o IP real ao `X-Forwarded-For`, e o backend não tem porta pública. Com o valor
  errado, ou um cabeçalho forjado burla os limites, ou todos os usuários caem no mesmo contador.
- **Detalhes:** cadastros simultâneos com o mesmo e-mail devolvem 400, não 500; a checagem de e-mail duplicado usa
  busca exata, pelo índice; o `.dockerignore` impede que `.env`, `.git` e `.venv` entrem na imagem, e os segredos
  chegam só em tempo de execução.
- **Limpeza de sessões:** o Django não apaga sessões expiradas sozinho. O worker de liquidação faz isso uma vez por dia
  (D19).

**Limite conhecido.** Não há verificação de e-mail, que não foi pedida, e o cadastro revela se um e-mail já existe
(aceito na [D31](#d31)).

<a id="d13"></a>
## D13 · Carteira

*2026-10-23 · Em vigor*

**Contexto.** O licitante precisa de saldo para dar lances, e o saldo nunca pode ser perdido nem duplicado.

**Decisão.**

- **Livro-razão só de inserção:** `LedgerEntry` com valor com sinal (crédito positivo, débito negativo, nunca zero).
  Um trigger do Postgres bloqueia `UPDATE` e `DELETE`, venham do ORM ou de SQL direto.
- **O saldo é a soma do livro:** não há coluna de saldo que possa divergir. Como o depósito só insere uma linha,
  depósitos simultâneos não perdem valor e não precisam de trava.
- **Toda conta tem carteira,** criada junto com o usuário. O leiloeiro também, porque recebe na liquidação.
- **Depósito simulado:** só para licitante; de R$ 1,00 a R$ 100.000,00, com 2 casas decimais; `NaN`, `Infinity` e
  texto são recusados. Não há chave de idempotência: dois cliques fazem dois depósitos, o que é aceitável numa
  simulação.
- **Dinheiro no JSON sempre como texto com 2 casas** (`"15.75"`), porque o DRF converteria `Decimal` em `float`.
- **Rotas:** `GET /api/wallet/` (o próprio saldo: total, comprometido e disponível) e `POST /api/wallet/deposit/`
  (só licitante).

<a id="d14"></a>
## D14 · Troca da API de itens: Metropolitan Museum

*2026-10-23 · Em vigor (substitui a [D01](#d01))*

**Contexto.** Quando os lotes começaram a ser construídos, as imagens do Art Institute passaram a responder 403 com um
desafio anti-robô do Cloudflare, tanto por script quanto no navegador. O backend não conseguia copiar as imagens
(burlar a proteção não é uma opção), e a miniatura da própria API era um GIF de 6×5 pixels.

**Decisão.** Usar a Metropolitan Museum Collection API, que também não pede chave. As imagens
(`images.metmuseum.org`) baixam normalmente, então o backend guarda uma cópia local de cada uma ao criar o lote.
Vitrine, sala e comprovante continuam com imagem mesmo com o museu fora do ar.

**Fatos verificados na API (2026-10-23).**

- A busca antiga `/v1/search` foi aposentada em 2026-10-01. A nova, `/v1.1/search`, devolve só IDs, paginados por
  `offset` e `limit`.
- O filtro `isPublicDomain` é ignorado na busca, e obras com `hasImages=true` podem vir sem imagem.
- Sem resultado, `objectIDs` vem `null`.

**Consequências.** A busca é feita em duas etapas (IDs e depois os detalhes, em paralelo e com cache). O filtro de
domínio público e de imagem é nosso, obra a obra, por isso uma página pode trazer menos obras que o limite.

<a id="d15"></a>
## D15 · Busca no acervo do Met

*2026-10-23 · Em vigor (paralelismo reduzido em [D24](#d24); servidor trocado em [D20](#d20); cache compartilhado em [D27](#d27))*

**Contexto.** A busca do leiloeiro depende de um serviço externo que às vezes fica lento, pode cair e bloqueia rajadas
de chamadas.

**Decisão.**

- **Rota:** `GET /api/catalog/search/?q=&offset=`, só para leiloeiro. `q` com 2 a 100 caracteres e `offset` de 0 a
  9988, porque a busca do Met não passa de 10.000 resultados. Limite de 30 buscas por minuto, porque cada busca vira
  até 13 chamadas ao museu.
- **Cliente HTTP da biblioteca padrão** (`urllib`), sem dependência nova, com timeout de 3 s por chamada. A busca tem
  uma nova tentativa após 0,5 s em caso de falha de rede, timeout, 429 ou 5xx. Erros 4xx não se repetem, e 404 numa
  obra vira "não existe". Os detalhes das obras (até 12 por página) são buscados em paralelo, sem novas tentativas.
- **Filtro nosso:** só obras de domínio público e com imagem. Na busca real por "sunflowers", 4 de 12 passaram.
- **Cache:** cada obra por 24 h (inclusive "não serve para lote") e cada página de busca por 10 min. Uma página em que
  alguma obra falhou não é guardada.
- **Falhas do museu** (fora do ar, lento, conexão caída, resposta cortada no meio, rota movida ou formato novo) viram
  503 com uma mensagem clara. Uma obra sem ID ou com ID inválido é ignorada, sem derrubar a página.
- **Nenhuma transação aberta** enquanto espera o museu (`non_atomic_requests` na rota).

<a id="d16"></a>
## D16 · Lotes e vitrine

*2026-10-23 · Em vigor (cancelamento revisto em [D29](#d29))*

**Contexto.** O lote junta uma obra do Met e as condições do pregão. Ele precisa funcionar mesmo que o museu caia, e
nada do que o navegador envia sobre a obra pode ser aceito sem conferência.

**Decisão.**

- **Snapshot feito no servidor:** ao criar o lote, o backend busca a obra no Met (nunca usa dados vindos do navegador),
  guarda título, artista, data, técnica, dimensões, cultura e departamento, e salva a imagem no banco. A imagem é
  servida por `GET /api/lots/{id}/image/` com cache de 1 dia. Um teste confirma que vitrine, detalhe e imagem
  funcionam com o museu fora do ar.
- **Download seguro da imagem (OWASP, SSRF):** só de `https://images.metmuseum.org`; redirecionamentos são recusados;
  no máximo 5 MB; só JPEG ou PNG, conferidos pelos primeiros bytes do arquivo, não pelo cabeçalho.
- **Link da obra validado:** só é guardado se for `https://www.metmuseum.org/...`, o que impede XSS por um link
  `javascript:`.
- **Rotas:**
  - `GET /api/lots/?phase=live|scheduled|ended&q=&page=`: pública, com 20 por página. Os lotes ao vivo vêm pelo que
    termina antes, os agendados pelo que começa antes e os encerrados pelo mais recente. Cancelados nunca aparecem.
    A busca `q` procura em título, artista, cultura e departamento.
  - `GET /api/lots/{id}/`: pública. Lote cancelado dá 404, e o leiloeiro não é exposto.
  - `POST /api/lots/`: só leiloeiro, 60 por hora.
  - `GET /api/lots/mine/`: todos os lotes do leiloeiro, inclusive os cancelados.
- **Regras de criação:** lance inicial de R$ 1,00 a R$ 1.000.000,00; incremento de R$ 1,00 a R$ 100.000,00; início
  entre agora (com 1 minuto de tolerância) e 90 dias à frente; duração de 1 minuto a 30 dias. Data e hora exigem fuso:
  sem ele, o horário seria lido como UTC e o lote mudaria de hora. O banco também recusa um lote que termina antes de
  começar.
- **Desempenho:** as listagens nunca carregam os bytes da imagem (há teste). A busca usa `ILIKE` sem índice. Com
  milhares de lotes, o caminho é um índice trigram, anotado como `shortcut:` no código.

<a id="d17"></a>
## D17 · Lances

*2026-10-23 · Em vigor (cancelamento revisto em [D29](#d29))*

**Contexto.** É o coração do leilão: lances simultâneos, saldo disputado entre lotes e mensagens de recusa que a pessoa
entenda.

**Decisão.**

- **Valor mínimo:** o lance deve ser **maior ou igual** ao mínimo, que é o lance inicial no primeiro lance e o atual
  mais o incremento nos seguintes. Isso corrige a D08, que pedia o valor exato e não combinava com a mensagem "abaixo
  do mínimo".
- **Regras num lugar só:** `auctions/bidding.py: place_bid()`, numa transação que trava primeiro o lote e depois a
  carteira, sempre nessa ordem, para evitar deadlock. As checagens seguem esta ordem: não iniciado, encerrado, já está
  ganhando, abaixo do mínimo e saldo insuficiente.
- **Recusas:** `400 {"code", "detail"}` com os códigos `not_started`, `ended`, `already_winning`, `below_minimum` (a
  mensagem traz o mínimo atual) e `insufficient_balance` (a mensagem traz o disponível). Leiloeiro recebe 403, e lote
  cancelado, 404.
- **Saldo comprometido calculado, nunca gravado:** ser superado libera o valor na hora.
- **Anti-sniping:** um lance aceito com 2 minutos ou menos para o fim soma 2 minutos ao fim. Um lance que deixa mais
  de 2 minutos não prorroga.
- **Concorrência testada de verdade:** dois lances iguais ao mesmo tempo (só um é aceito) e o mesmo licitante gastando
  o mesmo saldo em dois lotes ao mesmo tempo (só um é aceito). Sem as travas, esses testes falham.
- **Pseudônimo (D09)** gravado no próprio lance, para não mudar se a `SECRET_KEY` mudar.
- **Rotas:**
  - `POST /api/lots/{id}/bids/`: só licitante, 30 por minuto.
  - `GET /api/lots/{id}/bids/`: histórico público, do mais novo ao mais antigo, só com pseudônimos (e `is_you` para o
    próprio usuário).
  - `GET /api/lots/{id}/`: inclui `current_bid`, `bid_count`, `min_next_bid` e `me` (ganhando, superado ou sem lance,
    com posição e número de licitantes).
  - `GET /api/lots/participating/`: lotes em que o licitante deu lance, com `winning`, `outbid`, `won` ou `lost`.

**Observado na prática.** O dono do banco consegue desligar o trigger de imutabilidade (`ALTER TABLE ... DISABLE
TRIGGER`). Isso confirma o limite da D06: a prova de adulteração precisa do HMAC, não só do trigger.

<a id="d18"></a>
## D18 · Integridade e auditoria

*2026-10-23 · Em vigor*

**Contexto.** Era preciso implementar a D06 de modo que remover, inserir ou alterar qualquer lance, ou o próprio lote,
apareça na auditoria pública.

**Decisão.**

- **Cadeia de lances:** cada lance guarda `prev_hash` e
  `hash = HMAC-SHA256(AUDIT_HMAC_KEY, lote | sequência | licitante | pseudônimo | valor | horário | prev_hash)`.
  O primeiro lance se encadeia num `genesis` do lote.
- **Selo do lote:** `HMAC(lote | status | lance inicial | incremento | início | fim | nº de lances | lance atual |
  líder | último hash)`. A cadeia sozinha não pega quem apaga lances do **fim** e "volta" o lote ao estado anterior; o
  selo pega. Também pega troca de líder, de prazo ou de valores.
- **Chave fora do banco:** `AUDIT_HMAC_KEY`, variável de ambiente com no mínimo 32 caracteres. O backend não sobe sem
  ela, e um teste troca a chave e vê a auditoria falhar.
- **Nada se constrói sobre uma adulteração:** lance, cancelamento e liquidação conferem o selo antes. Se ele não bate,
  o lance é recusado (`integrity_error`) e o cancelamento dá 409. Sem isso, o próximo lance selaria a adulteração.
- **Âncora externa:** o hash de cada lance vai na resposta do lance e no histórico público, e o comprovante leva o
  último hash.
- **Auditoria pública:** `GET /api/lots/{id}/audit/`, sem login, inclusive para lote cancelado. Devolve `intact`,
  `problems` em português e apontando o lance ("Lance #2 foi alterado depois de registrado"), o hash final e cada lance
  com `prev_hash`, `hash` e `valid`. Só aparecem pseudônimos.
- **Testado:** alteração de valor e de licitante, remoção no meio e no fim (com o lote "voltado"), inserção forjada,
  troca de líder, de prazo e de lance inicial, hash forjado com caracteres não ASCII, e um `UPDATE` direto no Postgres
  com o trigger desligado.

**Limite conhecido.** Quem tem a chave **e** o banco consegue forjar, e restaurar um backup antigo inteiro também
resulta num estado consistente. Nesses casos, só as âncoras já entregues denunciam.

<a id="d19"></a>
## D19 · Liquidação e comprovante

*2026-10-23 · Em vigor (nova tentativa após falha revista em [D38](#d38))*

**Contexto.** Era preciso implementar a D05 e pagar uma vez só, mesmo com falhas, lances de último segundo e mais de
um worker.

**Decisão.**

- **Worker:** o comando `run_settlement` roda em loop de 1 s, num serviço próprio. Uma falha geral, como o banco fora
  do ar, é registrada e o loop continua. Ele também apaga as sessões expiradas uma vez por dia.
- **Um lote por transação:** trava com `SELECT ... FOR UPDATE SKIP LOCKED` e reconfere, sob a trava, que o lote está
  aberto e vencido. Um lance de último segundo que prorrogou o fim tira o lote da fila.
- **Selo antes do dinheiro:** um lote com selo inválido (D18) não é liquidado, não move dinheiro e sai da fila até o
  worker reiniciar. Também é bloqueado se o saldo do vencedor não cobrir o lance, o que não deveria acontecer, porque o
  valor fica comprometido enquanto ele lidera.
- **Um lote com erro não trava os outros:** o erro é registrado com o id do lote, e os demais seguem.
- **Ordem das travas:** lote, carteira do vencedor e carteira do leiloeiro. O leiloeiro nunca dá lance, então não há
  ciclo com os lances e não há deadlock.
- **Pagamento uma vez só, garantido pelo banco:** os lançamentos `settlement_debit` e `settlement_credit` têm restrição
  única por lote e tipo, e há um comprovante por lote. Testado com dois workers liquidando o mesmo lote ao mesmo tempo.
- **Lote sem lances** fecha como liquidado, sem lançamentos e sem comprovante.
- **Comprovante:** token aleatório de 144 bits, pseudônimo do vencedor (nunca o e-mail), valor, último hash da cadeia e
  assinatura HMAC. Um trigger impede alterá-lo ou apagá-lo. `GET /api/receipts/{token}/` é público e diz se o
  comprovante é autêntico e se o histórico atual ainda termina no hash gravado.
- **Painéis:** `GET /api/lots/mine/` traz o resultado de cada lote; `GET /api/lots/mine/summary/` traz o total
  recebido, os arrematados e os sem lances; `GET /api/lots/participating/` traz o token do comprovante só para o
  vencedor.

<a id="d20"></a>
## D20 · Tempo real com SSE na prática

*2026-10-23 · Em vigor*

**Contexto.** Era preciso implementar a D03 de modo que muitos espectadores custem pouco, nenhum evento se perca na
reconexão e nenhum evento saia de algo que foi desfeito.

**Decisão.**

- **Servidor ASGI:** `uvicorn` no lugar do `gunicorn`. Com 8 threads no gunicorn, só 8 pessoas poderiam assistir às
  salas ao mesmo tempo. Com ASGI, o stream é uma view assíncrona e cada conexão aberta custa pouco; as outras views
  seguem síncronas. O uvicorn roda com `--no-proxy-headers`, para que quem decida o IP do cliente continue sendo o
  `NUM_PROXIES` do DRF (D10).
- **Rota:** `GET /api/lots/{id}/events/`, pública. Eventos: `state`, `bid` (com `id` igual à sequência do lance),
  `closed`, `cancelled`, `resync` (o navegador deve recarregar o estado) e `unavailable`. Um `: ping` a cada 15 s
  mantém a conexão viva.
- **Aviso só depois do commit:** lance, liquidação e cancelamento chamam `pg_notify` dentro da transação, e o Postgres
  só entrega o aviso depois do `COMMIT`. Um lance recusado não gera evento (há teste).
- **Um distribuidor por processo:** uma única conexão `LISTEN` repassa os eventos para filas por lote, e os dados são
  lidos do banco uma vez por evento, não uma vez por espectador. Se essa conexão cai, ela reconecta e manda `resync`
  a todos. Um cliente lento recebe `resync` em vez de eventos atrasados.
- **Retomada:** com o `Last-Event-ID`, o servidor manda o estado atual e os lances mais novos, lidos do banco.
- **Nenhum espectador fantasma:** a inscrição vive dentro do stream, com `try/finally`. Se o cliente sai cedo ou o banco
  falha ao abrir a sala, não sobra inscrição (testado com o uvicorn real).
- **Limites (OWASP, esgotamento de recursos):** 2000 streams por processo e 20 por cliente.
- **"Você está ganhando" sem recarregar:** o detalhe do lote traz o pseudônimo do próprio usuário (`me.alias`), e o
  front o compara com o de cada lance que chega.
- **Windows:** o psycopg assíncrono não funciona com o laço de eventos padrão do Windows. Só os testes locais usam o
  `SelectorEventLoop`; no Docker, que é Linux, nada muda.

---

# Frontend

<a id="d21"></a>
## D21 · Base do frontend e porta de entrada com nginx

*2026-10-23 · Em vigor no ambiente local (na produção, o repasse é feito pelo `src/proxy.ts`: [D36](#d36))*

**Contexto.** A ideia inicial (D07) era o Next.js repassar `/api/` ao Django. Um experimento com o Next 16.4.3 mostrou
três problemas nesse repasse: ele **retinha o SSE** (três eventos com 1 s de intervalo chegavam juntos no fim),
repassava o `X-Forwarded-For` do cliente **sem acrescentar o IP real** (um IP forjado chegava intacto) e removia a
barra final das URLs.

**Decisão: nginx na frente de tudo,** numa porta única (`http://localhost:8080`).

- `/api/` vai direto ao Django, sem buffer e com timeout de leitura de 1 h, para o SSE.
- O `X-Forwarded-For` é **sobrescrito** com o IP real, nunca acrescentado. O backend não tem porta pública e usa
  `NUM_PROXIES=1`. Verificado: mesmo forjando um IP diferente a cada tentativa, o 6º login dá 429.
- O resto vai ao Next. Navegador e API ficam na mesma origem, então cookies e CSRF funcionam sem CORS.
- Atrás de outro balanceador, o nginx precisaria de `set_real_ip_from` e `real_ip_header` para enxergar o IP real.

**Stack do front.** Next.js 16.4.3 (App Router), React 19.3, TypeScript, Tailwind 4 e next-themes 0.4.6, com testes em
Vitest, Testing Library e jsdom.

**Segurança no front.**

- **CSP com nonce por requisição,** conforme o guia oficial do Next, em `src/proxy.ts`: `script-src 'self' 'nonce-…'
  'strict-dynamic'`, `style-src` com nonce, `object-src 'none'` e `frame-ancestors 'none'`. O nonce exige renderização
  dinâmica, por isso `cacheComponents` e `partialPrefetching` foram desligados. Só em desenvolvimento entram
  `'unsafe-eval'` e estilos sem nonce, porque o `next dev` precisa deles. Há teste que falha se a produção perder o
  nonce.
- Também: `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, `Permissions-Policy` e nenhum `X-Powered-By`.

**Também nesta etapa.**

- Tema claro, escuro ou do sistema, sem erro de hidratação.
- `AuthProvider`, que carrega `/api/auth/me/`.
- Cliente da API que lê o cookie de CSRF a cada escrita (D10).
- Mensagens de erro em português, ligadas ao campo (`aria-describedby`, `aria-invalid`).
- Telas de entrar e de cadastro.
- Imagem Docker em vários estágios, com usuário sem privilégios.

<a id="d22"></a>
## D22 · Identidade visual: catálogo de casa de leilão

*2026-10-23 · Em vigor*

**Contexto.** A interface precisava de uma identidade própria, coerente com uma casa de leilão de arte, em vez da cara
de um modelo genérico.

**Decisão.** Uma direção visual inspirada nos catálogos impressos das grandes casas de leilão.

- **Tipografia:** títulos em Fraunces (serifa variável com tamanho óptico, licença OFL), servida pelo próprio site, sem
  Google no build e dentro da CSP (`font-src 'self'`). A interface usa a fonte do sistema. Rótulos em versalete
  espaçado ("LOTE 012"), valores com algarismos tabulares e títulos com quebra balanceada.
- **Cores:** de dia, papel `#f5efe4`, tinta `#1b1714` e vermelho-sangue `#8a1c12`; à noite, uma sala de galeria com
  luz baixa (`#13100d`, `#efe7da` e vermelhão `#e2583f`). O contraste AA foi conferido em todas as combinações de texto
  usadas.
- **Composição:** filetes finos e duplos no lugar de caixas com sombra; cantos quase retos; cabeçalho como o topo de um
  jornal; rodapé como colofão, com o crédito ao Met (Open Access, CC0); "Como funciona" em algarismos romanos; telas
  de entrar e cadastro em duas colunas; ícone próprio.
- **Movimento contido:** botões encolhem levemente ao pressionar; o sublinhado da navegação nasce da esquerda, só onde
  há mouse; `prefers-reduced-motion` desliga o movimento; o toque não tem atraso nem realce azul.
- **Acessibilidade e formulários:** o anel de foco nunca é escondido; depois de uma falha, o foco vai ao primeiro campo
  com erro; campos vazios são explicados no próprio formulário ("Informe seu e-mail."), sem chamar o servidor; o aviso
  genérico não se repete quando o erro já está no campo; há um link "Pular para o conteúdo".

**Verificado** com Playwright em 1440 e 375 px, nos temas claro e escuro, sem rolagem horizontal.

<a id="d23"></a>
## D23 · Vitrine, busca e filtros

*2026-10-23 · Em vigor*

**Contexto.** A vitrine é a porta de entrada do visitante. Ela precisa ser rápida, poder ser compartilhada e funcionar
sem JavaScript.

**Decisão.**

- **Renderizada no servidor:** `/lotes` lê a API direto no backend, pela rede interna, e repassa o IP do visitante para
  os limites da API valerem por pessoa. Sem cache e com timeout de 5 s.
- **Estado na URL:** `?fase=ao-vivo|agendados|encerrados&q=…&pagina=N`. As abas são links e a busca é um formulário
  `GET`: funciona sem JavaScript, pode ser compartilhada, e o botão voltar do navegador funciona. Parâmetro inválido
  vira o padrão, e uma página que deixou de existir leva à primeira.
- **Estados vazios e de erro:** vazio por fase (a aba ao vivo vazia aponta para os agendados), busca sem resultado com
  "Limpar a busca" e backend fora com "Tentar de novo". A contagem tem concordância ("1 lote agendado") e é anunciada
  ao leitor de tela.
- **Cartão de lote:** obra emoldurada em 4:5, selo "Ao vivo", ficha técnica e um bloco de preço por fase:
  - ao vivo: lance atual ou inicial, número de lances ou incremento, e "termina em";
  - agendado: lance inicial e "abre em";
  - encerrado: lance final, ou "Encerrado sem lances".
- **Contagem regressiva pela hora do servidor:** a página traz a hora do servidor, e o relógio corrige a diferença do
  relógio do visitante, errando para menos tempo, que é o lado seguro. Um único relógio de 1 s serve à página toda. Nos
  últimos 2 minutos aparece "Um lance agora prorroga o pregão."
- **Home:** "Ao vivo agora" com os primeiros lotes; sem lotes ao vivo, "Em breve na vitrine" com os agendados.
- **Visitante sem login:** `GET /api/auth/me/` responde 204, o que tira um erro do console de todas as páginas.

**Achado sobre o Met.** A proteção anti-robô do museu (Imperva) **bloqueou o IP** depois de rajadas de chamadas, e o
bloqueio pode durar horas. A busca já tratava esse 403 como "catálogo indisponível". A resposta veio em duas etapas:
menos chamadas em paralelo ([D24](#d24)) e cache compartilhado ([D27](#d27)).

<a id="d24"></a>
## D24 · Painel do leiloeiro

*2026-10-23 · Em vigor (cancelamento revisto em [D29](#d29))*

**Contexto.** O leiloeiro precisa buscar uma obra, definir as condições do pregão, acompanhar os próprios lotes e ver
quanto recebeu.

**Decisão.**

- **`/painel` renderizado no navegador,** não no servidor, porque os dados são da conta e usam a sessão. Visitante vê
  "Entrar", e licitante é levado à vitrine. Só o leiloeiro vê o link "Painel" no cabeçalho.
- **Balanço:** total recebido, arrematados e encerrados sem lances.
- **Novo lote em dois passos:** primeiro a busca no acervo do Met, com miniaturas e "Mostrar mais"; depois a ficha da
  obra como vai aparecer na vitrine, ao lado das condições, dentro do mesmo formulário, para o leitor de tela ouvir a
  obra junto das condições.
- **Dinheiro:** campo de texto com teclado numérico no celular, que aceita `1.250,50`, `1250.50` e `R$ 99,9` e recusa
  mais de 2 casas ou separadores fora do lugar. Validado no navegador e de novo no backend.
- **Datas:** no horário do computador, convertidas para ISO com fuso, porque o backend exige (D16). O padrão é o
  próximo quarto de hora, com fim 24 h depois, e há atalhos de 1 hora, 1 dia, 3 dias e 7 dias.
- **Seus lotes:** situação, lance ou resultado ("Arrematado por R$ X", "Encerrado sem lances", "Aguardando
  liquidação") e contagem regressiva. Cancelar pede confirmação, com o foco na opção segura ("Manter").
- **Respostas fora de ordem:** cada recarga ignora a resposta se uma mais nova já foi pedida, para não mostrar um estado
  velho depois de duas ações seguidas.
- **Mensagem do 503:** se o backend explica a falha (por exemplo, o acervo do Met fora do ar), o painel mostra essa
  explicação; sem ela, mostra a mensagem genérica.
- **CSP:** `img-src` libera só `https://images.metmuseum.org`, para as miniaturas da busca. Os lotes usam a cópia local
  da imagem.
- **Met:** a busca passou a fazer 3 chamadas em paralelo em vez de 6, para disparar menos o bloqueio anti-robô.

<a id="d25"></a>
## D25 · Sala ao vivo, carteira e meus arremates

*2026-10-23 · Em vigor (cancelamento ao vivo em [D29](#d29))*

**Contexto.** A sala é onde a disputa acontece: o lance de uma pessoa precisa aparecer na tela da outra na hora, com
"ganhando" ou "superado" e o saldo corretos.

**Decisão.**

- **Sala `/lotes/[id]`:** o servidor renderiza o lote e os lances mais recentes, então o link pode ser compartilhado e
  funciona sem JavaScript. Lote inexistente ou cancelado leva à página "não encontrada", e backend fora mostra "Tentar
  de novo".
- **Tempo real:** o navegador conecta ao SSE (D20) e reconecta sozinho. Todo evento passa por uma função pura,
  `applyEvent`, que insere cada lance uma vez só e em ordem, mesmo quando o mesmo lance chega pelo `POST` e pelo SSE. A
  conexão fecha quando o lote é liquidado ou cancelado. Se o servidor recusa a conexão por limite, a sala avisa que o
  tempo real está indisponível, em vez de mostrar "Reconectando…" para sempre.
- **Fim pelo relógio do servidor:** quando o tempo acaba, o formulário fecha e aparece "Apurando o resultado…". O
  resultado chega com o evento `closed`, depois que o worker liquida.
- **Situação e saldo:** vêm do backend e são buscados de novo a cada lance que chega, porque ser superado libera o
  saldo. As buscas esperam 300 ms sem novos lances, então uma rajada de lances vira uma busca só. Depois do próprio
  lance aceito, "ganhando" aparece na hora.
- **Campo do lance:** começa no mínimo e acompanha o novo mínimo quando chega outro lance. As recusas mostram a
  mensagem do backend.
- **Prorrogação visível:** quando o fim avança, aparece "Pregão prorrogado até HH:MM".
- **Encerrado:** o resultado substitui a disputa ("Arrematado por…", "Você arrematou este lote", "Seu lance não foi o
  vencedor.").
- **Carteira `/carteira`:** total, comprometido e disponível, com uma frase explicando cada um, e depósito simulado com
  atalhos.
- **Meus arremates `/arremates`:** "Em disputa" e "Encerrados", com "Ver comprovante" e "Copiar link do comprovante".
  Enquanto o worker não emite o comprovante, aparece "Emitindo o comprovante…".

**Validado** com duas licitantes em janelas separadas: o lance de uma aparece na outra sem recarregar, e "superado"
troca de lado. Um lance com 1 min 59 s restantes prorrogou o fim, e um com 2 min 32 s não. Um lote de 45 s fechou, foi
liquidado e mostrou "Você arrematou" ao vivo.

<a id="d26"></a>
## D26 · Auditoria e comprovante públicos

*2026-10-23 · Em vigor*

**Contexto.** Qualquer pessoa, sem conta, deve conseguir conferir o histórico de um lote e a autenticidade de um
comprovante.

**Decisão.**

- **Renderizadas no servidor, sem login e sem cache:** `/lotes/[id]/auditoria` e `/comprovante/[token]` refazem a
  verificação a cada visita e funcionam sem JavaScript.
- **Auditoria:**
  - o veredito no título ("Histórico íntegro" ou "Histórico adulterado") e os problemas encontrados;
  - cada lance com pseudônimo, valor, hora, hash e "Confere" ou "Não confere" (a cor sempre acompanhada da palavra);
  - o selo final, para copiar;
  - uma explicação honesta do que foi verificado e de quem precisa confiar em quê;
  - os dados brutos em JSON.
- **Comprovante:** obra, lote, valor, pseudônimo do arrematante, data e selo final. A cada visita, três verificações:
  assinatura do comprovante, histórico íntegro e selo final igual ao do histórico. "Comprovante autêntico" só aparece
  com as três; se uma falha, o aviso diz qual e leva à auditoria. A página não é indexada por buscadores.
- **Impressão:** sem cabeçalho nem botões, sempre no tema claro e com o endereço completo do comprovante no papel.
- **Sala:** link "Auditar o histórico completo" ao pé do histórico.

**Validado com uma adulteração real.** Depois de liquidar um lote de teste, um lance foi alterado direto no banco
(R$ 300 → R$ 290, com o trigger desligado). A auditoria passou a dizer "Histórico adulterado · Lance #1 foi alterado
depois de registrado.", e o comprovante, "não confere".

---

# Qualidade e evolução

<a id="d27"></a>
## D27 · Cache compartilhado, revisão OWASP e documentação

*2026-10-23 · Em vigor*

**Contexto.** No fechamento das tarefas, o cache em memória sumia a cada reinício (o que gerou as rajadas ao Met da
D23) e não valia entre processos. Também faltava uma revisão de segurança completa.

**Decisão.**

- **Cache no Postgres** (`DatabaseCache`, tabela `verz_cache`), sem um serviço novo como o Redis. A busca no Met
  sobrevive a reinícios (medido: 2,7 s na primeira vez e 0,12 s depois de reiniciar), e os limites de tentativa passam
  a valer para todos os processos. Nos testes, o cache fica em memória.
- **Revisão OWASP (Top 10:2025), achados corrigidos:**
  - **[Médio] Postgres publicado em todas as interfaces com a senha padrão:** agora só em `127.0.0.1`.
  - **[Médio] Chaves de exemplo aceitas:** fora do modo debug, o backend não sobe com a `SECRET_KEY` ou a
    `AUDIT_HMAC_KEY` de exemplo. Com a chave da auditoria conhecida, quem altera o banco refaria a cadeia.
  - **[Baixo] Sem log de segurança:** logger `verz.security` para login falho (IP e hash do e-mail, nunca o e-mail ou
    a senha), lance recusado por integridade e auditoria com problema.
  - **[Baixo] HSTS só na API:** as páginas do Next também passaram a enviar `Strict-Transport-Security`.
- **Verificado sem achado:** injeção, XSS, SSRF, CSRF, fixação de sessão, acesso a dados de outro usuário, corrida em
  lances e saldo, e força do token do comprovante.
- **`check --deploy`:** os avisos que restam são de ambiente local (HTTPS sem redirecionamento e preload do HSTS, ambos
  configuráveis), e o README ensina a gerar uma `SECRET_KEY` com o tamanho recomendado.
- **Documentação:** os READMEs dos dois repositórios explicam como rodar e testar, as rotas, a resiliência, a
  integridade e a segurança.

<a id="d28"></a>
## D28 · Dados de demonstração (`seed_demo`)

*2026-10-24 · Em vigor, só no ambiente local*

**Contexto.** Para avaliar o fluxo localmente, é preciso ter contas e lotes em todas as situações sem montar tudo à
mão.

**Decisão.** O comando `python manage.py seed_demo` cria três contas e cinco lotes com obras reais do Met, um em cada
situação: ao vivo e disputado, ao vivo terminando em 10 minutos, agendado, arrematado com comprovante e encerrado sem
lances. No fim, imprime as contas, a senha e o endereço de cada lote.

- **Senha fixa e pública** (`leilao-demo-2026`, no README), com aviso, na saída e no README, de que é só para uso
  local.
- **Obras buscadas no Met na hora,** em vez de guardar imagens no repositório. São 4 obras fixas, buscadas uma de cada
  vez para não disparar o bloqueio anti-robô e reaproveitando o cache. Tudo é buscado antes de gravar: com o museu
  fora, nada é criado.
- **Tudo pelo caminho real:** a criação de lote saiu da view para `auctions/lots.create_lot`, usada pela API e pelo
  seed; os lances passam por `place_bid`; e o lote arrematado passa pela liquidação real. Comprovante e auditoria saem
  íntegros, como num pregão de verdade.
- **Rodar de novo** cria um conjunto novo, porque lances são imutáveis, e recarrega o saldo das licitantes quando fica
  abaixo de R$ 5.000.
- **Conexão local por `127.0.0.1`:** com o Postgres publicado só no loopback (D27), conectar por `localhost` levava
  30 s no Windows, que tenta IPv6 antes. O `.env.example` usa `127.0.0.1`.

<a id="d29"></a>
## D29 · Cancelamento de lote com lances e estorno automático

*2026-10-24 · Em vigor (revê a D08, a D16, a D17 e a D24)*

**Contexto.** Até aqui, um lote só podia ser cancelado sem lances. Na prática, o leiloeiro também precisa cancelar um
pregão em andamento ou até um arremate (obra danificada, erro no cadastro), e o dinheiro precisa voltar sozinho.

**Decisão.** Cancelar também depois do arremate, por até 7 dias, sempre com motivo quando houver lances.

| Situação | Pode cancelar? | O que acontece com o dinheiro |
|---|---|---|
| Agendado ou ao vivo, sem lances | Sim, sem motivo | Nada |
| Agendado ou ao vivo, com lances | Sim, com motivo | O comprometido de quem ganhava é liberado (é calculado, D08) |
| Encerrado, aguardando a liquidação | Sim, com motivo | Igual ao anterior; o worker só liquida lotes abertos |
| Arrematado há até 7 dias | Sim, com motivo | O valor volta ao vencedor (`refund_credit`) e sai do saldo do leiloeiro (`refund_debit`) |
| Arrematado há mais de 7 dias, ou encerrado sem lances | Não | — |

- **Uma transação só** (`auctions/cancellation.py: cancel_lot`), com a mesma ordem de travas do lance e da liquidação.
  Lance e cancelamento simultâneos nunca ficam pela metade (há teste com duas threads).
- **O leiloeiro sempre tem saldo para o estorno,** porque não dá lances e não há saque.
- **Registro de cancelamento imutável e assinado:** guarda motivo, data e valor estornado, com trigger e HMAC. O motivo
  não entrou no selo do lote, porque mudar a fórmula do selo faria todos os lotes existentes parecerem adulterados. O
  selo já cobre o status cancelado, e a auditoria confere a assinatura do cancelamento.
- **Estorno uma vez só,** pela mesma restrição única que impede liquidar duas vezes.
- **O comprovante continua imutável e autêntico.** As páginas mostram "Arremate cancelado", com motivo, data e valor.
- **Telas:**
  - **Painel:** diz o efeito antes do clique e até quando o lote pode ser cancelado. O motivo é obrigatório, com
    contador. Sem motivo, o botão não fica desabilitado: o clique mostra o erro junto ao campo e foca nele.
  - **Sala:** recebe o aviso ao vivo, com o motivo, e fecha o formulário.
  - **Meus arremates:** mostra os cancelados, com o estorno para quem pagou e o link para a auditoria.
  - **Comprovante e auditoria:** mostram o bloco do cancelamento.
  - **Balanço do leiloeiro:** desconta os estornos.

**Validado** sobre os dados do `seed_demo`: cancelar o lote disputado liberou o comprometido da licitante, e a outra
recebeu o aviso na sala pelo celular. Cancelar um arremate devolveu o valor, o comprovante passou a dizer "Arremate
cancelado" e a auditoria seguiu íntegra.

<a id="d30"></a>
## D30 · Testes ponta a ponta

*2026-10-24 · Em vigor*

**Contexto.** Os testes unitários não provam que os fluxos funcionam juntos, no navegador, com a pilha inteira no ar.

**Decisão.** `@playwright/test` em `verz-frontend/e2e/`, contra a pilha do Docker. `npm run e2e` roda a suíte, e
`npm run e2e:met` roda à parte o único fluxo que depende do museu, para a suíte principal não cair junto com o Met.

- **Dados próprios a cada execução:** `python manage.py e2e_fixtures` cria contas novas, com senha aleatória e a sessão
  já aberta (passar pelo login estouraria o limite de 5 por minuto), e lotes sob medida, sem depender do Met. Os lotes
  com relógio (prorrogação e liquidação) são pedidos pelo próprio teste na hora.
- **Cobertura:**
  - vitrine e filtros pela URL;
  - cadastro, saída e senha errada;
  - sala com duas licitantes: o lance aparece na outra sem recarregar, "ganhando/superado" troca de lado, o saldo
    comprometido volta e as recusas aparecem;
  - prorrogação nos últimos 2 minutos;
  - liquidação ao vivo até o comprovante;
  - carteira;
  - cancelamento com motivo e estorno;
  - auditoria e comprovante públicos, e a página não encontrada.
- **Em todo teste:** console limpo e nenhuma rolagem horizontal. Vitrine, sala e auditoria rodam também em 320 px
  (D32).
- **Um teste por vez e sem repetição automática:** vários testes mexem no mesmo saldo, e um teste instável se corrige,
  não se repete. Em caso de falha, ficam trace, captura de tela e vídeo.
- **Prova de que pega regressão:** com o aviso de prorrogação desligado de propósito, o teste de prorrogação falhou.

**Problema real que a suíte achou.** As páginas renderizadas no servidor liam a API sem a sessão, então contavam no
limite de visitante (60 por minuto por IP) até para quem estava logado, e, ao bater no limite, diziam "não conseguimos
falar com o servidor". Agora a leitura no servidor repassa só o cookie de sessão, sem afrouxar nenhum limite, e quem
está logado conta no limite da própria conta. Ao bater no limite, a página diz "Muitas páginas abertas em sequência.
Aguarde alguns segundos".

**Limite que fica.** As páginas de visitante da suíte usam cerca de 40 das 60 requisições por minuto por IP, então é
preciso esperar 1 minuto entre duas execuções.

<a id="d31"></a>
## D31 · Revisão dos limites conhecidos

*2026-10-24 · Em vigor*

**Contexto.** Antes da entrega, cada limite anotado nas decisões anteriores foi revisto, para ser corrigido ou aceito e
documentado.

**Corrigidos.**

- **Buscas no Met "esperando uma pela outra" (D27):** o limite não existia, porque a busca já rodava fora da transação
  da requisição (D15). Ficou um teste que falha se alguém tirar o `non_atomic_requests`.
- **Auditoria paginada:** a API continua verificando a cadeia **inteira**, que é o que sustenta o veredito, mas a lista
  vem em páginas de 50. O veredito e os problemas valem para o lote todo em qualquer página: a adulteração do lance #55
  aparece na página 1 (há teste). A tela pagina pela URL e funciona sem JavaScript.
- **Acessibilidade:** o axe roda as regras WCAG 2.1 A/AA em 11 telas, nos dois temas. Ele achou uma violação, corrigida:
  o nome do link do logotipo não continha o texto visível (WCAG 2.5.3). Um roteiro para leitor de tela
  (`verz-frontend/docs/leitor-de-tela.md`) achou outra falha, que o axe não pega: "Você foi superado" não era
  anunciado. Agora é, e há teste.
- **HTTPS:** o README do backend explica como terminar o TLS no nginx e o que muda quando o TLS termina num proxy antes
  dele.

**Aceitos e documentados.**

- **CI:** os testes rodam à mão (`pytest`, `npm test` e `npm run e2e`).
- **O cadastro revela se o e-mail já existe:** esconder isso exigiria confirmar a conta por e-mail, e o projeto não
  envia e-mail. O login responde igual nos dois casos, e o cadastro tem limite de 10 por hora por IP.
- **Visitantes atrás do mesmo IP dividem 60 requisições por minuto:** afrouxar enfraqueceria a proteção das leituras
  públicas. Quem está logado conta no limite da própria conta.

<a id="d32"></a>
## D32 · Revisão de responsividade

*2026-10-24 · Em vigor*

**Contexto.** O site precisa funcionar do celular mais estreito ao desktop.

**Como foi medido.** Um script Playwright passou por 14 telas em 320, 375, 414, 768, 1024 e 1440 px e no celular
deitado, medindo rolagem horizontal, alvos de toque e tamanho de fonte dos campos.

**Ajustes.**

- **Rolagem horizontal em 320 px:** as abas da vitrine passavam 24 px da tela. Agora quebram linha, e o e2e passou a
  rodar em 320 px, a tela mais estreita em uso.
- **Alvos de toque:** links e botões de texto tinham 22 a 24 px de altura. Em tela de toque, ganham uma área invisível
  que leva o alvo a cerca de 40 px sem mexer no layout. Links dentro de texto corrido ficam como estão, como a WCAG
  2.5.8 permite.
- **Imagem da sala no tablet e no celular deitado:** a moldura ocupava a tela inteira e empurrava o lance para baixo.
  Agora não passa de 70% da altura visível.
- **iOS deitado:** o Safari não infla mais a fonte.

**Já estava certo.** Campos com 16 px (sem zoom no iOS), hover só com mouse, nenhum atraso de toque e nenhum bloqueio
de zoom.

**Não verificado.** Aparelho real (toque e Safari), que a emulação não reproduz.

<a id="d38"></a>
## D38 · Revisão do tratamento de erros

*2026-10-27 · Em vigor*

**Contexto.** Uma revisão dos caminhos de erro, de ponta a ponta, achou situações em que a pessoa via uma mensagem crua
em inglês, uma tela que fingia estar tudo bem ou um log poluído.

**Decisão.**

- **Sessão expirada:** sem sessão, a API responde **401** (antes, 403), e falta de permissão continua 403. O front
  trata todo 401 como "Sua sessão expirou. Entre de novo para continuar.", passa a tratar a pessoa como deslogada e
  mostra "Sua sessão expirou." no cabeçalho. Antes, a tela mostrava a mensagem do DRF e o cabeçalho seguia com a conta.
  O esquema `Session` no 401 não abre a janela de login do navegador.
- **Erro de CSRF:** o DRF não traduz "CSRF Failed: …", que aparece com cookies bloqueados. O front troca por "Não foi
  possível validar sua sessão neste navegador. Ative os cookies e recarregue a página."
- **Erro inesperado na renderização:** páginas de erro próprias (`app/error.tsx` e `app/global-error.tsx`), no estilo
  do catálogo e com "Tentar de novo", no lugar da página padrão do Next em inglês.
- **Sala:** se a atualização da situação ou do saldo falha, a sala avisa que os dados podem estar desatualizados, em vez
  de engolir o erro. O aviso some na próxima atualização bem-sucedida.
- **Worker:** um lote que falha ao liquidar só é tentado de novo depois de 1 minuto (antes, a cada segundo, com um
  stack trace no log a cada vez). Um lote adulterado continua fora da fila até o worker reiniciar.

**Fica de fora.** Um health check que reinicie a API se ela travar: exigiria liberar `/api/health/` do segredo do proxy
(D36) e mudar a configuração da hospedagem.

---

# Produção

<a id="d36"></a>
## D36 · Só o front fala com a API: segredo do proxy

*2026-10-27 · Em vigor (ativada e validada na produção)*

**Contexto.** Na produção, o front fica na Vercel e a API na Fly.io. O navegador chama a API pelo domínio do front
(`/api/*`), e a Vercel repassa à Fly. Para enxergar o IP real do visitante atrás dessas camadas, o backend usa
`NUM_PROXIES=3`. Com isso, quem chamasse `verz-api.fly.dev` direto, sem passar pela Vercel, conseguia forjar o IP e
escapar dos limites por IP. O limite de login por e-mail continuava valendo. Fechar a API numa rede privada não
resolve, porque a Vercel não alcança a rede interna da Fly e não tem IPs fixos para liberar.

**Decisão.** Um segredo compartilhado entre o front e a API.

- O `src/proxy.ts` repassa `/api/*` à Fly acrescentando `X-Verz-Proxy: <PROXY_SECRET>`, no lugar do rewrite do
  `next.config.ts`, que não consegue acrescentar cabeçalhos. As leituras feitas no servidor mandam o mesmo cabeçalho.
- No Django, o `ProxySecretMiddleware` recusa com 403 o que chega sem o segredo certo, comparando em tempo constante.
  Sem `PROXY_SECRET` (Docker local e testes), ele não faz nada.
- O segredo foi gerado localmente e gravado direto nos segredos da Fly e da Vercel, sem passar por nenhum arquivo do
  repositório.

**Validado.**

- **Localmente,** com o front apontando para um servidor de eco: o segredo é acrescentado e sobrescreve o que o
  visitante mandar, corpo e query passam, e o streaming não é retido.
- **Na produção:** chamar `verz-api.fly.dev` direto dá 403, sem segredo ou com segredo errado. Pela Vercel, tudo segue
  funcionando: páginas com dados do servidor, CSRF, login, o IP real do visitante (mesmo quando ele forja outro) e o
  tempo real, com o `ping` chegando no tempo.

**Consequência.** Se a Vercel deixar de mandar o segredo (variável apagada, preview sem ela), a API responde 403 para
tudo. Em emergência, a checagem se desliga com `fly secrets unset PROXY_SECRET`.
