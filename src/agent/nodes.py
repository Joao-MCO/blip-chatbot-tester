import json
import os
import re
import time
from datetime import datetime

from selenium.common.exceptions import WebDriverException
from langchain_core.messages import HumanMessage

from services.scrapping import browser
from services.modelo_loader import carregar_imagens_modelo, localizar_arquivo_modelo

from agent.state import State
from agent.tools import carregar_instrucoes, decidir_resposta
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


def _mensagens_bot(conversa):
    return [m for m in conversa if m.get("role") == "bot"]


def _formatar_entrada_bot(mensagem: dict) -> str:
    entrada = f"BOT: {mensagem.get('content', '')}"
    opcoes = mensagem.get("options", []) or []
    if opcoes:
        entrada += "\nOPÇÕES DO BOT: " + json.dumps(opcoes, ensure_ascii=False)
    return entrada


def _padrao_com_placeholders(texto: str) -> str:
    partes = re.split(r"(\{[^{}]+\})", texto)
    regex = []
    for parte in partes:
        if re.fullmatch(r"\{[^{}]+\}", parte or ""):
            regex.append(r".+?")
        else:
            regex.append(re.escape(parte))
    return "^" + "".join(regex) + "$"


def _eh_template_variavel(esperado: str, ocorreu: str) -> bool:
    if "{" not in esperado or "}" not in esperado:
        return False
    padrao = _padrao_com_placeholders(esperado)
    return re.fullmatch(padrao, ocorreu, flags=re.DOTALL) is not None


def _eh_mensagem_agrupada(
    esperado: str,
    ocorreu: str,
    conversa_estruturada: list[dict] | None,
) -> bool:
    if not conversa_estruturada:
        return False

    esperado_normalizado = re.sub(r"\s+", " ", esperado).strip()
    ocorreu_normalizado = re.sub(r"\s+", " ", ocorreu).strip()

    for cenario in conversa_estruturada:
        bots = [m["texto"] for m in cenario.get("mensagens", []) if m.get("role") == "bot"]
        for indice in range(len(bots) - 1):
            combinado = f"{bots[indice]}\n{bots[indice + 1]}"
            combinado_normalizado = re.sub(r"\s+", " ", combinado).strip()
            if combinado_normalizado == esperado_normalizado and bots[indice] == ocorreu_normalizado:
                return True

    return False


def _mensagens_por_cenario(mensagens: list[str]) -> list[dict]:
    cenarios = []
    atual = {"cenario": 1, "mensagens": []}
    ordem_global = 1

    for entrada in mensagens:
        if entrada.startswith("SYSTEM: conversa reiniciada intencionalmente pelo roteiro de teste."):
            if atual["mensagens"]:
                cenarios.append(atual)
                atual = {"cenario": atual["cenario"] + 1, "mensagens": []}
            continue

        if entrada.startswith("BOT: "):
            corpo = entrada[len("BOT: ") :]
            opcoes = []
            if "\nOPÇÕES DO BOT: " in corpo:
                corpo, opcoes_json = corpo.split("\nOPÇÕES DO BOT: ", 1)
                try:
                    opcoes = json.loads(opcoes_json)
                except json.JSONDecodeError:
                    opcoes = [opcoes_json]
            atual["mensagens"].append(
                {
                    "ordem_global": ordem_global,
                    "role": "bot",
                    "texto": corpo,
                    "opcoes": opcoes,
                }
            )
            ordem_global += 1
            continue

        if entrada.startswith("USER: "):
            atual["mensagens"].append(
                {
                    "ordem_global": ordem_global,
                    "role": "user",
                    "texto": entrada[len("USER: ") :],
                }
            )
            ordem_global += 1
            continue

    if atual["mensagens"]:
        cenarios.append(atual)

    for cenario in cenarios:
        cenario["total_mensagens"] = len(cenario["mensagens"])

    return cenarios


