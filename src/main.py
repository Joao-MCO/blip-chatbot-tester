from services.scrapping import browser
from agent.core import Agent


URL = "https://sharkdev.chat.blip.ai/?appKey=cGxheWdyb3VuZGpvYW9tYXJyb2NvczpkMWFlYzZiZS02ZWMwLTQ4N2EtYjcwMi0wYzE4NzVhN2VjOWI=&_gl=1*113ay37*_gcl_au*MTk1MjUzMDM3Ny4xNzgzMzQyNzIx*_ga*MTYzNDg5OTAyLjE3NzU0Nzc4MzQ.*_ga_8GVWK8YMGL*czE3ODM4MjkwNTEkbzIwMSRnMSR0MTc4MzgyOTYxMiRqNTkkbDAkaDY1Njc5NTIwMw.."


def main():

    agent = Agent()

    try:
        browser.newPage(URL)

        resultado = agent.workflow.invoke(
            {
                "messages": [],
                "current_message": "",
                "current_options": [],
                "response": "",
                "response_tipo": "",
                "error": False,
                "end": False,
                "summary": "",
                "turns": 0,
                "repeated_count": 0,
                "aguardar_count": 0,
            }
        )

        print(resultado["summary"])

    finally:
        # garante que o Chrome seja fechado mesmo se algo falhar/for
        # interrompido no meio do teste
        browser.quit()


if __name__ == "__main__":
    main()
