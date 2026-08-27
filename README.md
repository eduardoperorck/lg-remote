# lg-remote

Controle remoto da TV LG (webOS) que roda no navegador do celular, sem assinatura e sem loja
de aplicativos. Além dos botões, ele busca uma série ou filme, descobre em qual serviço o
título está no Brasil e **abre o app da TV já com a busca preenchida**.

---

## Como funciona

```
iPhone (PWA)  ──HTTP──▶  este servidor  ──wss://3001──▶  TV LG
                              │
                              └──HTTPS──▶  TMDb (o que está em qual streaming)
```

O servidor existe por um motivo específico: TVs LG de 2018+ só aceitam conexão em
`wss://IP:3001` com **certificado autoassinado**, e nenhum navegador de celular aceita isso.
O servidor ignora o certificado e vira a ponte.

## Requisitos

- Um PC (ou Raspberry Pi / NAS) **ligado e na mesma rede da TV** enquanto você usa o controle.
- Python 3.12+. No Windows, o jeito mais simples é o `uv`.
- Opcional: token gratuito do TMDb, se quiser a busca de títulos.

---

## Instalação (Windows)

Ligue a TV, deixe-a na mesma rede que o PC e **clique duas vezes em `setup.bat`**.

Ele instala o que falta (o `uv`, se ainda não existir), procura a TV na rede, pareia,
lê os apps instalados, pergunta o PIN e sobe o servidor. Um passo só.

Se preferir pelo terminal:

```powershell
.\setup.bat                    # Prompt de Comando
.\setup.ps1                    # PowerShell
```

Durante o processo:

- **A TV vai pedir autorização** — aceite com o controle físico. Acontece uma vez só, e há
  cerca de um minuto para responder. A TV precisa estar **ligada e na tela inicial**: em
  standby ela continua respondendo na rede, mas recusa sem exibir o pedido (o assistente
  reconhece esse caso e diz o que fazer).
- **O PIN** protege a TV de qualquer outro aparelho na rede. O assistente sugere um.
- **A credencial do TMDb** é opcional e liga a busca por nome. Gratuita em
  **themoviedb.org → Settings → API**. Tanto a **chave v3** (32 caracteres) quanto o
  **token v4** (longo) funcionam — o assistente detecta qual você colou e testa na hora.
  Dá para pular e adicionar depois no `.env`.
- **O Windows vai pedir liberação de firewall** — aceite para rede **privada**, senão o
  celular não alcança o servidor.

O IP da TV pode mudar sem aviso — o controle guarda a identidade dela (`TV_UUID`) e a
reencontra sozinho quando isso acontece, regravando o `.env`. Reservar o IP no roteador
continua sendo melhor, porque evita a pausa de alguns segundos na primeira falha.

Para ligar a TV pelo celular, ela precisa de **Configurações → Geral → Mobile TV On / Ativar
via Wi-Fi** habilitado. Sem isso, o app só desliga.

### Depois da primeira vez

**Não é para rodar o `setup.bat` de novo.** Ele é de instalação, não de uso.

O assistente pergunta se você quer que o controle **suba junto com o Windows** — respondendo
que sim, não precisa fazer mais nada: ele fica no ar desde o login, sem janela, e continua no
ar mesmo se a TV mudar de IP. O log vai para `local-data/serve.log`.

Para ligar ou desligar isso depois:

```powershell
uv run lgremote autostart --install    # --remove desfaz, --status mostra o estado
```

Sem o início automático, clique duas vezes em **`serve.bat`** e deixe a janela aberta.

Se um dia parar de funcionar, o primeiro comando é sempre este — ele diz o que está certo,
o que falta e o que fazer:

```powershell
uv run lgremote doctor
```

Rodar o assistente outra vez também é seguro e rápido: ele reaproveita o IP e o pareamento
que já estão salvos, e não repete as perguntas já respondidas.

### Se o Windows bloquear o Python

Em Windows 11 com **Smart App Control** ligado, o Python que o `uv` baixa fica em `AppData`
sem assinatura reconhecida, e o sistema bloqueia suas DLLs — o erro aparece como
`DLL load failed while importing _ctypes`. O `setup.bat` detecta isso e passa a usar um
Python do sistema automaticamente; se não houver nenhum, ele pede para instalar:

```powershell
winget install Python.Python.3.12
```

**Não desligue o Smart App Control.** No Windows 11 essa mudança é irreversível sem
reinstalar o sistema, e enfraquece a proteção da máquina inteira para resolver um
problema que tem saída melhor.

