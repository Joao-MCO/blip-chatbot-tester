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

        graph.add_edge(
            "read_messages",
            "generate_response"
        )

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