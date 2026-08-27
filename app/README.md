# Controle LG — app de iPhone

O controle que fala **direto com a TV**. Sem PC ligado, sem servidor na rede, sem PIN.

## Por que isto existe

A versão anterior era um PWA servido por um FastAPI no PC. O PC não estava ali por
capricho: a TV LG expõe o SSAP em `wss://TV:3001` com **certificado autoassinado**, e
nenhum navegador — nem PWA — abre esse WebSocket. Só código nativo abre.

Esse desenho produzia três falhas, e o app corrige as três na raiz:

| Antes | Por quê | Agora |
|---|---|---|
| "Pede o PIN de novo" | O PIN vivia no `localStorage`, que o iOS apaga depois de 7 dias | Não há PIN. Não há servidor na rede para proteger. |
| "Ícone abre em branco" | O atalho congelava `http://<IP-do-PC>:8765`; o DHCP mudava o IP | Não há URL. O app é um app. |
| "A TV recusa a chave" | Só `lgremote pair` no PC resolvia | Tela de pareamento no próprio app, com o diagnóstico do "não" |

A chave que a TV concede fica no **Keychain do iOS**, que sobrevive à reinstalação e à
re-assinatura de 7 dias do SideStore. É isso que faz o pareamento não se perder.

## Como está organizado

```
app/
  src/tv/          protocolo SSAP, macros, catálogo, presets, gravador
  src/catalog/     TMDb
  src/api/         o roteador local — as rotas do servidor antigo, rodando no telefone
  src/storage/     Preferences + Keychain
  src/native/      ponte para o plugin
  plugins/lg-ssap/ o código Swift: socket que aceita o certificado da TV, Keychain,
                   Wake-on-LAN e varredura da rede
  www/             a tela — veio inteira do PWA, só trocou o transporte
  test/            164 testes, incluindo uma TV LG de mentira
```

O `www/app.js` é o mesmo arquivo do PWA. A única mudança foi a função `api()`: onde
havia `fetch` para o PC, agora há uma chamada direta ao roteador no mesmo processo.

## Desenvolver no Windows

Nada aqui precisa de Mac nem de TV.

```bash
npm ci
npm test          # 164 testes contra a TV falsa
npm run typecheck
```

Para mexer na tela, com uma TV de mentira respondendo de verdade:

```bash
npm run faketv    # sobe a TV falsa em ws://127.0.0.1:3010
npm run dev       # em outro terminal
```

Na tela de pareamento digite `127.0.0.1:3010`. Fora da porta 3001 o app usa `ws://`, que
o navegador aceita — é o que torna a tela inteira navegável sem iPhone.

## Gerar o .ipa

O build sai do GitHub Actions (`.github/workflows/ios.yml`), num runner macOS. É a única
máquina Apple envolvida, e ela só compila.

1. `git push` (ou rode o workflow à mão em
   [**Actions → build iOS → Run workflow**](https://github.com/eduardoperorck/lg-remote/actions/workflows/ios.yml))
2. Abra o run que terminou e baixe o artefato **ControleLG-ipa** (fica 30 dias)

O `.ipa` sai **sem assinatura** de propósito: quem assina é o SideStore, no telefone,
com a sua conta gratuita da Apple.

## Instalar pelo SideStore

1. **Uma vez só, num PC:** gere o arquivo de pareamento do SideStore (`SideStore.pairing`)
   seguindo o guia oficial. É o único momento em que um computador é necessário.
2. Instale o SideStore no iPhone e importe o arquivo de pareamento.
3. Abra o `.ipa` no SideStore.
4. O SideStore renova a assinatura sozinho pelo Wi-Fi a cada 7 dias.

Se a renovação atrasar, o app não abre — mas **a chave da TV continua no Keychain**.
Ao reinstalar, não há pareamento novo.

## Primeira execução

1. Ligue a TV e deixe-a na tela inicial, **fora de qualquer app**.
2. Abra o app → **Procurar a TV na rede**.
   - O iOS vai pedir permissão de Rede Local. Sem ela o app não enxerga nada.
3. Toque na TV encontrada. Ela mostra o pedido de autorização; aceite com o controle físico.
4. Nos ajustes, cole o token do TMDb para ligar a busca de títulos
   (themoviedb.org → Settings → API → *API Read Access Token*). O controle funciona sem ele.

Se a TV recusar **sem mostrar nada na tela**, o app diz isso com todas as letras — quase
sempre é standby, histórico de conexões antigo, ou *Mobile TV On* desligado.

## O que ainda não foi verificado numa TV real

O plugin **compila** contra o SDK real da Apple — os seis arquivos Swift passam pelo
`xcodebuild` no runner macOS e saem dentro de um `.ipa` de 3 MB. Isso é mais do que
sintaxe validada, mas ainda não é execução: o plugin **nunca rodou num iPhone**.

A premissa que falta confirmar é a mais importante de todas: se o `URLSession` do iOS
aceita mesmo o certificado autoassinado desta TV. A teoria diz que sim; o primeiro
pareamento é o teste.

Se falhar ali, o erro aparece na tela de pareamento — e o lugar para consertar é
`plugins/lg-ssap/ios/Sources/LgSsapPlugin/TvSocket.swift`, no delegate que decide a
confiança.
