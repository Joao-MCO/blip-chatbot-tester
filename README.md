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

## Comportamento em caso de falha técnica

Se a conexão com o navegador (Selenium/ChromeDriver) falhar durante o teste
(ex: o processo do Chrome travar), o agente encerra o teste de forma
controlada e gera um resumo indicando que houve uma falha técnica, em vez de
simplesmente quebrar sem salvar nada.
