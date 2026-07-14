import traceback

from agent.core import Agent
from agent.tools import carregar_instrucoes
from services.scrapping import browser
from services.tasks import parse_instructions

URL = "https://sharkdev.chat.blip.ai/?appKey=cGxheWdyb3VuZGpvYW9tYXJyb2NvczpkMWFlYzZiZS02ZWMwLTQ4N2EtYjcwMi0wYzE4NzVhN2VjOWI=&_gl=1*113ay37*_gcl_au*MTk1MjUzMDM3Ny4xNzgzMzQyNzIx*_ga*MTYzNDg5OTAyLjE3NzU0Nzc4MzQ.*_ga_8GVWK8YMGL*czE3ODM4MjkwNTEkbzIwMSRnMSR0MTc4MzgyOTYxMiRqNTkkbDAkaDY1Njc5NTIwMw.."


def main():
    url = input("Insira a URL do Bot: ") if(not URL) else URL
    instrucoes = carregar_instrucoes()
    agent = Agent()

    estado = {
        "messages": [],
        "current_message": "",
        "current_options": [],
        "bot_messages_seen": 0,
        "conversation_origin": f"Conversa capturada via Selenium na URL: {url}",
        "awaiting_final_reply": False,
        "final_reply_received": False,
        "response": "",
        "response_tipo": "",
        "error": False,
        "end": False,
        "summary": "",
        "turns": 0,
        "repeated_count": 0,
        "aguardar_count": 0,
        "tasks": parse_instructions(instrucoes),
        "current_task": 0
    }

    try:
        browser.newPage(url)

        resultado = agent.workflow.invoke(estado)
        conteudo_resultado = resultado.get("summary", "")
        with open("resultado.txt", "w+", encoding="utf-8") as file:
            file.write(conteudo_resultado)
        conteudo = resultado.get("messages", "")
        with open("conversa.txt", "w+", encoding="utf-8") as file:
            file.write("\n".join(conteudo))

    except Exception as e:
        conteudo_erro = (
            "Falha na execução do teste.\n\n"
            f"Erro: {e}\n\n"
            "Traceback:\n"
            f"{traceback.format_exc()}"
        )
        with open("resultado.txt", "w+", encoding="utf-8") as file:
            file.write(conteudo_erro)
        raise

    finally:
        browser.quit()


if __name__ == "__main__":
    main()
