import time
from datetime import datetime
from typing import List, Dict, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BUBBLE_SELECTOR = ".bubble.left, .bubble.right"

OPTIONS_CONTAINER_SELECTOR = (
    ".slideshow-track.options ul.item-list li, "
    ".slideshow-container .fixed-options ul.item-list li"
)


class Browser:
    def __init__(self):
        self.__options = Options()
        self.__options.add_experimental_option("detach", True)
        self.__browser: Optional[WebDriver] = None

    def newPage(self, url: str, timeout: int = 20):
        self.__browser = webdriver.Chrome(options=self.__options)
        self.__browser.get(url)

        self._wait_for(
            EC.presence_of_element_located((By.ID, "msg-textarea")),
            timeout=timeout,
        )
        time.sleep(5)
        data_hoje = datetime.now().strftime("%d/%m/%Y")
        self.sendMessage(f"ChatBot Tester - {data_hoje}")
        time.sleep(5)

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

        Também tolera falhas transitórias de conexão com o
        ChromeDriver (ex: "ConnectionResetError" observado em testes
        reais) fazendo até 2 tentativas extras antes de propagar o
        erro -- uma falha pontual de rede local não deveria derrubar o
        teste inteiro.
        """
        tentativas = 3
        ultimo_erro = None

        for _ in range(tentativas):
            try:
                bubbles = self.__browser.find_elements(By.CSS_SELECTOR, BUBBLE_SELECTOR)
                count = 0
                for b in bubbles:
                    try:
                        if b.text.strip():
                            count += 1
                    except StaleElementReferenceException:
                        continue
                return count
            except WebDriverException as e:
                # erro de conexão com o ChromeDriver (ex: processo do
                # Chrome travou/morreu, ou reset de conexão local) --
                # espera um instante e tenta de novo antes de desistir
                ultimo_erro = e
                time.sleep(1)

        # se mesmo assim continuar falhando, propaga o erro para que
        # a camada de cima (nodes.py) possa encerrar o teste de forma
        # controlada em vez de deixar o traceback cru derrubar o
        # processo no meio de uma espera do Selenium
        raise ultimo_erro

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

        # Uma resposta do bot pode ser composta por várias bolhas em
        # sequência (ex.: texto de pesar e, depois, pergunta com opções).
        # Só liberamos o agente após um período contínuo sem mudanças.
        self._wait_for_dom_stable(timeout=6, stable_for=1.5)

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

    def _wait_for_dom_stable(
        self,
        timeout: int = 6,
        poll: float = 0.25,
        stable_for: float = 1.5,
    ):
        """
        Aguarda ate que o tamanho do HTML da pagina pare de mudar entre
        duas leituras seguidas (ou ate estourar o timeout). Usado para
        dar tempo do Blip terminar de renderizar elementos extras
        (opcoes, cards, anexos) que chegam depois da bolha de texto.
        """
        import time

        deadline = time.monotonic() + timeout
        last_snapshot = None
        stable_since = None

        while time.monotonic() < deadline:
            try:
                # Comprimento + quantidade de bolhas detectam tanto novas
                # mensagens quanto opções anexadas a uma bolha existente.
                snapshot = (
                    len(self.__browser.page_source),
                    self._count_bubbles(),
                )
            except Exception:
                return

            agora = time.monotonic()

            if snapshot != last_snapshot:
                last_snapshot = snapshot
                stable_since = agora
            elif stable_since is not None and agora - stable_since >= stable_for:
                return

            time.sleep(poll)

    # ------------------------------------------------------------------
    # Envio de mensagens
    # ------------------------------------------------------------------
    def sendMessage(self, msg: str, wait_response: bool = True, timeout: int = 15):
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

    def selectOption(
        self,
        option_text: str,
        wait_response: bool = True,
        timeout: int = 15,
    ) -> bool:
        """
        Clica em uma opção pertencente à ÚLTIMA mensagem do bot.

        Isso evita clicar em botões antigos ("Sim", "Não", etc.) que ainda
        permanecem no DOM de conversas anteriores.
        """
        previous_count = self._count_bubbles()

        # Última bolha do bot
        try:
            bot_bubbles = self.__browser.find_elements(By.CSS_SELECTOR, ".bubble.left")

            if not bot_bubbles:
                return False

            ultima_bolha = bot_bubbles[-1]

        except WebDriverException:
            return False

        # Procura o container de opções associado apenas a essa bolha
        candidatos_xpath = [
            "./ancestor::div[contains(@class, 'blip-relative')][1]",
            "./ancestor::*[contains(@class,'left') or contains(@class,'right')]"
            "[.//div[contains(@class,'slideshow-track') and contains(@class,'options')]][1]",
            "./ancestor::div[position()<=6][.//div[contains(@class,'slideshow-track')]][1]",
        ]

        option_elements = []

        for xpath in candidatos_xpath:
            try:
                container = ultima_bolha.find_element(By.XPATH, xpath)

                encontrados = container.find_elements(
                    By.CSS_SELECTOR,
                    OPTIONS_CONTAINER_SELECTOR,
                )

                if encontrados:
                    option_elements = encontrados
                    break

            except (
                NoSuchElementException,
                StaleElementReferenceException,
            ):
                continue

        if not option_elements:
            print("[selectOption] Nenhuma opção encontrada para a última mensagem.")
            return False

        alvo = None
        texto_procurado = option_text.strip().lower()

        # Correspondência exata
        for opt in option_elements:
            try:
                texto = opt.text.strip()

                if texto.lower() == texto_procurado:
                    alvo = opt
                    break

            except StaleElementReferenceException:
                continue

        # Correspondência parcial
        if alvo is None:
            for opt in option_elements:
                try:
                    texto = opt.text.strip().lower()

                    if texto_procurado in texto:
                        alvo = opt
                        break

                except StaleElementReferenceException:
                    continue

        if alvo is None:
            print(
                f"[selectOption] Opção '{option_text}' não encontrada."
            )

            print(
                "[selectOption] Opções disponíveis:",
                [o.text for o in option_elements],
            )

            return False

        try:
            self.__browser.execute_script(
                "arguments[0].scrollIntoView({block:'center'});",
                alvo,
            )

            WebDriverWait(self.__browser, 5).until(
                EC.element_to_be_clickable(alvo)
            )

            try:
                alvo.click()
            except (
                ElementClickInterceptedException,
                WebDriverException,
            ):
                self.__browser.execute_script(
                    "arguments[0].click();",
                    alvo,
                )

        except StaleElementReferenceException:
            return False
        

        if wait_response:
            inicio = time.time()

            chegou = self.waitForNewMessage(previous_count, timeout=timeout)

            print(
                f"[selectOption] waitForNewMessage={chegou} "
                f"({time.time() - inicio:.2f}s)"
            )

            if not chegou:
                print(
                    "[selectOption] Nenhuma nova mensagem recebida após o clique."
                )
                return False

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
        candidatos_xpath = [
            "./ancestor::div[contains(@class, 'blip-relative')][1]",
            "./ancestor::*[contains(@class, 'left') or contains(@class, 'right')]"
            "[.//div[contains(@class, 'slideshow-track') and contains(@class, 'options')]][1]",
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
    
    def restartConversation(self):
        return self.sendMessage("Novo Teste", wait_response=True)


browser = Browser()