### Se preferir passo a passo

O assistente é só a soma destes comandos:

```powershell
uv run lgremote pair --host 192.168.0.10   # parear (o IP está em Configurações → Rede)
uv run lgremote discover                    # ler os apps reais da TV
# preencher UI_PIN, TMDB_TOKEN e TV_MAC no .env
uv run lgremote serve
```

### Instalar no iPhone

Mesmo Wi-Fi → Safari → `http://<IP-do-PC>:8765` → **Compartilhar → Adicionar à Tela de Início**.
Vira um ícone com tela cheia, sem barra do Safari.

> Não há service worker: em HTTP na rede local o iOS não considera contexto seguro e não o
> registraria. Não faz diferença — um controle remoto é inútil offline.

---

## Trocar legenda e áudio

Os apps de streaming **não expõem nenhuma API** para legenda ou faixa de áudio — a LG
confirma que [não há API pública de legendas](https://forum.webostv.developer.lge.com/t/subtitles-captions-not-working-on-newer-lg-tv/2176/6),
e o menu é desenhado pelo próprio app, que só responde a botões. A saída é gravar o
caminho uma vez:

1. Deixe algo **tocando** no Max (ou Disney+), sem menus abertos.
2. No app, toque em **⋯ (Ajustes)** → deixe "Legenda/áudio" selecionado → **Gravar sequência**.
3. Volte à aba Controle e navegue pela área de deslize até trocar a legenda, como sempre faz.
4. Ajustes → **Parar e salvar** → dê um nome (ex.: "Dublado").

Repita para a outra combinação ("Legendado"). Os dois viram botões na aba Controle,
mostrados só quando aquele app está em foco na TV.

**Por que dois botões e não um que alterna:** para alternar, o app precisaria saber o
estado atual — e isso não dá para ler do Max. Bastaria você mexer no controle físico uma
vez para ele dessincronizar e passar a fazer o oposto do que promete. Dois botões nomeados
dão o mesmo um-toque, sempre previsíveis.

Os presets ficam em `config/presets.yaml`. Esse arquivo é **reescrito pelo gravador** —
não coloque comentários nele.

## Calibrar as macros sem editar YAML

As sequências do `apps.yaml` são chutes até você calibrá-las: layout de app de streaming
muda por região e por versão. Dá para ajustar na mão, mas o caminho curto é gravar:

1. Abra o app na TV, na tela onde a sequência começa.
2. No celular: **⋯ (Ajustes)** → escolha o que gravar:
   - **Tela "Quem está assistindo?"** — como confirmar o seu perfil;
   - **Caminho até a busca** — informe também o título que você vai digitar, para ele
     virar `{title}` e a macro servir a qualquer outra série;
   - **Caminho até o episódio**.
3. **Gravar sequência** → volte à aba Controle e navegue como sempre faz.
4. **Parar e salvar**.

A macro vai direto para o `config/apps.yaml`, **com os comentários preservados** e uma
cópia `.bak` ao lado. Os tempos gravados são os reais da sua TV, não estimativas.

---

## Escolher o episódio

Na busca, séries ganham um botão **Episódios**: escolha a temporada e o episódio pela
miniatura. Para o app realmente *tocar* o episódio, é preciso calibrar a macro `episode`
do serviço em `config/apps.yaml` — grave a navegação até o **episódio 1** e o
`{episode_index}` faz o deslocamento:

```yaml
episode:
  - {button: DOWN, times: 2}                    # até a faixa de episódios
  - {button: RIGHT, times: "{episode_index}"}   # até o episódio pedido
  - {button: ENTER}
```

Sem essa macro, o app abre a série e para ali — o mesmo comportamento de antes, e o PWA
avisa disso antes de você escolher. Episódios distantes (o 20 de uma temporada) erram mais,
porque a grade rola na tela.

## A tela de perfil ("Quem está assistindo?")

Max e Disney+ mostram o seletor de perfil **toda vez que o app abre do zero** — a ajuda do
Max diz que [em TV isso é por design para perfis adultos](https://help.hbomax.com/gy-en/answer/detail/000002539),
e não há como desligar em smart TV. Sem tratar isso, a macro de busca digitaria no seletor
e nada aconteceria.

O `apps.yaml` já traz um `profile` para Max e Disney+ que confirma o perfil destacado (o
último usado):

```yaml
profile:
  - {button: ENTER}
wait_before_profile: 8
```

Ele **só roda em abertura fria**. Com o app já em primeiro plano o seletor não aparece, e o
app detecta isso e pula — apertar ENTER ali abriria o que estivesse em foco na home.

Se o seu perfil não for o que vem destacado, acrescente os deslocamentos antes do ENTER:

```yaml
profile:
  - {button: RIGHT, times: 2}
  - {button: ENTER}
```

Para calibrar, force a abertura fria (senão o app já aberto pula a tela e o teste não
reproduz o caso real):

```powershell
uv run lgremote try-title "The Last of Us" --service max --cold
```

## Calibrar a busca dentro dos apps

Esta é a única parte que exige trabalho manual, e vale entender por quê.

O webOS aceita abrir um app direto num título (`params.contentTarget`), mas **o formato desse
parâmetro é definido por cada app e não é documentado** para Max/Disney+. Então a base do
projeto é uma **macro**: abrir o app, esperar carregar, navegar até a lupa e digitar o título
pelo teclado da TV. É o que uma pessoa faria — em um toque.

As macros do `config/apps.yaml` são **chutes iniciais**. Ajustar na primeira vez é esperado:

```powershell
uv run lgremote try-title "The Last of Us" --service max
```

Cada passo é impresso enquanto acontece. Olhe a TV, veja onde a sequência erra e ajuste o
bloco `search` daquele serviço:

```yaml
search:
  - {wait: 3}                        # deu tempo do app abrir?
  - {button: LEFT, times: 4}         # chegou no menu lateral?
  - {button: UP, times: 3}           # chegou na lupa?
  - {button: ENTER}                  # abriu a busca?
  - {wait: 2.5}
  - {text: "{title}"}                # digitou no campo certo?
```

Repita até funcionar. Depois disso não mexe mais — só quando o app mudar de layout.

Se você **descobrir** um deep link válido para algum app, preencha `content_target` e ele passa
a ser usado no lugar da macro (instantâneo, sem navegação):

```yaml
content_target: "https://exemplo/titulo/{tmdb_id}"
```

### Passos disponíveis

| Passo | O que faz |
|---|---|
| `{button: UP, times: 3, delay: 0.2}` | aperta um botão N vezes |
| `{wait: 1.5}` | espera segundos |
| `{text: "{title}"}` | digita via IME; `{title}`, `{year}` e `{tmdb_id}` são substituídos |
| `{enter: true}` | tecla Enter do teclado (≠ botão ENTER do direcional) |
| `{clear: 40}` | apaga N caracteres |

---

## Comandos

| Comando | Para quê |
|---|---|
| `lgremote setup` | assistente: acha a TV, pareia, lê os apps e escreve o `.env` |
| `lgremote pair [--host IP]` | parear e gravar a chave no `.env` |
| `lgremote apps` | listar os apps instalados na TV |
| `lgremote discover [--dry-run]` | casar o `apps.yaml` com os apps reais |
| `lgremote try-title "X" --service max` | rodar a macro passo a passo para calibrar |
| &nbsp;&nbsp;`… --cold` | fecha o app antes, para testar a tela de perfil |
| &nbsp;&nbsp;`… --season 1 --episode 4` | testa também a macro de episódio |
| `lgremote serve [--port N]` | subir o servidor e o PWA |
| `lgremote autostart --install` | subir o controle junto com o Windows (`--remove` desfaz) |
| `lgremote doctor` | diagnóstico: o que está certo, o que falta e o que fazer |

---

## Segurança

O servidor escuta na rede local inteira, então **o PIN não é opcional**: sem `UI_PIN` no
`.env` ele recusa todos os comandos. Os nomes de botão passam por uma whitelist — o que chega
pela rede não vira comando arbitrário na TV.

Não há HTTPS: para uma rede doméstica é aceitável, e é justamente o que torna o PIN necessário.

---

## Verificar

Sem a TV (roda em qualquer máquina):

```bash
uv run pytest        # 303 testes, com uma TV falsa que grava os comandos
uv run ruff check .
uv run mypy
```

Com a TV:

1. `lgremote pair` → a TV mostra o pedido de autorização
2. `lgremote discover` → Max, Disney+ e Netflix aparecem com os IDs reais
3. no PWA: deslizar na área de navegação move o foco e o volume muda na tela
4. digitar no campo "Digitar na TV" escreve num campo de busca aberto na TV
5. `lgremote try-title "The Last of Us" --service max` → o Max abre com a busca preenchida

---

## Quando algo não funciona

Antes de qualquer coisa: **`uv run lgremote doctor`**. Ele checa o `.env`, se a TV responde,
se a chave de pareamento ainda vale, se o servidor já está no ar e se o início automático
está instalado — e termina dizendo o que resolver.

| Sintoma | Causa provável |
|---|---|
| "Não consegui falar com a TV" | TV desligada. Se foi só o IP que mudou, o controle se corrige sozinho |
| `403 User denied access` sem nada na tela da TV | TV em standby, ou uma recusa antiga gravada nela: Configurações → Geral → Dispositivos → apague o histórico de conexões |
| Pareamento não aparece na TV | TV em rede diferente (Wi-Fi vs cabo), ou firewall bloqueando 3000/3001 |
| Celular não abre a página | firewall do Windows; e o celular precisa estar no mesmo Wi-Fi |
| App abre mas não busca nada | macro precisa de calibração → grave em **⋯ Ajustes**, ou use `try-title` |
| Macro funciona "às vezes" | `wait_after_launch` curto demais: o app ainda não estava pronto |
| Para no seletor de perfil | aumente `wait_before_profile` (nunca menor que `wait_after_launch`), ou grave a macro `profile` em **⋯ Ajustes** |
| Entra no perfil errado | acrescente `{button: RIGHT}` antes do ENTER em `profile` |
| Botão de ligar não faz nada | falta `TV_MAC`, ou "Mobile TV On" desligado na TV |
| `DLL load failed ... _ctypes` | Smart App Control bloqueou o Python do uv — veja acima |
| `Acesso negado` ao instalar, ou `.venv\Scripts\lgremote.exe ... em uso` | o controle já estava no ar (início automático). O `setup.bat` para o servidor sozinho antes de atualizar; se rodar o `uv sync` na mão, pare antes com `Stop-Process -Name lgremote -Force` |
| `ModuleNotFoundError: No module named 'lgremote'` | `.venv` pela metade, sobra de uma instalação interrompida. O `setup.bat` detecta e recria; na mão: `Remove-Item -Recurse -Force .venv` e `uv sync --extra dev` |
| Preset não aparece | ele só aparece quando aquele app está em foco na TV |
| Preset erra a legenda | o título tem mais faixas de áudio que o gravado — regrave |
| Não dá para gravar | abra o app na TV primeiro: é ele que define de quem é o preset |
| Episódio abre o errado | a grade rolou; regrave a macro `episode` com a série em cima |
| Busca não retorna nada | `TMDB_TOKEN` ausente ou recusado — rode `lgremote setup` para testar |

---

## Estrutura

```
setup.bat / setup.ps1     ← instalação em um clique (Windows)
serve.bat / serve.ps1     ← ligar o controle no dia a dia
config/apps.yaml          ← o arquivo que você edita (serviços + macros)
config/presets.yaml       ← escrito pelo gravador; não edite à mão
src/lgremote/
  setup_wizard.py         o assistente de configuração
  autostart.py            iniciar junto com o Windows
  tv/discovery.py         acha a TV varrendo a porta SSAP na rede
  tv/session.py           conexão SSAP persistente, com reconexão e IP novo
  tv/pairing.py           registro na TV, com prazo humano e diagnóstico do "não"
  tv/macros.py            interpretador dos passos do YAML
  tv/recorder.py          grava seus toques e escreve a macro no apps.yaml
  tv/presets.py           presets de legenda/áudio por serviço
  tv/opener.py            camadas: deep link → macro → episódio
  tv/apps.py              provedor TMDb ↔ app da TV; descoberta
  tv/buttons.py           whitelist de botões
  tv/wol.py               Wake-on-LAN
  catalog/tmdb.py         busca, disponibilidade BR, temporadas/episódios
  api/                    FastAPI + executor de ação longa
  web/                    o PWA (HTML/CSS/JS, sem build)
```

Dados de disponibilidade por **JustWatch**, via TMDb. Este produto usa a API do TMDb, mas não
é endossado nem certificado pelo TMDb.

---

## App de iPhone (substitui este servidor)

O controle agora existe como **app nativo de iPhone**, que fala direto com a TV — sem
PC ligado, sem servidor na rede e sem PIN. É o que resolve, na raiz, as três formas de
"perder o pareamento" que este PWA tinha.

Veja **[`app/README.md`](app/README.md)**: como desenvolver no Windows, como gerar o
`.ipa` pelo GitHub Actions e como instalar pelo SideStore.

Este projeto Python continua funcionando e é a referência de comportamento — os 164
testes do app foram portados dos daqui.