def _roteiro_por_cenario(tasks: list[dict]) -> list[dict]:
    cenarios = []
    atual = {"cenario": 1, "tarefas": []}
    ordem_global = 1

    for tarefa in tasks:
        if tarefa.get("type") == "restart":
            atual["tarefas"].append(
                {
                    "ordem_global": ordem_global,
                    "type": tarefa.get("type"),
                    "instruction": tarefa.get("instruction"),
                }
            )
            ordem_global += 1
            cenarios.append(atual)
            atual = {"cenario": atual["cenario"] + 1, "tarefas": []}
            continue

        atual["tarefas"].append(
            {
                "ordem_global": ordem_global,
                "type": tarefa.get("type"),
                "instruction": tarefa.get("instruction"),
                "value": tarefa.get("value"),
                "field": tarefa.get("field"),
            }
        )
        ordem_global += 1

    if atual["tarefas"]:
        cenarios.append(atual)

    for cenario in cenarios:
        cenario["total_tarefas"] = len(cenario["tarefas"])

    return cenarios


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
    bot_seen_anterior = state.get("bot_messages_seen", 0)

    # Guardamos quantas bolhas do bot já foram consumidas porque o DOM
    # pode acumular várias mensagens no mesmo turno. Ler só a última
    # bolha fazia o histórico perder trechos intermediários.
    try:
        conversa = browser.readMessages()
        mensagens_bot = _mensagens_bot(conversa)

        if len(mensagens_bot) < bot_seen_anterior:
            bot_seen_anterior = 0

        novas_mensagens_bot = mensagens_bot[bot_seen_anterior:]
        tentativas_extra = 0

        while not novas_mensagens_bot and mensagem_anterior and tentativas_extra < 2:
            time.sleep(1.5)
            conversa = browser.readMessages()
            mensagens_bot = _mensagens_bot(conversa)

            if len(mensagens_bot) < bot_seen_anterior:
                bot_seen_anterior = 0

            novas_mensagens_bot = mensagens_bot[bot_seen_anterior:]
            tentativas_extra += 1
    except WebDriverException as e:
        # Falha de conexão com o ChromeDriver/Chrome (ex: processo
        # travou ou morreu, reset de conexão local). Em vez de deixar
        # o traceback cru derrubar o processo inteiro sem salvar nada,
        # encerramos o teste de forma controlada -- o resumo final vai
        # registrar isso como uma falha técnica, não como sucesso.
        print(f"[read_messages] falha de conexão com o navegador: {e}")
        return {"error": True, "end": True, "messages": []}

    if mensagens_bot:
        ultima = mensagens_bot[-1]
        ultima_msg = ultima["content"]
        ultimas_opcoes = ultima.get("options", [])
    else:
        ultima_msg = ""
        ultimas_opcoes = []

    mensagem_mudou = bool(novas_mensagens_bot)
    repeated_count = (
        state.get("repeated_count", 0) + 1
        if (ultima_msg and not mensagem_mudou and ultima_msg == mensagem_anterior)
        else 0
    )

    historico_bot_anterior = [
        m[len("BOT: "):] for m in state.get("messages", []) if m.startswith("BOT: ")
    ]

    print("Última:", ultima_msg)
    print("Histórico:", historico_bot_anterior[-5:])

    print(
        f"[read_messages] turno={state.get('turns', 0)} "
        f"msg={ultima_msg!r} opcoes={ultimas_opcoes!r} "
        f"novas={len(novas_mensagens_bot)}"
    )

    historico_atual = state.get("messages", [])
    novas_mensagens = []
    ultima_entrada_registrada = historico_atual[-1] if historico_atual else None

    for mensagem in novas_mensagens_bot:
        nova_entrada = _formatar_entrada_bot(mensagem)
        if nova_entrada != ultima_entrada_registrada:
            novas_mensagens.append(nova_entrada)
            ultima_entrada_registrada = nova_entrada

    return {
        "current_message": ultima_msg,
        "current_options": ultimas_opcoes,
        "bot_messages_seen": bot_seen_anterior + len(novas_mensagens_bot),
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
    task = None

    task = current_task(state)

    resultado = decidir_resposta(
        messages=state["messages"],
        mensagem_atual=state["current_message"],
        task=task,
        opcoes=state["current_options"],
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

        task = current_task(state)

        if task and task.get("type") == "restart":
            browser.restartConversation()

            return {
                "messages": [
                    "SYSTEM: conversa reiniciada intencionalmente pelo roteiro de teste."
                ],
                "turns": 0,
                "current_message": "",
                "current_options": [],
                "repeated_count": 0,
                "aguardar_count": 0,
                "current_task": next_task(state),
            }

        # Uma despedida só conclui o teste quando o roteiro também acabou.
        # Se ainda existe uma ação comum pendente, mantemos o diagnóstico
        # explícito em vez de declarar sucesso prematuramente.
        if task:
            print(
                "[send_message] bot encerrou a conversa com tarefas pendentes: "
                f"{task.get('instruction', task)!r}"
            )
        _dump_debug_html(state.get("turns", 0), state["current_message"], state["current_options"])

        return {
            "end": True
        }

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

    return {
        "aguardar_count": 0,
        "messages": novas_mensagens,
        "turns": turns,
        "current_task": next_task(state),
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

    caminho_modelo = localizar_arquivo_modelo()

    if caminho_modelo is not None:
        resposta_content = _gerar_comparativo_com_modelo(
            state.get("messages", []),
            motivo_encerramento,
            caminho_modelo,
            state.get("tasks", []),
            state.get("conversation_origin", "Origem não informada."),
        )
    else:
        resposta_content = _gerar_resumo_padrao(conversa, motivo_encerramento)

    return {"summary": resposta_content}


def _gerar_resumo_padrao(conversa: str, motivo_encerramento: str) -> str:
    """
    Comportamento padrão (sem arquivo "modelo" configurado): gera um
    resumo textual da conversa, possíveis falhas e melhorias.
    """
    prompt = f"""
Analise a conversa abaixo, que é um teste automatizado de chatbot.

Motivo técnico do encerramento: {motivo_encerramento}

Conversa:
{conversa}

Retorne SOMENTE erros concretos observados na conversa.

Formato de cada item:
**Erro N — título curto**
- Esperado: comportamento correto.
- Ocorreu: mensagem ou comportamento observado.
- Problema: explicação simples de por que está errado.

Regras:
- Não escreva resumo, conclusão, acertos, melhorias ou despedida.
- Não invente erros. Se não houver erro comprovável, responda apenas:
  "Nenhum erro identificado."
- Use poucas palavras e cite apenas o trecho necessário.
"""

    resposta = llm.invoke(prompt)
    return resposta.content


def _gerar_comparativo_com_modelo(
    mensagens: list[str],
    motivo_encerramento: str,
    caminho_modelo: str,
    tasks: list[dict],
    conversation_origin: str,
) -> str:
    """
    Gera um comparativo entre o fluxo esperado (arquivo "modelo" --
    imagem ou PDF exportado do Figma, com o design/fluxo de mensagens
    planejado) e o que o chatbot realmente respondeu durante o teste.

    Em vez de tentar extrair o texto do design via processamento de
    PDF (frágil e impreciso, já que o Figma mistura balões de mensagem
    com rótulos do fluxograma no layout), enviamos a própria imagem do
    modelo para a LLM multimodal -- ela lê o design diretamente e
    compara com o texto real da conversa capturada pelo Selenium.
    """
    imagens_base64 = carregar_imagens_modelo()

    if not imagens_base64:
        # o arquivo existe mas não foi possível carregá-lo (erro de
        # conversão, formato inválido etc.) -- cai para o resumo padrão
        # em vez de falhar o teste inteiro por causa disso
        print(
            f"[generate_summary] arquivo de modelo encontrado em "
            f"{caminho_modelo!r} mas não foi possível carregá-lo; "
            f"gerando resumo padrão"
        )
        return _gerar_resumo_padrao(conversa, motivo_encerramento)

    nome_arquivo = os.path.basename(caminho_modelo)

    roteiro_teste = carregar_instrucoes()
    conversa_estruturada = _mensagens_por_cenario(mensagens)
    conversa_segmentada = json.dumps(
        conversa_estruturada,
        ensure_ascii=False,
        indent=2,
    )
    roteiro_segmentado = json.dumps(
        _roteiro_por_cenario(tasks),
        ensure_ascii=False,
        indent=2,
    )

    texto_instrucao = f"""
Você está validando uma conversa de chatbot em um teste automatizado.

Origem da conversa capturada:
{conversation_origin}

Origem do fluxo esperado:
Arquivo "{nome_arquivo}" exportado do Figma.

A tarefa é comparar o que foi esperado com o que realmente aconteceu,
sem assumir contexto específico do domínio.

Abaixo está a conversa REAL capturada durante o teste.
Ela está separada por cenário e em JSON para reduzir mistura entre
ramificações.

--- CONVERSA REAL ESTRUTURADA ---
{conversa_segmentada}
--- FIM DA CONVERSA REAL ---

--- ROTEIRO EXECUTADO ESTRUTURADO ---
{roteiro_segmentado}
--- FIM DO ROTEIRO ---

--- ROTEIRO ORIGINAL ---
{roteiro_teste or "Nenhum roteiro específico."}
--- FIM DO ROTEIRO ORIGINAL ---

Motivo do encerramento do teste: {motivo_encerramento}

Compare a conversa com o conteúdo VISÍVEL nas imagens e retorne SOMENTE
JSON válido, sem markdown, neste formato:

{{
  "erros": [
    {{
      "titulo": "título curto",
      "esperado_no_modelo": "transcrição literal do modelo",
      "ocorreu": "transcrição literal da mensagem/opções do bot",
      "problema": "explicação curta e didática"
    }}
  ]
}}

Regras obrigatórias:
- Não retorne resumo, conclusão, mensagens que batem, acertos,
  recomendações, prioridades ou despedida.
- Use poucas palavras. Não repita o mesmo erro em itens diferentes.
- Cada erro precisa conter evidência literal nos campos
  "esperado_no_modelo" e "ocorreu". Não use descrições vagas.
- Se esperado e ocorrido forem iguais, isso NÃO é erro e o item deve ser
  omitido. A escolha feita pelo USER também nunca prova que as opções do
  BOT estavam erradas; use apenas "OPÇÕES DO BOT" como evidência.
- A imagem é a única fonte do fluxo esperado. Não atribua ao modelo
  nenhuma mensagem que não esteja claramente visível nele.
- Compare cenário por cenário. Não misture opções, textos ou emojis de
  um cenário com outro.
- Use `ordem_global`, `total_mensagens` e `total_tarefas` para conferir
  sequência e cobertura total do fluxo.
- Se uma mensagem não tiver `opcoes` no JSON da conversa, não invente
  opções para ela.
- Cada item da conversa representa uma única bolha do bot. Não junte
  uma mensagem com a seguinte na mesma comparação.
- Texto entre chaves é variável de template. Se o
  restante da frase bate, isso não é erro.
- Use o `ROTEIRO EXECUTADO ESTRUTURADO` apenas como guia de ordem e
  intenção do teste. Nunca transfira opções, textos ou requisitos de um
  cenário para outro.
- Verifique sempre a fraseologia completa. Isso inclui ordem das
  palavras, pontuação, espaços, quebras de linha, acentos, caracteres
  especiais, emojis, negrito, itálico e qualquer outra marcação visível.
- Verifique a ordem completa da conversa. Se uma mensagem faltou,
  sobrou, foi duplicada, ou apareceu fora de posição, isso é erro.
- Verifique também a cobertura total do fluxo. Se uma mensagem esperada
  não foi enviada, ou se o bot enviou uma mensagem extra, registre.
- Compare também todas as opções/botões visíveis, não apenas a pergunta.
  Liste opções ausentes, extras ou com texto diferente, inclusive na
  ordem em que aparecem.
- Se houver negrito, itálico, sublinhado, aspas, parênteses, quebras de
  linha ou qualquer outro destaque visível, trate isso como parte do
  texto comparado.
- Compare emojis como parte do texto. Emoji ausente, extra ou diferente
  é uma divergência e deve ser indicado com precisão.
- Ao citar uma mensagem, leia o bloco inteiro no modelo; não resuma nem
  chame de "incompleto" sem mostrar qual trecho ou emoji está diferente.
- Diferencie mensagem ausente de caminho não percorrido. Só marque uma
  mensagem como ausente se a conversa realmente entrou naquele ramo.
- O roteiro explica as ações intencionais do testador. Não classifique
  reinícios solicitados pelo roteiro, nem mensagens enviadas pelo
  testador, como erro do chatbot.
- Cada bloco "CENÁRIO" é independente. Não interprete a abertura do
  cenário seguinte como continuação espontânea do anterior.
- Ignore diferenças apenas visuais causadas pela captura textual de
  botões, menus e outros componentes visuais.
- Se não houver erro comprovável, retorne {{"erros": []}}.
  """

    conteudo_mensagem = [{"type": "text", "text": texto_instrucao}]

    for img_b64 in imagens_base64:
        conteudo_mensagem.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            }
        )

    mensagem = HumanMessage(content=conteudo_mensagem)

    try:
        resposta = llm.invoke([mensagem])
    except Exception as e:
        print(f"[generate_summary] falha ao chamar o modelo multimodal: {e}")
        print("[generate_summary] gerando resumo padrão como fallback")
        return _gerar_resumo_padrao(conversa, motivo_encerramento)

    return _formatar_erros(resposta.content, conversa_estruturada)


def _segmentar_cenarios(conversa: str) -> str:
    marcador = "SYSTEM: conversa reiniciada intencionalmente pelo roteiro de teste."
    partes = conversa.split(marcador)
    blocos = []

    for indice, parte in enumerate(partes, start=1):
        conteudo = parte.strip()
        if conteudo:
            blocos.append(f"=== CENÁRIO {indice} ===\n{conteudo}")

    return "\n\n".join(blocos)


def _formatar_erros(
    conteudo: str,
    conversa_estruturada: list[dict] | None = None,
) -> str:
    """Valida o JSON do modelo e produz um relatório curto e consistente."""
    bruto = conteudo.strip()
    if bruto.startswith("```"):
        bruto = bruto.strip("`").strip()
        if bruto.lower().startswith("json"):
            bruto = bruto[4:].strip()

    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, TypeError):
        return "Falha ao validar a análise: o modelo não retornou JSON válido."

    erros = dados.get("erros", []) if isinstance(dados, dict) else []
    itens = []
    vistos = set()

    for erro in erros:
        if not isinstance(erro, dict):
            continue

        titulo = str(erro.get("titulo", "")).strip()
        esperado = str(erro.get("esperado_no_modelo", "")).strip()
        ocorreu = str(erro.get("ocorreu", "")).strip()
        problema = str(erro.get("problema", "")).strip()

        # Itens sem evidência ou que comparam textos idênticos são falsos
        # positivos e não entram no relatório final.
        if not all((titulo, esperado, ocorreu, problema)):
            continue
        if esperado.casefold() == ocorreu.casefold():
            continue
        if _eh_template_variavel(esperado, ocorreu):
            continue
        if _eh_mensagem_agrupada(esperado, ocorreu, conversa_estruturada):
            continue

        chave = (esperado.casefold(), ocorreu.casefold())
        if chave in vistos:
            continue
        vistos.add(chave)

        numero = len(itens) + 1
        itens.append(
            f"**Erro {numero} — {titulo}**\n"
            f"- Esperado no modelo: {esperado}\n"
            f"- Ocorreu: {ocorreu}\n"
            f"- Problema: {problema}"
        )

    return "\n\n".join(itens) if itens else "Nenhum erro identificado."

def current_task(state):
    indice = state.get("current_task", 0)

    if indice >= len(state["tasks"]):
        return None

    return state["tasks"][indice]


def next_task(state):
    task = current_task(state)

    indice = state["current_task"]

    if task and task.get("advance_on_response", False):
        indice += 1

    return indice
