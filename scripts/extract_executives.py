#!/usr/bin/env python3
"""
Script para extrair decisores do LinkedIn usando Lusha + Claude Vision
Controla o Chrome automaticamente para buscar CEOs, Diretores e gestores de TI
Salva resultado em CSV com: Nome, Cargo, Link LinkedIn, Telefone
"""

import os
import time
import json
import csv
from datetime import datetime
from pathlib import Path
import base64

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import anthropic

# ============ CONFIGURAÇÃO ============

COMPANIES = [
    {'name': 'CCR Aeroportos', 'url': 'https://www.linkedin.com/company/motivaaeroportos/people/'},
    {'name': 'Fraport Brasil', 'url': 'https://www.linkedin.com/company/fraport-brasil-porto-alegre-airport/people/'},
    {'name': 'Guindastes Brasil', 'url': 'https://br.linkedin.com/company/guindastesbrasil/people/'},
    {'name': 'Prioriza Soluções Ferroviárias', 'url': 'https://www.linkedin.com/company/prioriza-solu%C3%A7%C3%B5es-ferrovi%C3%A1rias/people/'},
    {'name': 'Gatos & Atos', 'url': 'https://www.linkedin.com/company/gatos-atos/people/'},
    {'name': 'Embrast Embalagens', 'url': 'https://www.linkedin.com/company/embrast-embalagens/people/'},
    {'name': 'Printbag Embalagens', 'url': 'https://www.linkedin.com/company/printbag-embalagens-ltda/people/'},
    {'name': 'Inplac Plásticos', 'url': 'https://www.linkedin.com/company/inplacplasticos/people/'},
    {'name': 'Gira Brasil Distribuidora', 'url': 'https://www.linkedin.com/company/girabrasil-distribuidora/people/'},
    {'name': 'Gox Internet', 'url': 'https://br.linkedin.com/company/goxinternet/people/'},
    {'name': 'Forusi', 'url': 'https://br.linkedin.com/company/forusi/people/'},
]

SEARCH_TERMS = ['ceo', 'diretor', 'diretor ti', 'diretor de ti', 'gerente ti', 'gerente de ti', 'chief']
SCREENSHOT_DIR = 'screenshots'
OUTPUT_FILE = f'decisores_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

# ============ SETUP ============

# Cria pasta de screenshots
Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

# Inicializa cliente Anthropic
api_key = os.getenv('ANTHROPIC_API_KEY')
if not api_key:
    raise ValueError("ANTHROPIC_API_KEY não configurada. Configure em .env")
client = anthropic.Anthropic(api_key=api_key)

# ============ FUNÇÕES ============

def setup_browser():
    """Configura e retorna o Chrome/Edge controlado"""
    options = Options()
    # options.add_argument('--headless')  # Descomente se não quiser ver o navegador
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    driver = webdriver.Chrome(options=options)
    return driver

def search_employees(driver, company_name, search_term):
    """
    Busca por funcionários com um termo específico
    Retorna lista de nomes encontrados
    """
    try:
        # Procura pela caixa de busca
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder*="Pesquisar"]'))
        )

        # Limpa e busca
        search_box.clear()
        search_box.send_keys(search_term)
        search_box.send_keys('\n')

        # Aguarda resultados
        time.sleep(2)

        # Pega elementos dos perfis
        profile_elements = driver.find_elements(By.CSS_SELECTOR, '.artdeco-entity-lockup')

        results = []
        for elem in profile_elements[:5]:  # Limite a 5 por busca para não ficar muito longo
            try:
                name = elem.find_element(By.CSS_SELECTOR, '.artdeco-entity-lockup__title span').text
                results.append(name)
            except:
                pass

        return results
    except TimeoutException:
        print(f"  ❌ Timeout ao buscar '{search_term}'")
        return []
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar: {e}")
        return []

