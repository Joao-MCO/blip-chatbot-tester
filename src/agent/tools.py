import json
import os
from typing import List, Optional

from services.fake_data import (
    gerar_nome,
    gerar_email,
    gerar_telefone,
    gerar_cpf,
    gerar_empresa,
    gerar_endereco,
)
from agent.llm import llm


GERADORES_DADOS = {
    "cpf": gerar_cpf,
    "telefone": gerar_telefone,
    "email": gerar_email,
    "empresa": gerar_empresa,
    "endereco": gerar_endereco,
    "nome": gerar_nome,
}

# Caminho do arquivo de instruções/roteiro de teste. O tester (a pessoa
# rodando o projeto) pode editar esse arquivo de texto simples para
# guiar o comportamento do agente durante o teste -- por exemplo:
# "Teste o fluxo de cancelamento de pedido. Ao ser perguntado o motivo,
# diga que o produto chegou danificado." Sem esse arquivo (ou vazio), o
# agente se comporta como um usuário genérico, como antes.
INSTRUCOES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "instrucoes.txt",
)


def carregar_instrucoes() -> str:
    """
    Lê o roteiro/objetivo de teste do arquivo instrucoes.txt na raiz do
    projeto, se existir. Retorna string vazia se o arquivo não existir
    ou estiver vazio -- nesse caso o agente segue com o comportamento
    padrão (usuário genérico testando o fluxo sem objetivo específico).
    """
    try:
        with open(INSTRUCOES_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[tools] falha ao ler instrucoes.txt: {e}")
        return ""


def _montar_historico(messages: List[str]) -> str:
    # limita a quantidade de histórico enviado ao LLM para não estourar
    # contexto/custo em conversas muito longas, mantendo o suficiente
    # para o modelo entender o assunto e o estado atual do fluxo
    return "\n".join(messages[-30:])


def decidir_resposta(
    messages: List[str],
    mensagem_atual: str,
    opcoes: Optional[List[str]] = None,
) -> dict:
    """
    Usa o LLM lendo TODO o histórico da conversa (não só a última
    mensagem isolada) para decidir como responder ao bot. Isso é
    necessário porque uma mesma mensagem do bot pode significar coisas
    diferentes dependendo do contexto -- por exemplo "E você?" só faz
    sentido interpretado junto com a pergunta anterior, e uma mensagem
    contendo a palavra "nome" nem sempre é literalmente um pedido de
    nome cadastral.

    O LLM decide entre 5 tipos de ação:
      - "opcao": clicar em uma das opções de menu oferecidas
      - "dado_cadastral": preencher com um dado fake (cpf, nome, email,
        telefone, empresa, endereco) -- usamos geração determinística
        (Faker) para o VALOR, mas é o LLM quem decide QUAL campo pedir
      - "texto_livre": responder normalmente, como um usuário faria
      - "aguardar": a mensagem do bot não espera resposta (ex: um aviso,
        despedida sem encerrar, mensagem informativa) -- não envia nada
      - "fim": o bot encerrou o atendimento (despedida, agradecimento
        final, protocolo gerado, etc.) -- o teste deve parar aqui

    Retorna um dict:
      {"tipo": "opcao", "valor": "<texto exato da opção>"}
      {"tipo": "texto", "valor": "<texto a enviar>"}
      {"tipo": "aguardar", "valor": None}
      {"tipo": "fim", "valor": None}
    """
    historico = _montar_historico(messages)
    opcoes = opcoes or []
    instrucoes = carregar_instrucoes()

    campos_disponiveis = list(GERADORES_DADOS.keys())

    bloco_instrucoes = ""
    if instrucoes:
        bloco_instrucoes = f"""
--- ROTEIRO DO TESTE (siga isso como objetivo principal) ---
{instrucoes}
--- FIM DO ROTEIRO ---

IMPORTANTE: Use o roteiro acima para guiar suas respostas em texto
livre e escolhas de opção sempre que ele for aplicável ao ponto atual
da conversa (ex: se o roteiro pede para testar cancelamento, prefira
opções relacionadas a cancelamento quando fizerem sentido, e escreva
respostas em texto livre consistentes com esse objetivo). Se o bot
perguntar algo que o roteiro não cobre, responda de forma razoável como
um usuário real faria, mantendo o objetivo do roteiro em mente sempre
que possível.
"""

    prompt = f"""
Você está testando automaticamente um chatbot, simulando ser um usuário
real. Abaixo está o histórico completo da conversa até agora (BOT =
mensagens do chatbot, USER = mensagens que você mesmo já enviou):

--- HISTÓRICO ---
{historico}
--- FIM DO HISTÓRICO ---
{bloco_instrucoes}
A última mensagem do bot foi:
"{mensagem_atual}"

{"O bot apresentou as seguintes opções de menu (botões clicáveis): " + json.dumps(opcoes, ensure_ascii=False) if opcoes else "O bot NÃO apresentou nenhuma opção de menu -- espera texto livre (ou não espera resposta)."}

Considerando TODO o contexto da conversa (não só a última mensagem
isolada), decida qual ação tomar. Responda SOMENTE com um JSON válido,
sem markdown, sem explicações, no seguinte formato exato:

{{
  "tipo": "opcao" | "dado_cadastral" | "texto_livre" | "aguardar" | "fim",
  "campo": "cpf" | "telefone" | "email" | "empresa" | "endereco" | "nome" | null,
  "valor_roteiro": "<valor EXATO mencionado no ROTEIRO DO TESTE para esse campo, se houver, senão null>",
  "opcao_escolhida": "<texto exato de uma das opções, se tipo=opcao, senão null>",
  "resposta": "<texto da resposta, se tipo=texto_livre, senão null>"
}}

Regras para decidir:
- PRIORIDADE MÁXIMA: se a mensagem do bot claramente ENCERRA o
  atendimento -- despedida final, agradecimento de encerramento
  ("obrigado, volte sempre", "até mais", etc.), geração de protocolo,
  confirmação de que o atendimento foi concluído -- use "tipo": "fim",
  MESMO QUE o roteiro do teste ainda não tenha sido totalmente seguido
  ou pareça "incompleto". Uma vez que o bot se despediu, o teste
  termina ali; NUNCA tente reiniciar o fluxo, repetir uma etapa do
  roteiro, ou enviar uma nova mensagem para "continuar" ou "recomeçar"
  o roteiro depois de uma despedida. Julgue pelo SENTIDO da mensagem
  (está claramente se despedindo/fechando o atendimento?), não por
  bater com uma frase fixa. Se o ROTEIRO DO TESTE definir seu próprio
  critério de conclusão (ex: "pare quando o chamado for registrado com
  sucesso"), considere esse critério também, mas a despedida do bot
  sempre encerra o teste independentemente disso.
- Se há opções de menu, "tipo" DEVE SER "opcao" -- nunca "texto_livre"
  quando há opções, mesmo que a resposta natural pareça só um "sim"
  ou "não" em texto. Se as opções forem, por exemplo, ["Sim", "Não"],
  seu "opcao_escolhida" deve ser exatamente "Sim" ou "Não" (o texto
  exato da opção), nunca uma frase própria como "Não, obrigado." --
  isso não corresponde a nenhum botão real e o clique falhará.
  "opcao_escolhida" deve ser o texto EXATO de uma das opções listadas,
  sem nenhuma palavra a mais ou a menos.
- Se não há opções e o bot está pedindo um dado cadastral específico
  (CPF, telefone, celular, email, empresa, endereço ou nome), use
  "tipo": "dado_cadastral" e informe o campo em "campo" (use exatamente
  um destes valores: {campos_disponiveis}). Se o ROTEIRO DO TESTE acima
  especificar um valor exato para esse campo (ex: "use o telefone
  5535998768686"), coloque esse valor EXATO em "valor_roteiro". Se o
  roteiro não especificar nenhum valor para esse campo, deixe
  "valor_roteiro" como null -- nesse caso um valor fictício será gerado
  automaticamente.
- Se não há opções e o bot fez uma pergunta aberta, uma saudação, ou
  qualquer mensagem que espera uma resposta em texto livre (mesmo que
  contenha a palavra "nome" ou similar sem ser um pedido de cadastro,
  ex: "qual seu nome favorito de filme?"), use "tipo": "texto_livre" e
  escreva uma resposta curta e natural em "resposta", como um usuário
  real responderia, mantendo coerência com o histórico da conversa.
- Se a mensagem do bot é apenas informativa/um aviso e claramente NÃO
  espera nenhuma resposta do usuário para continuar o fluxo (ex: "Só um
  momento, estou verificando...", "Aguarde...") E NÃO é uma despedida
  final, use "tipo": "aguardar".
"""

    resposta = llm.invoke(prompt)
    bruto = resposta.content.strip()

    # remove possíveis cercas de código markdown que o modelo às vezes adiciona
    if bruto.startswith("```"):
        bruto = bruto.strip("`")
        if bruto.lower().startswith("json"):
            bruto = bruto[4:]
        bruto = bruto.strip()

    try:
        decisao = json.loads(bruto)
    except json.JSONDecodeError:
        # fallback de segurança: se o LLM não retornou JSON válido,
        # trata como texto livre usando o próprio texto retornado
        return {"tipo": "texto", "valor": bruto or "Ok"}

    tipo = decisao.get("tipo")

    # SALVAGUARDA ESTRUTURAL: se o bot apresentou opções de menu, a
    # resposta TEM que ser um clique em uma delas -- não importa o que
    # o LLM tenha decidido (mesmo que ele tenha decidido "texto_livre"
    # por engano, ex: respondendo "Não, obrigado." quando a opção real
    # era só "Não"). Isso evita enviar texto solto que não corresponde
    # a nenhum botão real quando há um menu na tela.
    if opcoes:
        candidato = (
            decisao.get("opcao_escolhida")
            or decisao.get("resposta")
            or ""
        ).strip()
        return {"tipo": "opcao", "valor": _validar_opcao(candidato, opcoes)}

    if tipo == "opcao":
        escolha = (decisao.get("opcao_escolhida") or "").strip()
        return {"tipo": "opcao", "valor": _validar_opcao(escolha, opcoes)}

    if tipo == "dado_cadastral":
        campo = decisao.get("campo")
        valor_roteiro = (decisao.get("valor_roteiro") or "").strip()

        if valor_roteiro:
            # o roteiro especificou um valor exato para esse campo
            # (ex: um telefone específico) -- usar esse valor em vez
            # de gerar um dado fictício aleatório
            return {"tipo": "texto", "valor": valor_roteiro}

        gerador = GERADORES_DADOS.get(campo)
        if gerador is not None:
            return {"tipo": "texto", "valor": gerador()}
        # campo desconhecido -> cai para texto livre
        return {"tipo": "texto", "valor": decisao.get("resposta") or "Ok"}

    if tipo == "aguardar":
        return {"tipo": "aguardar", "valor": None}

    if tipo == "fim":
        return {"tipo": "fim", "valor": None}

    # "texto_livre" ou qualquer outro valor inesperado
    return {"tipo": "texto", "valor": decisao.get("resposta") or "Ok"}


def _validar_opcao(escolha: str, opcoes: List[str]) -> str:
    """Garante que a opção escolhida pelo LLM bate com uma real da lista."""
    if not opcoes:
        return escolha

    escolha_normalizada = escolha.strip().lower()

    for opcao in opcoes:
        if opcao.strip().lower() == escolha_normalizada:
            return opcao

    # correspondência parcial: a opção (geralmente curta, ex: "Não")
    # aparece dentro do texto mais longo que o LLM devolveu (ex: "Não,
    # obrigado."). A ordem importa -- checamos se a OPÇÃO está contida
    # na escolha, não o contrário, já que a escolha tende a ser mais
    # longa/elaborada que o texto exato do botão.
    for opcao in opcoes:
        opcao_normalizada = opcao.strip().lower()
        if opcao_normalizada and opcao_normalizada in escolha_normalizada:
            return opcao

    # se o LLM alucinou um texto que não bate com nada, usa a primeira
    # opção como fallback seguro em vez de travar o teste
    return opcoes[0]
