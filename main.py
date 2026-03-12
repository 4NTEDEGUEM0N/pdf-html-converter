import fitz
import os
import time
import google.generativeai as genai
from PIL import Image
from prompt_html import get_prompt, get_html
import re
import base64
import html
from dotenv import load_dotenv

load_dotenv()

CHAVE_API = os.getenv("CHAVE_API")
genai.configure(api_key=CHAVE_API)

def parse_paginas(string_paginas: str, total_paginas: int):
    if not string_paginas.strip():
        return list(range(total_paginas))
    
    paginas = set()
    partes = string_paginas.strip().replace(" ", "").split(',')
    for parte in partes:
        parte = parte.strip()
        if not parte: continue
        if '-' in parte:
            inicio, fim = parte.split('-', 1)
            try:
                start_idx = int(inicio) - 1
                if not fim:
                    end_idx = total_paginas - 1
                else:
                    end_idx = int(fim) - 1
                if not (0 <= start_idx < total_paginas and 0 <= end_idx < total_paginas and start_idx <= end_idx): 
                    return None
                paginas.update(range(start_idx, end_idx + 1))

            except ValueError:
                return None
        else:
            try:
                idx = int(parte) - 1
                if not (0 <= idx < total_paginas): 
                    return None
                paginas.add(idx)
            except ValueError:
                return None

    return sorted(list(paginas))

def pdf_para_imagens(caminho_pdf: str, paginas_selecionadas: list, dpi: int = 100):
    pdf_basename = os.path.basename(caminho_pdf)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    pasta_saida = os.path.join('temp_processing', f"{pdf_basename}_{timestamp}")
    os.makedirs(pasta_saida, exist_ok=True)
    image_paths = []

    documento = fitz.open(caminho_pdf)

    for numero_pagina in paginas_selecionadas:
        pagina = documento.load_page(numero_pagina)
        
        imagem = pagina.get_pixmap(dpi=dpi)
        
        nome_arquivo = os.path.join(f"{pasta_saida}", f"pagina_{numero_pagina + 1}.png")
        image_paths.append(nome_arquivo)
        
        imagem.save(nome_arquivo)
        print(f"Página {numero_pagina + 1} salva como {nome_arquivo}")

    return pasta_saida, image_paths

def analisar_imagens_com_gemini(pdf_basename: str, lista_caminhos: list):
    modelo = genai.GenerativeModel('gemini-2.5-flash-lite')
    respostas = []

    for caminho in lista_caminhos:
        try:
            print(f"Processando: {caminho}...")
            
            if not os.path.exists(caminho):
                raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")
                
            imagem = Image.open(caminho)

            with open(caminho, "rb") as image_file:
                image_data = image_file.read()

            current_page_num_in_doc = caminho.split("pagina_")[1].split(".")[0]
            base64_image_data = base64.b64encode(image_data).decode('utf-8')
            prompt = get_prompt(pdf_basename, imagem.size, current_page_num_in_doc)
            
            response = modelo.generate_content([prompt, imagem])


            if response and response.candidates:
                final_finish_reason = response.candidates[0].finish_reason.value if response.candidates[0].finish_reason else 'UNKNOWN'

                response_text_content = response.text
                if not response_text_content and response.candidates[0].content and response.candidates[0].content.parts:
                    response_text_content = ''.join(
                        part.text for part in response.candidates[0].content.parts if hasattr(part, 'text')
                    )

                html_body = None
                if response_text_content:
                    match = re.search(r"```html\s*(.*?)\s*```", response_text_content, re.DOTALL | re.IGNORECASE)
                    if match:
                        html_body = match.group(1).strip()
                    else:
                        # Fallback for cases where the AI might forget the markdown block
                        trimmed_text = response_text_content.strip()
                        if trimmed_text.startswith("<") and trimmed_text.endswith(">") and \
                            re.search(r"<p|<div|<span|<table|<ul|<ol|<h[1-6]", trimmed_text, re.IGNORECASE): # Basic check for HTML content
                            html_body = trimmed_text
                        else:
                            html_body = None
                    if html_body:
                        html_body = re.sub(r'<bdi>([a-zA-Z0-9_](?:<sup>.*?</sup>)?)</bdi>', r'\1', html_body)
                        html_body = re.sub(r'<bdi>(\\[a-zA-Z]+(?:\{.*?\})?(?:\s*\^\{.*?\})?(?:\s*_\{.*?\})?)</bdi>', r'\1',
                                            html_body)
                        html_body = re.sub(r'<bdi>\s*</bdi>', '', html_body)

            if html_body is None:
                print(f"Erro: Falha ao extrair HTML para {pdf_basename} (pág {current_page_num_in_doc}).")
                if response:
                    print(f"Texto bruto (300c): {str(response.text)[:300]}...")
                    print(f"Motivo: {final_finish_reason} ({response.candidates[0].finish_reason.name})")
            
            resposta = {"page_num_in_doc": current_page_num_in_doc, "body": html_body, "base64_image": base64_image_data}
            respostas.append(resposta)
            print(f"✅ Sucesso!")
            
            time.sleep(2)
            
        except Exception as e:
            print(f"❌ Erro ao processar {caminho}: {e}")
            respostas.append(f"Erro na extração: {e}")
            
    return respostas

