# blip-chatbot-tester

Agente autônomo que conversa com um chatbot do [Blip](https://blip.ai) através
do navegador (Selenium), usando um LLM para decidir as respostas em tempo
real, com o objetivo de **testar o fluxo de atendimento de ponta a ponta**
sem intervenção manual.

Em vez de um script de teste com passos fixos, o agente lê cada mensagem que
o bot envia (incluindo textos livres e menus de opção), decide como um
usuário real responderia — opcionalmente seguindo um roteiro de teste que
você escreve em texto simples — e ao final gera um resumo da conversa ou,
se você fornecer o design esperado do fluxo, um comparativo apontando
divergências entre o que foi planejado e o que o bot realmente fez.

## Como funciona

```
┌────────────────┐     ┌───────────────────┐     ┌──────────────┐
│  read_messages  │ ──▶ │ generate_response │ ──▶ │ send_message │
└────────────────┘     └───────────────────┘     └──────────────┘
        ▲                                                │
        │                                                ▼
        └──────────────── should_finish? ─────── (loop até "fim")
                                │
                                ▼
                        ┌────────────────┐
                        │    summary     │
                        └────────────────┘
```

1. **`read_messages`** — lê a página via Selenium, extrai a última mensagem
   do bot e, se houver, as opções de menu associadas a ela.
2. **`generate_response`** — envia o histórico completo da conversa para o
   LLM, que decide a próxima ação: clicar numa opção, preencher um dado
   cadastral, responder em texto livre, aguardar, ou encerrar o teste.
3. **`send_message`** — executa a ação decidida (clique real no botão do
   menu ou digitação no campo de texto).
4. O ciclo se repete até o LLM (ou uma das salvaguardas de segurança
   descritas abaixo) sinalizar o fim do teste.
5. **`summary`** — gera o resultado final: um resumo da conversa, ou um
   comparativo com o design esperado (ver seção [Comparativo com o
   design](#comparativo-com-o-design-modelopdf--modelopng--modelojpg)).

## Instalação

**Pré-requisitos:** Python 3.11+, Google Chrome instalado, uma chave de API
da OpenAI.

```bash
git clone https://github.com/Joao-MCO/blip-chatbot-tester
cd blip-chatbot-tester

pip install -r requirements.txt
```

Crie um arquivo `.env` na raiz do projeto com sua chave de API:

```
OPENAI_API_KEY=sk-...
```

## Uso básico

Configure a URL do chatbot que deseja testar em `src/main.py` (constante
`URL`), depois rode:

```bash
python src/main.py
```

O agente abre o Chrome, envia uma mensagem inicial identificando o teste
(`ChatBot Tester - DD/MM/AAAA`), conversa com o bot sozinho até o fluxo
terminar, e imprime o resultado no console.

## Roteiro de teste (`instrucoes.txt`)

Por padrão, o agente se comporta como um usuário genérico: responde de
forma coerente ao que o bot pergunta, mas sem seguir um objetivo específico.

Para guiar o teste — testar um cenário específico, preencher dados
determinados, forçar um caminho do fluxo — crie um arquivo `instrucoes.txt`
na raiz do projeto com o roteiro em texto livre. Um exemplo comentado está
em `instrucoes.example.txt`:

```bash
cp instrucoes.example.txt instrucoes.txt
# edite instrucoes.txt com o roteiro desejado
```

O LLM lê esse roteiro a cada resposta que gera durante o teste — não é um
script rígido de comandos, é contexto que orienta as decisões do modelo.
Se o arquivo não existir ou estiver vazio, o comportamento padrão
(genérico) é usado.

**Exemplo:**

```
Teste o fluxo de abertura de chamado de suporte técnico.
Use o telefone 5535998768686 quando solicitado.
Quando o bot pedir para descrever o problema, diga que "o sistema
trava ao gerar relatórios em PDF".
Se perguntarem se pode ajudar em algo mais, diga que não.
```

Se o roteiro especificar um valor exato para um dado cadastral (CPF,
telefone, e-mail, nome, empresa, endereço), esse valor literal é usado no
lugar do dado fictício gerado automaticamente pelo `Faker`.

## Comparativo com o design (`modelo.pdf` / `modelo.png` / `modelo.jpg`)

Por padrão, o resultado final é um resumo em texto da conversa. Se você
colocar um arquivo chamado exatamente `modelo` (extensão `.pdf`, `.png`,
`.jpg` ou `.jpeg`) na raiz do projeto, esse resumo é substituído por um
**comparativo entre o design esperado e o que o chatbot realmente
respondeu**.

O arquivo `modelo` deve conter o design/fluxo esperado do chatbot — por
exemplo, uma tela exportada do Figma mostrando as mensagens previstas em
cada etapa. O LLM multimodal lê essa imagem ou PDF diretamente (sem
nenhuma extração de texto intermediária) e compara com o texto real
capturado pelo Selenium durante o teste.

```bash
# copie o arquivo exportado do Figma para a raiz do projeto, renomeando:
cp ~/Downloads/MeuFluxo.pdf ./modelo.pdf
```

O comparativo retornado cobre:

1. Mensagens que batem com o design
2. Divergências de texto (design vs. real, lado a lado)
3. Mensagens do design que não apareceram na conversa real
4. Mensagens da conversa real que não estão no design
5. Conclusão geral sobre a fidelidade do fluxo testado

**Observações:**
- Sem o arquivo `modelo`, o comportamento é o resumo padrão — não é
  obrigatório usar essa funcionalidade.
- PDFs com várias páginas: até 5 páginas são convertidas em imagem e
  enviadas (ajustável via `PDF_RENDER_ZOOM` e `MAX_PAGINAS_PDF` em
  `src/services/modelo_loader.py`).
- Se o PDF/imagem contiver o fluxo inteiro do chatbot em um único artboard
  (comum em exports do Figma com várias telas lado a lado), a comparação
  ainda funciona, mas fica mais precisa se o `modelo` corresponder à etapa
  específica que o `instrucoes.txt` está exercitando, em vez do storyboard
  completo.
- Se o arquivo existir mas não puder ser lido (ex: PDF corrompido), o
  agente registra um aviso no console e usa o resumo padrão em vez de
  falhar o teste.

## Salvaguardas contra loops e falhas

Testar um chatbot real por automação tem instabilidades inerentes (bots que
reiniciam o fluxo sozinhos após uma despedida, elementos que demoram a
renderizar, quedas de conexão do navegador). O agente tem algumas proteções
para não ficar preso indefinidamente:

| Situação | Comportamento |
|---|---|
| O bot encerra o atendimento (despedida, protocolo etc.) | O LLM classifica como fim de conversa e o teste encerra |
| O bot repete uma mensagem já vista antes no histórico | Sinal de que o fluxo reiniciou sozinho — o teste encerra mesmo que o LLM não tenha identificado a despedida anterior |
| O bot repete a mesma mensagem 3 vezes seguidas | Encerra e reporta como possível travamento do fluxo |
| O agente fica "aguardando" resposta do bot 5 vezes seguidas | Encerra e reporta como possível travamento do lado do bot |
| Conversa passa de 25 turnos | Encerra por limite de segurança |
| Falha de conexão com o Chrome/ChromeDriver | Encerra de forma controlada, preservando o resumo do que já foi coletado |

## Diagnóstico

A cada leitura de mensagens, o projeto salva o HTML completo da página em
`src/debug_html/` (criado automaticamente), junto com a mensagem e as
opções de menu que foram extraídas daquele estado. Use esses arquivos para
investigar qualquer discrepância entre o que o bot mostrou na tela e o que
o agente entendeu.

## Estrutura do projeto

```
blip-chatbot-tester/
├── src/
│   ├── main.py                  # ponto de entrada
│   ├── agent/
│   │   ├── core.py              # definição do grafo (LangGraph)
│   │   ├── nodes.py             # lógica de cada etapa do grafo
│   │   ├── state.py             # estado compartilhado entre os nós
│   │   ├── tools.py             # decisão de resposta via LLM
│   │   └── llm.py               # configuração do modelo
│   └── services/
│       ├── scrapping.py         # camada de Selenium (leitura/envio de mensagens)
│       ├── fake_data.py         # geração de dados fictícios (Faker)
│       └── modelo_loader.py     # carregamento do arquivo modelo.*
├── instrucoes.txt                # roteiro de teste (opcional, não versionado)
├── instrucoes.example.txt        # exemplo comentado de roteiro
├── modelo.pdf / .png / .jpg       # design esperado (opcional, não versionado)
└── requirements.txt
```

## Licença

Distribuído sob a licença GPL-2.0. Veja [`LICENSE`](./LICENSE) para mais
detalhes.