def extract_data_from_screenshot(screenshot_path, person_name):
    """
    Envia screenshot para Claude Vision extrair dados
    Retorna: {nome, cargo, link_linkedin, telefone}
    """
    try:
        # Lê o arquivo de screenshot
        with open(screenshot_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

        # Envia para Claude com visão
        message = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=500,
            messages=[
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/png',
                                'data': image_data
                            }
                        },
                        {
                            'type': 'text',
                            'text': f'''
Analise este screenshot do LinkedIn + Lusha.
Extraia EXATAMENTE os seguintes dados:
1. Nome completo da pessoa
2. Cargo/Posição (ex: CEO, Diretor, Gerente de TI)
3. Link do perfil LinkedIn (URL completa começando com https://)
4. Telefone (se visível no painel Lusha, com DDD e número)

Se algum dado não for visível, indique "NÃO VISÍVEL".

Responda em JSON assim:
{{"nome": "...", "cargo": "...", "linkedin_url": "...", "telefone": "..."}}
'''
                        }
                    ]
                }
            ]
        )

        # Parse resposta
        response_text = message.content[0].text

        # Extrai JSON da resposta
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return data
        else:
            return None
    except Exception as e:
        print(f"    ❌ Erro ao extrair dados: {e}")
        return None

def process_company(driver, company):
    """Processa uma empresa inteira"""
    company_name = company['name']
    company_url = company['url']

    print(f"\n🏢 Processando: {company_name}")
    print(f"   URL: {company_url}")

    try:
        driver.get(company_url)
        time.sleep(3)  # Aguarda carregamento

        found_decisores = []

        # Tenta cada termo de busca
        for search_term in SEARCH_TERMS:
            print(f"  🔍 Buscando: '{search_term}'")

            employees = search_employees(driver, company_name, search_term)

            for emp_name in employees:
                print(f"    → Encontrado: {emp_name}")

                # Clica no perfil para abrir o painel Lusha
                try:
                    profile = driver.find_element(By.XPATH, f"//*[contains(text(), '{emp_name}')]")
                    profile.click()
                    time.sleep(2)  # Aguarda Lusha carregar

                    # Tira screenshot
                    screenshot_filename = f"{SCREENSHOT_DIR}/{company_name.replace(' ', '_')}_{emp_name.replace(' ', '_')}.png"
                    driver.save_screenshot(screenshot_filename)
                    print(f"      📸 Screenshot: {screenshot_filename}")

                    # Extrai dados via Claude Vision
                    data = extract_data_from_screenshot(screenshot_filename, emp_name)

                    if data:
                        data['empresa'] = company_name
                        found_decisores.append(data)
                        print(f"      ✅ Cargo: {data.get('cargo', 'N/A')}")
                        print(f"      ✅ Telefone: {data.get('telefone', 'N/A')}")

                    # Fecha o painel (ESC)
                    driver.find_element(By.TAG_NAME, 'body').send_keys('\x1b')
                    time.sleep(1)

                except Exception as e:
                    print(f"      ⚠️  Erro ao processar perfil: {e}")

        return found_decisores

    except Exception as e:
        print(f"❌ Erro ao processar {company_name}: {e}")
        return []

def save_results(all_decisores):
    """Salva resultados em CSV"""
    if not all_decisores:
        print("\n❌ Nenhum decisor encontrado!")
        return

    # Escreve CSV
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['empresa', 'nome', 'cargo', 'linkedin_url', 'telefone'])
        writer.writeheader()
        writer.writerows(all_decisores)

    print(f"\n✅ Resultados salvos em: {OUTPUT_FILE}")
    print(f"   Total de decisores encontrados: {len(all_decisores)}")

# ============ MAIN ============

def main():
    driver = setup_browser()
    all_decisores = []

    try:
        for company in COMPANIES:
            decisores = process_company(driver, company)
            all_decisores.extend(decisores)
            time.sleep(2)  # Pausa entre empresas

        save_results(all_decisores)

    finally:
        driver.quit()
        print("\n🏁 Script finalizado!")

if __name__ == '__main__':
    main()
