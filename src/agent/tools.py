import json
import os
from typing import List, Optional

from agent.state import Task
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


def _resolver_dado(task: Task | None, campo: str) -> str:
    """
    Resolve o valor para um dado cadastral.
    Se a tarefa possuir um valor explícito, usa-o.
    Caso contrário gera um dado fake.
    """

    if task and task.get("field") == campo and task.get("value"):
        return task["value"]

    gerador = GERADORES_DADOS.get(campo)

    if gerador:
        return gerador()

    return "Ok"

def _parse_llm_response(texto: str) -> dict:
    """
    Remove markdown e converte a resposta do LLM em JSON.
    """

    bruto = texto.strip()

    if bruto.startswith("```"):
        bruto = bruto.strip("`")

        if bruto.lower().startswith("json"):
            bruto = bruto[4:]

        bruto = bruto.strip()

    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        return {
            "tipo": "texto_livre",
            "resposta": bruto,
        }


def _build_prompt(
    historico: str,
    mensagem_atual: str,
    task: Task | None,
    opcoes: list[str],
) -> str:

    instrucao = task["instruction"] if task else "Nenhuma tarefa."

    opcoes_txt = (
        json.dumps(opcoes, ensure_ascii=False)
        if opcoes
        else "Nenhuma opção."
    )

    return f"""
Você está simulando um usuário durante o teste de um chatbot.

Histórico:

{historico}

Mensagem atual do bot:

{mensagem_atual}

Tarefa atual:

{instrucao}

Opções disponíveis:

{opcoes_txt}

Responda SOMENTE com JSON:

{{
  "tipo":"opcao|dado_cadastral|texto_livre|aguardar|fim",
  "campo":"cpf|telefone|email|empresa|endereco|nome|null",
  "opcao_escolhida":null,
  "resposta":null
}}
"""

def decidir_resposta(
    messages,
    mensagem_atual,
    task: Task | None,
    opcoes: Optional[List[str]] = None,
) -> dict:
    """
    Decide a próxima resposta do usuário utilizando apenas a tarefa atual
    e o histórico da conversa.
    """

    opcoes = opcoes or []

    # Tarefas estruturadas possuem precedência sobre o LLM. O modelo só é
    # usado quando o roteiro não determinou uma resposta exata.
    if task and task.get("type") == "restart":
        # Sinaliza ao nó de envio que a mensagem final do cenário atual já
        # foi recebida e que o próximo cenário deve começar.
        return {"tipo": "fim", "valor": None}

    if task and task.get("type") in {"text", "option", "data"} and task.get("value"):
        valor = task["value"]
        if task["type"] == "option" and opcoes:
            valor = _validar_opcao(valor, opcoes)
        return {
            "tipo": "opcao" if task["type"] == "option" and opcoes else "texto",
            "valor": valor,
        }

    historico = _montar_historico(messages)

    prompt = _build_prompt(
        historico=historico,
        mensagem_atual=mensagem_atual,
        task=task,
        opcoes=opcoes,
    )

    resposta = llm.invoke(prompt)

    decisao = _parse_llm_response(resposta.content)

    tipo = decisao.get("tipo")

    #
    # Há opções na tela -> sempre clicar em uma delas
    #
    if opcoes:
        candidato = (
            decisao.get("opcao_escolhida")
            or decisao.get("resposta")
            or ""
        ).strip()

        return {
            "tipo": "opcao",
            "valor": _validar_opcao(candidato, opcoes),
        }

    #
    # Pedido de dado cadastral
    #
    if tipo == "dado_cadastral":

        campo = decisao.get("campo")

        if not campo:
            return {
                "tipo": "texto",
                "valor": "Ok",
            }

        return {
            "tipo": "texto",
            "valor": _resolver_dado(task, campo),
        }

    #
    # Apenas aguardar
    #
    if tipo == "aguardar":
        return {
            "tipo": "aguardar",
            "valor": None,
        }

    #
    # Fluxo encerrado
    #
    if tipo == "fim":
        return {
            "tipo": "fim",
            "valor": None,
        }

    #
    # Texto livre
    #
    return {
        "tipo": "texto",
        "valor": decisao.get("resposta") or "Ok",
    }

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
