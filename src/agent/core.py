from langgraph.graph import (
    StateGraph,
    START,
    END
)

from agent.state import State
from agent.nodes import (
    read_messages,
    generate_response,
    send_message,
    should_finish,
    generate_summary
)


class Agent:

    def __init__(self):

        graph = StateGraph(State)

        graph.add_node(
            "read_messages",
            read_messages
        )

        graph.add_node(
            "generate_response",
            generate_response
        )

        graph.add_node(
            "send_message",
            send_message
        )

        graph.add_node(
            "summary",
            generate_summary
        )

        graph.add_edge(
            START,
            "read_messages"
        )

        graph.add_conditional_edges(
            "read_messages",
            should_finish,
            {
                "read_messages": "generate_response",
                "summary": "summary",
            },
        )
        # NOTA: reaproveitamos should_finish aqui -- ele retorna
        # "read_messages" (=> segue o fluxo normal, vai para
        # generate_response) ou "summary" (=> encerra sem gerar nem
        # enviar mais nenhuma resposta). Isso é necessário porque a
        # salvaguarda de fim de conversa em read_messages (detectar que
        # o bot reiniciou o fluxo) precisa interromper o ciclo ANTES de
        # generate_response/send_message rodarem de novo -- caso
        # contrário o agente ainda gera e envia uma resposta a mais
        # para o bot antes do teste realmente parar.

        graph.add_edge(
            "generate_response",
            "send_message"
        )

        graph.add_conditional_edges(
            "send_message",
            should_finish,
            {
                "read_messages": "read_messages",
                "summary": "summary",
            },
        )

        graph.add_edge(
            "summary",
            END
        )

        self.workflow = graph.compile()
        graph_png = self.workflow.get_graph().draw_mermaid_png()

        with open("graph.png", "wb") as f:
            f.write(graph_png)