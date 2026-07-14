import re
import unicodedata

from agent.state import Task


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


def _extrair_nome(texto: str) -> str | None:
    padroes = (
        r"(?:coloque|use|informe|diga)\s+(?:como\s+)?nome\s+(.+?)(?:[.!\n]|$)",
        r"nome\s+(?:deve\s+ser|sera)\s+(.+?)(?:[.!\n]|$)",
    )
    for padrao in padroes:
        match = re.search(padrao, texto, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" \"'")
    return None


def _extrair_escolha_comentario(trecho: str) -> str | None:
    normalizado = _normalizar(trecho)
    if re.search(r"(?:diga|dizendo|responda)\s+nao\s+(?:ao|a|sobre o)\s+coment", normalizado):
        return "Não"
    if re.search(r"(?:diga|dizendo|responda)\s+sim\s+(?:ao|a|sobre o)\s+coment", normalizado):
        return "Sim"
    return None


def parse_instructions(text: str) -> list[Task]:
    """Converte um roteiro em linguagem natural em ações executáveis.

    Aceita tanto listas iniciadas por ``-`` quanto parágrafos comuns. Para
    roteiros de avaliação, cada nota forma um cenário independente e uma
    tarefa de reinício é inserida entre os cenários.
    """
    if not text or not text.strip():
        return []

    nome = _extrair_nome(text)
    normalizado = _normalizar(text)
    sempre_nao_ajuda = bool(
        re.search(r"sempre.+?nao.+?(?:precisa|preciso|ajuda)", normalizado, re.DOTALL)
    )

    notas = list(
        re.finditer(r"(?:use|envi(?:e|ando)|d[eê])(?:\s+a\s+op[cç][aã]o\s+de)?\s+(\d)\s+estrelas?", text, re.IGNORECASE)
    )

    # Roteiro estruturado por cenários de nota.
    if notas:
        tasks: list[Task] = []
        for indice, nota in enumerate(notas):
            inicio = nota.end()
            fim = notas[indice + 1].start() if indice + 1 < len(notas) else len(text)
            trecho = text[inicio:fim]

            # O comando de reinício mantém os dados da sessão no chatbot.
            # Depois do primeiro cenário ele volta diretamente à pergunta
            # de continuidade, portanto reenviar o nome deslocaria todo o
            # restante do roteiro em uma etapa.
            if nome and indice == 0:
                tasks.append(_task(f'Informe o nome "{nome}".', "data", nome, field="nome"))
            if sempre_nao_ajuda:
                tasks.append(_task("Diga NÃO quando perguntarem se precisa de mais ajuda.", "option", "Não"))

            quantidade = int(nota.group(1))
            estrelas = "⭐" * quantidade
            tasks.append(_task(f"Envie {quantidade} estrela(s).", "text", estrelas))

            escolha = _extrair_escolha_comentario(trecho)
            if escolha:
                tasks.append(_task(f'Diga "{escolha}" à pergunta sobre comentário.', "option", escolha))

            if escolha == "Sim" and re.search(r"comente|reclama[cç][aã]o", trecho, re.IGNORECASE):
                tasks.append(
                    _task(
                        "Comente uma reclamação genérica sobre um atendimento humano.",
                        "text",
                        "O atendimento humano demorou e não resolveu meu problema.",
                    )
                )

            if indice + 1 < len(notas):
                tasks.append(
                    _task(
                        "Ao receber a mensagem final, reinicie a conversa para o próximo cenário.",
                        "restart",
                    )
                )
        return tasks

    # Compatibilidade com roteiros simples: uma ação por linha/list item.
    tasks = []
    for linha in text.splitlines():
        instruction = linha.strip().lstrip("-").strip()
        if not instruction or instruction.startswith("#"):
            continue

        linha_normalizada = _normalizar(instruction)
        tipo = "text"
        extra = {}
        valor = None

        if "reinici" in linha_normalizada:
            tipo = "restart"
        elif "nome" in linha_normalizada:
            tipo = "data"
            extra["field"] = "nome"
            valor = _extrair_nome(instruction)
        elif re.search(r"\b(sim|nao)\b", linha_normalizada):
            tipo = "option"
            valor = "Não" if re.search(r"\bnao\b", linha_normalizada) else "Sim"

        tasks.append(_task(instruction, tipo, valor, **extra))

    return tasks