def merge_html(pdf_filename_title: str, report_button: bool, content_list: list[dict], output_path: str = "output/"):
    merged_html, report_button_forms = get_html(pdf_filename_title, report_button)

    for i, content_data in enumerate(content_list):
        page_num_in_doc = content_data.get("page_num_in_doc")
        html_body = content_data.get("body", "")
        base64_image = content_data.get("base64_image")
        if i > 0: merged_html += f"\n<hr class=\"page-separator\" aria-hidden=\"true\">\n"
        merged_html += f"<article class='page-content' id='page-{page_num_in_doc}' aria-labelledby='page-heading-{page_num_in_doc}'>\n"
        merged_html += f"<h2 id='page-heading-{page_num_in_doc}'>Página {page_num_in_doc}</h2>\n"
        merged_html += html_body if html_body else f"<p><i>[Conteúdo não pôde ser extraído para a página {page_num_in_doc}.]</i></p>"
        if html_body and base64_image and "[Descrição da imagem:" in html_body:
            safe_alt_text = html.escape(f"Imagem original da página {page_num_in_doc}")
            merged_html += f"""
                <details class="original-page-viewer">
                    <summary>Ver Imagem da Página Original {page_num_in_doc}</summary>
                    <div style="text-align: center; padding: 10px;">
                        <img src="data:image/png;base64,{base64_image}" alt="{safe_alt_text}" style="max-width: 100%; height: auto;" aria-hidden="true">
                    </div>
                </details>
            """
        merged_html += "\n</article>\n"
    merged_html += f"\n    </main> \n    {report_button_forms}\n</body>\n</html>"

    #full_output_path = os.path.join(output_path, pdf_filename_title, ".html")
    full_output_path = "output/teste.html"
    with open(full_output_path, "w", encoding="utf-8") as f: 
        f.write(merged_html)


def processar_pdf(caminho_pdf: str, string_paginas: str):
    with fitz.open(caminho_pdf) as temp_doc:
        total_paginas = temp_doc.page_count
    
    print("Iniciando o Parser das Páginas")
    paginas_selecionadas = parse_paginas(string_paginas, total_paginas)
    if paginas_selecionadas is None:
        print("Falha no parser de páginas")
        return
    print("Fim do Parse das Páginas")
    print()

    input("Aperte ENTER para continuar...")
    print()

    print("Convertendo o PDF para imagens")
    pasta_saida, image_paths = pdf_para_imagens(caminho_pdf, paginas_selecionadas)
    if not image_paths: 
        print("Falha ao converter PDF para imagens.")
        return
    print("Fim da conversão")
    print()

    print("Gerando HTML de cada imagem")
    pdf_basename = os.path.basename(caminho_pdf)
    respostas = analisar_imagens_com_gemini(pdf_basename, image_paths)
    print("Os HTML foram gerados")

    print("Mesclando os HTML")
    merge_html(pdf_basename, True, respostas)
    print("HTML Mesclado")


processar_pdf("input/semana_04ap_gab.pdf", "1")
