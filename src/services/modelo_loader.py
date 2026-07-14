"""
Localiza e carrega o arquivo de referência "modelo" (o design/fluxo
esperado do chatbot, geralmente exportado do Figma) para ser usado na
comparação final com o que o teste automatizado realmente capturou.

O tester coloca um arquivo chamado exatamente "modelo" na raiz do
projeto, em um dos formatos suportados: .pdf, .png, .jpg ou .jpeg.
Assim como o "instrucoes.txt", esse arquivo é opcional -- se não
existir, o projeto simplesmente gera o resumo padrão (sem comparação).
"""

import base64
import os
from io import BytesIO
from typing import List, Optional

from PIL import Image, ImageChops

EXTENSOES_SUPORTADAS = [".pdf", ".png", ".jpg", ".jpeg"]

# Raiz do projeto (mesmo nível de instrucoes.txt / requirements.txt)
_RAIZ_PROJETO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Resolução usada para renderizar páginas de PDF como imagem. Zoom 1.0
# equivale ao tamanho nativo do PDF exportado -- para artboards do
# Figma isso já costuma ser grande o suficiente para manter o texto
# legível, sem gerar arquivos desnecessariamente pesados para a
# chamada à LLM. Aumente se o texto do modelo estiver saindo pequeno
# demais para o modelo ler corretamente.
PDF_RENDER_ZOOM = 1.0

# Limite de páginas de PDF renderizadas -- protege contra um "modelo"
# gigante (ex: um artboard do Figma com o storyboard inteiro) gerar
# dezenas de imagens e estourar o contexto/custo da chamada ao LLM.
MAX_PAGINAS_PDF = 5


def localizar_arquivo_modelo() -> Optional[str]:
    """
    Procura, na raiz do projeto, um arquivo chamado "modelo" com uma
    das extensões suportadas. Retorna o caminho completo se encontrar,
    ou None se não existir nenhum.
    """
    for ext in EXTENSOES_SUPORTADAS:
        caminho = os.path.join(_RAIZ_PROJETO, f"modelo{ext}")
        if os.path.isfile(caminho):
            return caminho
    return None


def _codificar_png(imagem: Image.Image) -> str:
    buffer = BytesIO()
    imagem.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _recortar_conteudo(imagem: Image.Image) -> Image.Image:
    """Remove margens quase uniformes sem cortar elementos do fluxograma."""
    rgb = imagem.convert("RGB")
    fundo = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diferenca = ImageChops.difference(rgb, fundo).convert("L")
    mascara = diferenca.point(lambda valor: 255 if valor > 10 else 0)
    bbox = mascara.getbbox()

    if not bbox:
        return rgb

    margem = 24
    esquerda = max(0, bbox[0] - margem)
    topo = max(0, bbox[1] - margem)
    direita = min(rgb.width, bbox[2] + margem)
    base = min(rgb.height, bbox[3] + margem)
    return rgb.crop((esquerda, topo, direita, base))


def _ampliar(imagem: Image.Image, largura_alvo: int = 1600) -> Image.Image:
    if imagem.width >= largura_alvo:
        return imagem
    escala = largura_alvo / imagem.width
    tamanho = (largura_alvo, max(1, round(imagem.height * escala)))
    return imagem.resize(tamanho, Image.Resampling.LANCZOS)


def _gerar_visoes(imagem: Image.Image) -> List[str]:
    """Gera visão geral e recortes ampliados para leitura multimodal."""
    conteudo = _recortar_conteudo(imagem)
    imagens = [_codificar_png(conteudo)]

    colunas = 3
    linhas = 2
    sobreposicao = 0.08
    largura = conteudo.width / colunas
    altura = conteudo.height / linhas

    for linha in range(linhas):
        for coluna in range(colunas):
            x1 = max(0, int(coluna * largura - largura * sobreposicao))
            y1 = max(0, int(linha * altura - altura * sobreposicao))
            x2 = min(
                conteudo.width,
                int((coluna + 1) * largura + largura * sobreposicao),
            )
            y2 = min(
                conteudo.height,
                int((linha + 1) * altura + altura * sobreposicao),
            )
            recorte = conteudo.crop((x1, y1, x2, y2))
            imagens.append(_codificar_png(_ampliar(recorte)))

    return imagens


def _imagem_para_base64(caminho: str) -> List[str]:
    with open(caminho, "rb") as f:
        imagem = Image.open(f)
        imagem.load()
    return _gerar_visoes(imagem)


def _pdf_para_imagens_base64(caminho_pdf: str) -> List[str]:
    """
    Renderiza cada página do PDF como uma imagem PNG e retorna a lista
    de imagens já codificadas em base64, prontas para uso em uma
    mensagem multimodal do LangChain.

    Fazemos a conversão para imagem em vez de enviar o PDF bruto porque
    modelos de linguagem multimodais geralmente têm suporte mais
    confiável e testado para imagens do que para PDFs complexos/
    vetoriais como os exportados pelo Figma.
    """
    import fitz  # PyMuPDF -- import local para não exigir a dependência

    imagens_base64 = []

    with fitz.open(caminho_pdf) as doc:
        total_paginas = min(len(doc), MAX_PAGINAS_PDF)
        matriz_zoom = fitz.Matrix(PDF_RENDER_ZOOM, PDF_RENDER_ZOOM)

        for i in range(total_paginas):
            pagina = doc[i]
            pixmap = pagina.get_pixmap(matrix=matriz_zoom)
            png_bytes = pixmap.tobytes("png")
            imagem = Image.open(BytesIO(png_bytes))
            imagem.load()
            imagens_base64.extend(_gerar_visoes(imagem))

    return imagens_base64


def carregar_imagens_modelo() -> List[str]:
    """
    Localiza o arquivo "modelo" (se existir) e retorna uma lista de
    imagens em base64 prontas para envio à LLM multimodal:
      - se for .png/.jpg/.jpeg: retorna uma lista com essa única imagem
      - se for .pdf: renderiza cada página (até MAX_PAGINAS_PDF) como
        imagem e retorna a lista de todas

    Retorna lista vazia se nenhum arquivo "modelo" for encontrado.
    """
    caminho = localizar_arquivo_modelo()
    if caminho is None:
        return []

    extensao = os.path.splitext(caminho)[1].lower()

    if extensao == ".pdf":
        try:
            return _pdf_para_imagens_base64(caminho)
        except Exception as e:
            print(f"[modelo_loader] falha ao converter PDF em imagem: {e}")
            return []

    try:
        return _imagem_para_base64(caminho)
    except Exception as e:
        print(f"[modelo_loader] falha ao ler arquivo de imagem: {e}")
        return []
