# blip-chatbot-tester

Agente automatizado que conversa com um chatbot Blip via navegador (Selenium),
usando um LLM para decidir as respostas, com o objetivo de testar o fluxo de
atendimento.

## Como rodar

1. Configure a variável de ambiente `OPENAI_API_KEY` (arquivo `.env` na raiz,
   por exemplo).
2. Instale as dependências: `pip install -r requirements.txt`
3. Rode: `python src/main.py`

## Roteiro de teste (instrucoes.txt)

Por padrão, o agente se comporta como um usuário genérico testando o
atendimento sem objetivo específico -- ele responde de forma coerente ao que
o bot pergunta, mas sem seguir um roteiro definido.

Para guiar o teste com um objetivo específico (ex: "teste o fluxo de
cancelamento", "diga que o produto chegou com defeito quando perguntarem o
motivo"), crie um arquivo `instrucoes.txt` na raiz do projeto (mesmo nível
deste README) com o roteiro em texto livre.

Um exemplo comentado está em `instrucoes.example.txt` -- copie para
`instrucoes.txt` e adapte:

```bash
cp instrucoes.example.txt instrucoes.txt
# edite instrucoes.txt com o roteiro desejado
```

O agente lê esse arquivo a cada resposta que gera durante o teste. Se o
arquivo não existir ou estiver vazio, o comportamento padrão (genérico) é
usado -- não é obrigatório criá-lo.

**Exemplo de conteúdo:**

```
Teste o fluxo de abertura de chamado de suporte técnico.
Quando o bot pedir para descrever o problema, diga que "o sistema
trava ao gerar relatórios em PDF".
Se perguntarem se pode ajudar em algo mais, diga que não.
```

## Diagnóstico

A cada leitura de mensagens, o projeto salva o HTML completo da página em
`debug_html/` (criado automaticamente), junto com o que foi extraído
(mensagem atual e opções de menu identificadas). Isso é útil para investigar
qualquer discrepância entre o que o bot mostrou e o que o agente entendeu.

## Comparativo com o design (modelo.pdf / modelo.png / modelo.jpg)

Por padrão, ao final do teste o agente gera um resumo em texto da conversa
(pontos 1-3 acima). Se você colocar um arquivo chamado exatamente `modelo`
(com extensão `.pdf`, `.png`, `.jpg` ou `.jpeg`) na raiz do projeto -- mesmo
nível deste README --, esse resumo padrão é substituído por um
**comparativo entre o design esperado e o que o chatbot realmente
respondeu**.

O arquivo `modelo` deve ser o design/fluxo esperado do chatbot (por exemplo,
uma tela exportada do Figma mostrando as mensagens previstas em cada etapa).
O próprio LLM multimodal lê essa imagem/PDF diretamente -- não fazemos
nenhuma extração de texto do arquivo -- e compara com o texto real capturado
pelo Selenium durante o teste.

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

**Observações importantes:**
- Se o arquivo `modelo` não existir, o comportamento é exatamente o mesmo de
  antes (resumo padrão) -- não é obrigatório usar essa funcionalidade.
- Se o `modelo` for um PDF com várias páginas, até 5 páginas são convertidas
  em imagem e enviadas (ver `PDF_RENDER_ZOOM` e `MAX_PAGINAS_PDF` em
  `src/services/modelo_loader.py` para ajustar).
- Se o PDF/imagem contiver o fluxo INTEIRO do chatbot em um único artboard
  grande (comum em exports do Figma), a comparação ainda funciona, mas
  recomenda-se comparar **um fluxo por vez** -- ou seja, usar um `modelo`
  focado na etapa que o roteiro de teste (`instrucoes.txt`) está exercitando,
  em vez de um artboard com o storyboard completo do bot. Isso deixa o
  comparativo mais preciso e fácil de revisar.
- Se o arquivo existir mas não puder ser lido/convertido (ex: PDF corrompido),
  o agente registra um aviso no console e cai de volta para o resumo padrão,
  em vez de falhar o teste.



Se a conexão com o navegador (Selenium/ChromeDriver) falhar durante o teste
(ex: o processo do Chrome travar), o agente encerra o teste de forma
controlada e gera um resumo indicando que houve uma falha técnica, em vez de
simplesmente quebrar sem salvar nada.
