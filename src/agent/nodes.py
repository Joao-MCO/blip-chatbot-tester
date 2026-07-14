import os
import time
from datetime import datetime

from selenium.common.exceptions import WebDriverException

from services.scrapping import browser

from agent.state import State
from agent.tools import decidir_resposta
from agent.llm import llm

MAX_TURNS = 25
MAX_REPEATED = 3  # se o bot repetir a mesma mensagem N vezes seguidas, aborta
MAX_AGUARDAR = 5  # quantas vezes seguidas aceitamos "aguardar" antes de desistir

DEBUG_DIR = "debug_html"


def _dump_debug_html(turno: int, ultima_msg: str, opcoes: list):
    """
    Salva o HTML completo da página a cada leitura, junto com o que foi
    extraído (mensagem e opções). Isso permite diagnosticar, depois de
    uma falha, exatamente o que o Selenium estava vendo naquele
    instante -- em vez de depender de descrições ou trechos parciais.
    """
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(DEBUG_DIR, f"turno_{turno:03d}_{timestamp}.html")
        with open(path, "w+", encoding="utf-8") as f:
            f.write(f"<!-- current_message: {ultima_msg!r} -->\n")
            f.write(f"<!-- options: {opcoes!r} -->\n")
            f.write(browser.getHtml())
    except Exception as e:
        print(f"[debug] falha ao salvar HTML de diagnóstico: {e}")


def _extrair_ultima_msg_bot(conversa):
    """
    Extrai a última mensagem do BOT (não do usuário) de uma lista de
    mensagens retornada por browser.readMessages(). Ver comentário em
    read_messages sobre por que não podemos usar conversa[-1] direto.
    """
    mensagens_bot = [m for m in conversa if m.get("role") == "bot"]
    if not mensagens_bot:
        return "", []
    ultimo = mensagens_bot[-1]
    return ultimo["content"], ultimo.get("options", [])


# IMPORTANTE -- padrão de retorno dos nós:
#
# Cada nó abaixo retorna sempre um DICT NOVO contendo apenas as chaves
# que de fato mudaram naquela chamada -- nunca mutamos o `state`
# recebido como argumento nem o retornamos diretamente (`return state`).
#
# Mutar e devolver o mesmo objeto `state` que o LangGraph nos passou
# causava duplicação silenciosa no histórico: como "messages" usa um
# reducer que concatena, e o LangGraph mantém referências internas do
# state entre etapas, retornar o mesmo objeto mutado (em vez de um
# dict novo e independente) fazia o merge do reducer ser aplicado de
# forma inconsistente, duplicando o conteúdo de "messages" a cada
# volta do grafo. Retornando sempre um dict novo, isolado, esse
# problema desaparece -- é o padrão recomendado para nós de LangGraph.


