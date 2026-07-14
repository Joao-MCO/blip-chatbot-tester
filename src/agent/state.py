from typing import Literal, TypedDict, List
from typing_extensions import Annotated


def _append(left: List[str], right: List[str]) -> List[str]:
    return left + right

class Task(TypedDict, total=False):
    instruction: str
    type: Literal[
        "text",
        "option",
        "restart",
        "data"
    ]

    field: str
    value: str
    advance_on_response: bool
class State(TypedDict):
    messages: Annotated[List[str], _append]
    current_message: str
    current_options: List[str]     # opções de menu (se houver) na última msg do bot
    bot_messages_seen: int         # quantas mensagens do bot já foram registradas
    conversation_origin: str
    response: str
    response_tipo: str             # "opcao" | "texto" | "aguardar"
    error: bool
    end: bool
    summary: str
    turns: int                     # contador de turnos, evita loop infinito
    repeated_count: int            # quantas vezes a msg do bot se repetiu seguida
    aguardar_count: int            # quantas vezes seguidas o agente ficou "aguardando"
    scenario: int
    total_scenarios: int
    tasks: list[Task]
    current_task: int
