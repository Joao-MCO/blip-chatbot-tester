import json
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

    campos_disponiveis = list(GERADORES_DADOS.keys())

    prompt = f"""
Você está testando automaticamente um chatbot, simulando ser um usuário
real. Abaixo está o histórico completo da conversa até agora (BOT =
mensagens do chatbot, USER = mensagens que você mesmo já enviou):

--- HISTÓRICO ---
{historico}
--- FIM DO HISTÓRICO ---

A última mensagem do bot foi:
"{mensagem_atual}"

{"O bot apresentou as seguintes opções de menu (botões clicáveis): " + json.dumps(opcoes, ensure_ascii=False) if opcoes else "O bot NÃO apresentou nenhuma opção de menu -- espera texto livre (ou não espera resposta)."}

Considerando TODO o contexto da conversa (não só a última mensagem
isolada), decida qual ação tomar. Responda SOMENTE com um JSON válido,
sem markdown, sem explicações, no seguinte formato exato:

{{
  "tipo": "opcao" | "dado_cadastral" | "texto_livre" | "aguardar" | "fim",
  "campo": "cpf" | "telefone" | "email" | "empresa" | "endereco" | "nome" | null,
  "opcao_escolhida": "<texto exato de uma das opções, se tipo=opcao, senão null>",
  "resposta": "<texto da resposta, se tipo=texto_livre, senão null>"
}}

Regras para decidir:
- Se há opções de menu, "tipo" deve ser "opcao", escolhendo a opção mais
  coerente com o contexto da conversa. "opcao_escolhida" deve ser o
  texto EXATO de uma das opções listadas.
- Se não há opções e o bot está pedindo um dado cadastral específico
  (CPF, telefone, celular, email, empresa, endereço ou nome), use
  "tipo": "dado_cadastral" e informe o campo em "campo" (use exatamente
  um destes valores: {campos_disponiveis}).
- Se a mensagem do bot claramente ENCERRA o atendimento -- despedida
  final, agradecimento de encerramento ("obrigado, volte sempre",
  "até mais", etc.), geração de protocolo, confirmação de que o
  atendimento foi concluído -- use "tipo": "fim". Isso vale mesmo que a
  frase exata varie bastante entre atendimentos; julgue pelo SENTIDO da
  mensagem (está claramente se despedindo/fechando o atendimento?), não
  por bater com uma frase fixa.
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

    if tipo == "opcao":
        escolha = (decisao.get("opcao_escolhida") or "").strip()
        return {"tipo": "opcao", "valor": _validar_opcao(escolha, opcoes)}

    if tipo == "dado_cadastral":
        campo = decisao.get("campo")
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

    for opcao in opcoes:
        if opcao.strip().lower() == escolha.strip().lower():
            return opcao

    for opcao in opcoes:
        if escolha.strip().lower() in opcao.strip().lower():
            return opcao

    # se o LLM alucinou um texto que não bate com nada, usa a primeira
    # opção como fallback seguro em vez de travar o teste
    return opcoes[0]