def read_messages(state: State) -> dict:

    mensagem_anterior = state.get("current_message", "")

    # Camada extra de segurança: se a primeira leitura vier igual à
    # mensagem anterior (ou seja, aparentemente "nada mudou"), tentamos
    # ler de novo mais 2 vezes com um pequeno intervalo antes de aceitar
    # isso como uma repetição real do bot. Isso cobre casos em que o
    # Selenium capturou o DOM num instante intermediário (ex: bolha
    # ainda vazia, texto sendo preenchido, opções ainda não anexadas) --
    # mesmo com as esperas em waitForNewMessage, timing de UI nunca é
    # 100% determinístico.
    try:
        conversa = browser.readMessages()
        ultima_msg, ultimas_opcoes = _extrair_ultima_msg_bot(conversa)

        tentativas_extra = 0
        while ultima_msg == mensagem_anterior and tentativas_extra < 2:
            time.sleep(1.5)
            conversa = browser.readMessages()
            ultima_msg, ultimas_opcoes = _extrair_ultima_msg_bot(conversa)
            tentativas_extra += 1
    except WebDriverException as e:
        # Falha de conexão com o ChromeDriver/Chrome (ex: processo
        # travou ou morreu, reset de conexão local). Em vez de deixar
        # o traceback cru derrubar o processo inteiro sem salvar nada,
        # encerramos o teste de forma controlada -- o resumo final vai
        # registrar isso como uma falha técnica, não como sucesso.
        print(f"[read_messages] falha de conexão com o navegador: {e}")
        return {"error": True, "end": True, "messages": []}

    mensagem_mudou = ultima_msg != mensagem_anterior

    # detecta se o bot repetiu a mesma mensagem da rodada anterior
    # (indício de que o agente está preso em loop) -- só conta como
    # repetição de verdade depois das tentativas extras acima
    repeated_count = (state.get("repeated_count", 0) + 1) if (ultima_msg and not mensagem_mudou) else 0

    # SALVAGUARDA ESTRUTURAL: se essa mesma mensagem do bot já apareceu
    # ANTES no histórico da conversa (não apenas na rodada imediatamente
    # anterior, mas em qualquer ponto anterior), é sinal de que o bot
    # reiniciou o fluxo do zero -- isso só acontece depois de uma
    # despedida/encerramento, então força o fim do teste aqui, sem
    # depender exclusivamente do LLM classificar corretamente a
    # despedida anterior como "fim" (o LLM pode "esquecer" de encerrar
    # quando ainda há itens do roteiro pendentes, por exemplo).
    historico_bot_anterior = [
        m[len("BOT: "):] for m in state.get("messages", []) if m.startswith("BOT: ")
    ]
    if ultima_msg and ultima_msg in historico_bot_anterior:
        print(
            f"[read_messages] mensagem do bot já vista antes no histórico "
            f"({ultima_msg!r}) -- fluxo reiniciou, encerrando o teste"
        )
        return {
            "current_message": ultima_msg,
            "current_options": ultimas_opcoes,
            "end": True,
            "repeated_count": repeated_count,
            "messages": [],
        }

    # Diagnóstico: salva o HTML completo + o que foi extraído a cada
    # leitura. Útil para investigar casos em que uma mensagem tinha
    # opções de menu mas o agente não as identificou.
    _dump_debug_html(state.get("turns", 0), ultima_msg, ultimas_opcoes)

    print(
        f"[read_messages] turno={state.get('turns', 0)} "
        f"msg={ultima_msg!r} opcoes={ultimas_opcoes!r}"
    )

    # Só registramos a mensagem do bot no histórico se ela for
    # realmente nova. "messages" usa um reducer que sempre concatena
    # (o dict retornado por este nó é somado ao histórico existente,
    # não o substitui) -- sem essa checagem, a mesma mensagem do bot
    # seria duplicada no histórico a cada volta do grafo, mesmo quando
    # ele não disse nada novo (ex: enquanto aguardamos uma resposta).
    #
    # Camada extra: mesmo com "mensagem_mudou" checado acima, garantimos
    # aqui que a entrada a ser adicionada não seja idêntica à última já
    # registrada no histórico -- proteção redundante contra duplicação,
    # já que o histórico só deve crescer quando algo novo é de fato
    # trocado com o bot.
    historico_atual = state.get("messages", [])
    nova_entrada = f"BOT: {ultima_msg}"

    if mensagem_mudou and (not historico_atual or historico_atual[-1] != nova_entrada):
        novas_mensagens = [nova_entrada]
    else:
        novas_mensagens = []

    return {
        "current_message": ultima_msg,
        "current_options": ultimas_opcoes,
        "repeated_count": repeated_count,
        "messages": novas_mensagens,
    }


def generate_response(state: State) -> dict:

    # O LLM recebe o HISTÓRICO COMPLETO da conversa (não só a última
    # mensagem isolada), para entender o contexto corretamente -- por
    # exemplo, "E você?" só faz sentido interpretado junto com a
    # pergunta anterior do próprio usuário simulado. É também o LLM,
    # com esse mesmo contexto, quem decide se a conversa chegou ao fim
    # (tipo "fim") -- ver decidir_resposta em tools.py. Isso substitui
    # uma checagem antiga baseada em palavras-chave fixas (ex:
    # "obrigado pelo contato"), que não cobria a forma real como os
    # bots se despedem (ex: "Muito Obrigado! Volte sempre!").
    resultado = decidir_resposta(
        messages=state.get("messages", []),
        mensagem_atual=state["current_message"],
        opcoes=state.get("current_options") or None,
    )

    response_tipo = resultado["tipo"]
    response = resultado["valor"] or ""

    print(
        f"[generate_response] turno={state.get('turns', 0)} "
        f"msg={response!r} tipo={response_tipo!r}"
    )

    return {
        "response_tipo": response_tipo,
        "response": response,
        "error": False,
    }


