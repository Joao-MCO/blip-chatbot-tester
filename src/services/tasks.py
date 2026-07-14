import json
import re
import unicodedata

from agent.llm import llm
from agent.state import Task


TASK_TYPES = {"text", "option", "restart", "data"}
DATA_FIELDS = {"cpf", "telefone", "email", "empresa", "endereco", "nome"}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _task(instruction: str, tipo: str, value: str | None = None, **extra) -> Task:
    task: Task = {
        "instruction": instruction.strip(),
        "type": tipo,
        "advance_on_response": True,
    }
    if value is not None:
        task["value"] = value
    task.update(extra)
    return task


def _extrair_json(texto: str) -> dict | None:
    bruto = texto.strip()
    if bruto.startswith("```"):
        bruto = bruto.strip("`").strip()
        if bruto.lower().startswith("json"):
            bruto = bruto[4:].strip()

    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, TypeError):
        return None

    return dados if isinstance(dados, dict) else None


def _normalizar_task(item: dict) -> Task | None:
    if not isinstance(item, dict):
        return None

    instruction = str(item.get("instruction", "")).strip()
    tipo = str(item.get("type", "")).strip().lower()
    value = item.get("value")
    field = item.get("field")

    if not instruction or tipo not in TASK_TYPES:
        return None

    extra = {}
    if tipo == "data":
        field = str(field or "").strip().lower()
        if field not in DATA_FIELDS:
            return None
        extra["field"] = field

    if tipo == "restart":
        value = None
    elif value is not None:
        value = str(value).strip()
        if not value:
            value = None

    return _task(instruction, tipo, value, **extra)


def _interpretar_com_llm(text: str) -> list[Task]:
    prompt = f"""
Voce esta convertendo um roteiro de teste de chatbot em tarefas executaveis.

Roteiro:
{text}

Retorne SOMENTE JSON valido, sem markdown, neste formato:

{{
  "tasks": [
    {{
      "instruction": "acao clara para o usuario simulado",
      "type": "text|option|data|restart",
      "value": "texto exato a enviar ou opcao exata a selecionar, ou null",
      "field": "nome|cpf|telefone|email|empresa|endereco|null"
    }}
  ]
}}

Regras:
- Converta somente acoes que o usuario simulado deve executar.
- Nao inclua mensagens esperadas do bot, criterios de validacao, titulos,
  explicacoes ou observacoes como tarefas.
- Use "data" quando a acao for informar um dado cadastral. Preencha "field".
- Use "option" quando a acao for escolher botao, menu, nota, estrela,
  avaliacao, sim/nao ou alternativa visivel.
- Use "text" quando a acao for digitar uma mensagem livre.
- Use "restart" apenas quando o roteiro pedir reinicio de conversa ou novo
  cenario.
- Se a instrucao disser "sempre", repita a tarefa nas fases em que ela for
  necessaria para cumprir o roteiro, mas nao invente fases.
- Para estrelas, retorne a quantidade como caracteres de estrela em "value".
  Exemplo: 5 estrelas => "⭐⭐⭐⭐⭐".
- Para comentarios genericos, gere um texto curto coerente em "value".
- Preserve acentos, emojis e grafia quando o roteiro trouxer valor literal.
"""

    resposta = llm.invoke(prompt)
    dados = _extrair_json(resposta.content)
    if not dados:
        return []

    tasks = dados.get("tasks", [])
    if not isinstance(tasks, list):
        return []

    normalizadas = []
    for item in tasks:
        task = _normalizar_task(item)
        if task:
            normalizadas.append(task)

    return normalizadas


def _fallback_parse(text: str) -> list[Task]:
    tasks: list[Task] = []
    for linha in text.splitlines():
        instruction = linha.strip().lstrip("-").strip()
        if not instruction or instruction.startswith("#"):
            continue

        normalizada = _normalizar(instruction)
        if "ultima mensagem" in normalizada or "mensagem final" in normalizada:
            continue

        if "reinici" in normalizada:
            tasks.append(_task(instruction, "restart"))
            continue

        nome = re.search(r"(?:nome|como nome)\s+(.+?)(?:[.!\n]|$)", instruction, re.IGNORECASE)
        if nome:
            tasks.append(_task(instruction, "data", nome.group(1).strip(" \"'"), field="nome"))
            continue

        estrelas = re.search(r"(\d)\s+estrelas?", instruction, re.IGNORECASE)
        if estrelas:
            tasks.append(_task(instruction, "option", "⭐" * int(estrelas.group(1))))
            continue

        if re.search(r"\b(sim|n[aã]o)\b", normalizada):
            valor = "Não" if re.search(r"\bnao\b", normalizada) else "Sim"
            tasks.append(_task(instruction, "option", valor))
            continue

        tasks.append(_task(instruction, "text"))

    return tasks


def parse_instructions(text: str) -> list[Task]:
    """Converte um roteiro em linguagem natural em acoes executaveis."""
    if not text or not text.strip():
        return []

    try:
        tasks = _interpretar_com_llm(text)
    except Exception as e:
        print(f"[tasks] falha ao interpretar instrucoes com LLM: {e}")
        tasks = []

    if tasks:
        return tasks

    return _fallback_parse(text)
