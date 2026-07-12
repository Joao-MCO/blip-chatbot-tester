from datetime import datetime
from typing import List, Dict, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    NoSuchElementException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# Seletor das bolhas de mensagem (bot = left, usuário = right)
BUBBLE_SELECTOR = ".bubble.left, .bubble.right"

# Seletor das opções/botões de menu que o Blip renderiza logo abaixo
# de uma bolha do bot (ex: "select", "quick reply", etc.)
OPTIONS_CONTAINER_SELECTOR = ".slideshow-track.options ul.item-list li"


class Browser:
    def __init__(self):
        self.__options = Options()
        self.__options.add_experimental_option("detach", True)
        self.__browser: Optional[WebDriver] = None

    def newPage(self, url: str, timeout: int = 20):
        self.__browser = webdriver.Chrome(options=self.__options)
        self.__browser.get(url)

        # Espera o chat carregar (textarea disponível) em vez de sleep fixo
        self._wait_for(
            EC.presence_of_element_located((By.ID, "msg-textarea")),
            timeout=timeout,
        )
        # A primeira mensagem enviada identifica que essa conversa é um
        # teste automatizado, com a data do dia -- útil para rastrear
        # esses atendimentos de teste no painel do Blip e diferenciá-los
        # de conversas reais de usuários.
        data_hoje = datetime.now().strftime("%d/%m/%Y")
        self.sendMessage(f"ChatBot Tester - {data_hoje}")

    def getBrowser(self) -> WebDriver:
        return self.__browser

    def getOptions(self) -> Options:
        return self.__options

    def setOptions(self, option: str):
        self.__options.add_argument(option)

    def getHtml(self) -> str:
        htmlCode = self.__browser.page_source
        site = BeautifulSoup(htmlCode, "html.parser")
        return site.prettify()

    def quit(self):
        """Fecha o browser. Sempre chamar ao final da execução."""
        if self.__browser is not None:
            try:
                self.__browser.quit()
            except Exception:
                pass
            finally:
                self.__browser = None

    # ------------------------------------------------------------------
    # Espera helpers
    # ------------------------------------------------------------------
    def _wait_for(self, condition, timeout: int = 10):
        return WebDriverWait(self.__browser, timeout).until(condition)

    def _count_bubbles(self) -> int:
        """
        Conta apenas bolhas com TEXTO não vazio. O Blip às vezes injeta
        a bolha nova no DOM antes do texto terminar de renderizar
        (ex: uma bolha "left" vazia aparece um instante antes do texto
        real ser preenchido). Se contássemos bolhas vazias, o código
        acharia que uma nova mensagem já chegou e leria o DOM cedo
        demais, pegando a mensagem ANTERIOR (a última com texto) em vez
        de esperar a mensagem nova terminar de aparecer.
        """
        bubbles = self.__browser.find_elements(By.CSS_SELECTOR, BUBBLE_SELECTOR)
        count = 0
        for b in bubbles:
            try:
                if b.text.strip():
                    count += 1
            except StaleElementReferenceException:
                continue
        return count

    def waitForNewMessage(self, previous_count: int, timeout: int = 15) -> bool:
        """
        Espera até que uma nova bolha COM TEXTO apareça na conversa (bot
        respondeu) ou até estourar o timeout. Retorna True se uma nova
        mensagem chegou.

        Observado na prática: o Blip pode inserir uma bolha vazia no
        DOM (placeholder) um instante antes do texto real ser
        preenchido nela. "_count_bubbles" já ignora bolhas vazias, mas
        aqui damos uma folga extra depois da contagem bater, para dar
        tempo do texto realmente aparecer antes de seguir em frente.

        Além disso, depois que a bolha aparece, aguardamos o DOM "parar
        de crescer" -- o Blip costuma renderizar a bolha de texto
        primeiro e, em um tick seguinte, o bloco de opções
        (.slideshow-container) associado a ela.
        """
        try:
            self._wait_for(
                lambda d: self._count_bubbles() > previous_count,
                timeout=timeout,
            )
        except TimeoutException:
            return False

        # folga extra: garante que, mesmo que a bolha tenha acabado de
        # ganhar texto agora mesmo, o restante do conteúdo (e um
        # eventual slideshow de opções associado) tenha tempo de
        # aparecer também.
        self._wait_for_dom_stable(timeout=4)

        # espera extra e curta especificamente pelo container de opções,
        # sem falhar se não houver nenhum (mensagem sem menu é normal)
        try:
            self._wait_for(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, OPTIONS_CONTAINER_SELECTOR)
                ),
                timeout=2,
            )
        except TimeoutException:
            pass

        return True

    def _wait_for_dom_stable(self, timeout: int = 3, poll: float = 0.3):
        """
        Aguarda ate que o tamanho do HTML da pagina pare de mudar entre
        duas leituras seguidas (ou ate estourar o timeout). Usado para
        dar tempo do Blip terminar de renderizar elementos extras
        (opcoes, cards, anexos) que chegam depois da bolha de texto.
        """
        import time

        deadline = time.monotonic() + timeout
        last_len = None

        while time.monotonic() < deadline:
            try:
                current_len = len(self.__browser.page_source)
            except Exception:
                return

            if last_len is not None and current_len == last_len:
                return

            last_len = current_len
            time.sleep(poll)

    # ------------------------------------------------------------------
    # Envio de mensagens
    # ------------------------------------------------------------------
    def sendMessage(self, msg: str, wait_response: bool = False, timeout: int = 15):
        """
        Digita e envia uma mensagem de texto livre pelo textarea.
        Se wait_response=True, aguarda até uma nova bolha aparecer
        (em vez de usar sleep fixo).
        """
        previous_count = self._count_bubbles()

        textarea = self._wait_for(
            EC.element_to_be_clickable((By.ID, "msg-textarea"))
        )
        textarea.send_keys(msg + Keys.ENTER)

        if wait_response:
            self.waitForNewMessage(previous_count, timeout=timeout)

    def selectOption(self, option_text: str, wait_response: bool = True, timeout: int = 15) -> bool:
        """
        Clica em uma opção/botão de menu (elemento <li> do Blip) cujo
        texto bate com option_text. Isso é necessário porque, em menus
        de opções (select/quick-reply), o Blip espera o clique no
        elemento -- apenas digitar o texto no textarea pode não ser
        reconhecido como uma escolha válida pelo fluxo do bot.

        Retorna True se conseguiu clicar em alguma opção.
        """
        previous_count = self._count_bubbles()

        options = self.__browser.find_elements(By.CSS_SELECTOR, OPTIONS_CONTAINER_SELECTOR)

        alvo = None
        for opt in options:
            try:
                texto = opt.text.strip()
            except StaleElementReferenceException:
                continue
            if texto == option_text.strip():
                alvo = opt
                break

        if alvo is None:
            # fallback: tenta por correspondência parcial (case-insensitive)
            for opt in options:
                try:
                    texto = opt.text.strip().lower()
                except StaleElementReferenceException:
                    continue
                if option_text.strip().lower() in texto:
                    alvo = opt
                    break

        if alvo is None:
            return False

        try:
            self.__browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", alvo)
            alvo.click()
        except ElementClickInterceptedException:
            # alguns temas cobrem o <li> com overlay; clique via JS resolve
            self.__browser.execute_script("arguments[0].click();", alvo)
        except StaleElementReferenceException:
            return False

        if wait_response:
            self.waitForNewMessage(previous_count, timeout=timeout)

        return True

    # ------------------------------------------------------------------
    # Leitura de mensagens
    # ------------------------------------------------------------------
    def readMessages(self) -> List[Dict]:
        """
        Retorna a lista de mensagens da conversa, na ordem em que aparecem.
        Cada item tem: role ("bot"/"user"), content (texto da bolha) e
        options (lista de textos das opções de menu associadas àquela
        bolha do bot, se houver).
        """
        mensagens = []

        bubbles = self.__browser.find_elements(By.CSS_SELECTOR, BUBBLE_SELECTOR)

        for bubble in bubbles:
            try:
                texto = bubble.text.strip()
                classes = bubble.get_attribute("class")
            except StaleElementReferenceException:
                continue

            if not texto:
                continue

            role = "bot" if "left" in classes else "user" if "right" in classes else None
            if role is None:
                continue

            item = {"role": role, "content": texto, "options": []}

            if role == "bot":
                item["options"] = self._read_options_for_bubble(bubble)

            mensagens.append(item)

        # NOTA: NÃO usamos mais um fallback "global" que pega qualquer
        # .slideshow-track.options visível na página. O Blip NÃO remove
        # o menu de opções do DOM depois que o usuário clica -- ele
        # continua lá (só marcado como já respondido). Um fallback
        # global acabava "vazando" as opções de um menu ANTIGO/já
        # respondido para uma mensagem nova do bot que não tinha menu
        # nenhum, fazendo o agente tentar responder com uma opção que
        # não existe mais naquele contexto. A associação estrutural via
        # "blip-relative" (em _read_options_for_bubble) é confiável o
        # suficiente sozinha e evita esse falso positivo.
        return mensagens

    def _read_options_for_bubble(self, bubble) -> List[str]:
        """
        Procura opções de menu (.slideshow-track.options li) associadas
        a bolha do bot.

        No DOM do Blip, o container de opcoes (.slideshow-container)
        NAO fica dentro da bolha nem de um ancestral que a englobe
        diretamente -- ele aparece como IRMAO logo depois da bolha,
        dentro do mesmo bloco pai (ex: a div com classe "left" que
        envolve tanto a bolha quanto o slideshow):

            <div class="left">
                <div class="blip-card-container">...bubble...</div>
                <div class="slideshow-container left">...opcoes...</div>
            </div>

        Por isso subimos ate o ancestral "left"/"right" mais proximo
        que contenha um slideshow de opcoes (em vez de "blip-container",
        que pode nao englobar o slideshow), com um fallback mais amplo.
        """
        # 'blip-relative' e o container mais estreito que envolve UMA
        # unica mensagem (bolha + eventual slideshow de opcoes + avatar).
        # Containers mais largos (ex: blip-message-group) podem agrupar
        # VARIAS mensagens do bot em sequencia e vazar opcoes de uma
        # bolha para outra que nao tem relacao com elas.
        candidatos_xpath = [
            "./ancestor::div[contains(@class, 'blip-relative')][1]",
            "./ancestor::*[contains(@class, 'left') or contains(@class, 'right')]"
            "[.//div[contains(@class, 'slideshow-track') and contains(@class, 'options')]][1]",
            # fallback mais tolerante: sobe alguns níveis genéricos e
            # procura o slideshow em qualquer lugar dentro do bloco --
            # útil se o tema do Blip mudar a estrutura de classes
            "./ancestor::div[position()<=6][.//div[contains(@class,'slideshow-track')]][1]",
        ]

        option_elements = []
        for xpath in candidatos_xpath:
            try:
                container = bubble.find_element(By.XPATH, xpath)
            except (NoSuchElementException, StaleElementReferenceException):
                continue

            try:
                found = container.find_elements(
                    By.CSS_SELECTOR, OPTIONS_CONTAINER_SELECTOR
                )
            except StaleElementReferenceException:
                continue

            if found:
                option_elements = found
                break

        opcoes = []
        for opt in option_elements:
            try:
                texto = opt.text.strip()
            except StaleElementReferenceException:
                continue
            if texto:
                opcoes.append(texto)

        return opcoes


browser = Browser()