def send_message(state: State) -> dict:

    tipo = state.get("response_tipo", "texto")
    opcoes = state.get("current_options") or []
    resposta = state["response"]

    if tipo == "fim":
        # o LLM identificou que o bot encerrou o atendimento (despedida,
        # protocolo, agradecimento final etc). Não enviamos mais nada --
        # só marcamos o fim para should_finish encerrar o teste.
        print(f"[send_message] turno={state.get('turns', 0)} fim de conversa detectado")
        return {"end": True, "messages": []}

    if tipo == "aguardar":
        # o bot mandou algo que não espera resposta do usuário (ex: um
        # aviso "Aguarde..."). Não enviamos nada -- só damos um tempo
        # para o bot continuar o fluxo sozinho e lemos de novo.
        aguardar_count = state.get("aguardar_count", 0) + 1
        time.sleep(2)
        turns = state.get("turns", 0) + 1
        print(f"[send_message] turno={turns} aguardando (sem enviar)")
        return {
            "aguardar_count": aguardar_count,
            "messages": ["USER: (aguardando o bot continuar...)"],
            "turns": turns,
        }

    try:
        enviado_via_clique = False

        if tipo == "opcao" and opcoes:
            enviado_via_clique = browser.selectOption(resposta, wait_response=True)

        if not enviado_via_clique:
            # ou não era uma opção, ou o clique falhou (ex: LLM alucinou um
            # texto que não bate com nenhuma opção) -> cai para texto livre
            browser.sendMessage(resposta, wait_response=True)
    except WebDriverException as e:
        # Mesma lógica de resiliência de read_messages: um erro de
        # conexão com o Chrome/ChromeDriver no meio do envio não deve
        # matar o processo sem deixar rastro -- encerra o teste de
        # forma controlada e registra a falha técnica no resumo.
        print(f"[send_message] falha de conexão com o navegador: {e}")
        return {"error": True, "end": True, "messages": []}

    historico_atual = state.get("messages", [])
    nova_entrada = f"USER: {resposta}"

    if not historico_atual or historico_atual[-1] != nova_entrada:
        novas_mensagens = [nova_entrada]
    else:
        novas_mensagens = []

    turns = state.get("turns", 0) + 1

    print(
        f"[send_message] turno={turns} msg={resposta!r} "
        f"via_clique={enviado_via_clique}"
    )

    return {
        "aguardar_count": 0,
        "messages": novas_mensagens,
        "turns": turns,
    }


def should_finish(state: State):

    if state.get("end"):
        return "summary"

    if state.get("turns", 0) >= MAX_TURNS:
        return "summary"

    if state.get("repeated_count", 0) >= MAX_REPEATED:
        # o bot está travado respondendo a mesma coisa: encerra o teste
        # e reporta isso no resumo como uma falha do fluxo
        return "summary"

    if state.get("aguardar_count", 0) >= MAX_AGUARDAR:
        # o agente ficou "aguardando" repetidamente sem o bot avançar
        # o fluxo -- provável travamento do lado do bot
        return "summary"

    return "read_messages"


def generate_summary(state: State) -> dict:

    conversa = "\n".join(state["messages"])

    motivo_encerramento = "fim natural da conversa"
    if state.get("error"):
        motivo_encerramento = (
            "falha técnica de conexão com o navegador (Selenium/ChromeDriver) "
            "durante o teste -- o teste foi interrompido antes de concluir o "
            "fluxo normalmente"
        )
    elif state.get("end"):
        motivo_encerramento = "o bot encerrou o atendimento normalmente"
    elif state.get("turns", 0) >= MAX_TURNS:
        motivo_encerramento = f"limite de {MAX_TURNS} turnos atingido"
    elif state.get("repeated_count", 0) >= MAX_REPEATED:
        motivo_encerramento = (
            "o bot repetiu a mesma mensagem "
            f"{state['repeated_count']} vezes seguidas (possível loop/trava no fluxo)"
        )
    elif state.get("aguardar_count", 0) >= MAX_AGUARDAR:
        motivo_encerramento = (
            "o agente ficou aguardando o bot continuar o fluxo repetidamente "
            "sem sucesso (possível travamento do lado do bot)"
        )

    prompt = f"""
Analise a conversa abaixo, que é um teste automatizado de um chatbot.

Motivo do encerramento do teste: {motivo_encerramento}

Conversa:
{conversa}

Retorne:

1. Resumo
2. Possíveis falhas
3. Melhorias
"""

    resposta = llm.invoke(prompt)

    return {"summary": resposta.content}
