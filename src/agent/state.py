from typing import TypedDict, List
from typing_extensions import Annotated


def _append(left: List[str], right: List[str]) -> List[str]:
    return left + right


class State(TypedDict):
    messages: Annotated[List[str], _append]
    current_message: str
    current_options: List[str]     # opções de menu (se houver) na última msg do bot
    response: str
    response_tipo: str             # "opcao" | "texto" | "aguardar"
    error: bool
    end: bool
    summary: str
    turns: int                     # contador de turnos, evita loop infinito
    repeated_count: int            # quantas vezes a msg do bot se repetiu seguida
    aguardar_count: int            # quantas vezes seguidas o agente ficou "aguardando"
