"""SEI Extractor - versão standalone (arquivo único).

Mesma lógica do pacote em src/sei_extractor/ (config, debug, navigator, parser,
core, extractor, services e gui), fundida em um único arquivo sem imports de
pacote — no estilo do ExtratorCatmat.py. Pode ser executado diretamente
(`python ExtratorSEI.py`) ou empacotado com PyInstaller para gerar um
executável standalone.

O pacote instalável em src/sei_extractor/ (com o CLI `sei-extractor`) continua
sendo a fonte de verdade para desenvolvimento; este arquivo é gerado a partir
dele para fins de distribuição.
"""

import csv
import datetime
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
import pandas as pd
import pymupdf
import requests
from bs4 import BeautifulSoup
from dateutil.parser import parse as date_parse
from dotenv import load_dotenv
from playwright.sync_api import Frame, Page, Playwright, sync_playwright
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from tkinter import filedialog, messagebox

# ==============================================================================
# Config
# ==============================================================================
load_dotenv()

SEI_URL = "https://sei.saude.gov.br/"
SEI_USERNAME = os.getenv("SEI_USERNAME")
SEI_PASSWORD = os.getenv("SEI_PASSWORD")
THREADS = int(os.getenv("THREADS", 10))
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "t")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")

LOG_FILE = "sei_extractor.log"
logger = logging.getLogger("sei_extractor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_242_880, backupCount=3)
    _file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_file_handler)

console = Console()


# ==============================================================================
# Debug
# ==============================================================================
class DebugDumper:
    def __init__(self, output_dir: str = 'output'):
        self.output_dir = Path(os.environ.get('OUTPUT_DIR', output_dir))
        (self.output_dir / 'debug').mkdir(parents=True, exist_ok=True)

    def dump(self, page, tag: str):
        """Save page/frame HTML and a screenshot to output/debug for inspection.

        page: Playwright Page object (or None)
        tag: short string to identify the dump
        """
        try:
            debug_dir = self.output_dir / 'debug'
            ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            base = debug_dir / f"{tag}_{ts}"

            try:
                with open(base.with_suffix('.page.html'), 'w', encoding='utf-8') as fh:
                    fh.write(page.content() if page else '')
            except Exception:
                pass

            try:
                if page:
                    page.screenshot(path=str(base.with_suffix('.png')))
            except Exception:
                pass

            try:
                if page:
                    for fr in page.frames:
                        name = fr.name or 'frame'
                        safe = name.replace('/', '_') or 'frame'
                        try:
                            with open(debug_dir / f"{tag}_{safe}_{ts}.frame.html", 'w', encoding='utf-8') as fh:
                                fh.write(fr.content())
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                with open(base.with_suffix('.trace.txt'), 'w', encoding='utf-8') as fh:
                    fh.write(traceback.format_exc())
            except Exception:
                pass

            return str(base)

        except Exception:
            return None


# ==============================================================================
# Navigator
# ==============================================================================
class Navigator:
    def __init__(self, page: Page, debug: bool = False, dumper: Optional[DebugDumper] = None):
        self._page = page
        self._debug = debug
        self._dumper = dumper

    @property
    def page(self) -> Page:
        return self._page

    def wait(self, seconds: int = 3):
        try:
            self._page.wait_for_load_state('load')
        except Exception:
            pass
        time.sleep(seconds)

    def get_frame(self, name: str) -> Frame:
        fr = self._page.frame(name=name)
        if fr is None:
            if self._debug and self._dumper:
                try:
                    self._dumper.dump(self._page, f'missing_frame_{name}')
                except Exception:
                    pass
            raise RuntimeError(f"Frame '{name}' not found")
        return fr

    def click_consultar_processo(self):
        try:
            self._page.wait_for_selector("#ifrConteudoVisualizacao", timeout=10000)
            frame = self.get_frame('ifrConteudoVisualizacao')
        except Exception:
            if self._debug and self._dumper:
                try:
                    self._dumper.dump(self._page, 'missing_ifrConteudoVisualizacao')
                except Exception:
                    pass
            raise

        try:
            frame.locator('xpath=//*[@title="Consultar Processo"]').click()
            if self._debug:
                try:
                    frame.keyboard.press('Enter')
                except Exception:
                    pass
            return
        except Exception:
            pass

        try:
            frame.locator('xpath=//*[@title="Consultar/Alterar Processo"]').click()
            if self._debug:
                try:
                    frame.keyboard.press('Enter')
                except Exception:
                    pass
            return
        except Exception:
            if self._debug and self._dumper:
                try:
                    self._dumper.dump(self._page, 'click_consultar_processo_failed')
                except Exception:
                    pass
            raise

    def abrir_todas_pastas(self):
        frame = self.get_frame('ifrArvore')
        try:
            frame.locator('xpath=//*[@title="Abrir todas as Pastas"]').click(timeout=5000)
            if self._debug:
                try:
                    frame.keyboard.press('Enter')
                except Exception:
                    pass
        except Exception:
            pass

    def get_cookies(self):
        cookies = self._page.context.cookies()
        return {cookie['name']: cookie['value'] for cookie in cookies}

    def get_oficio(self):
        self.wait()
        self.abrir_todas_pastas()
        time.sleep(3)
        self.wait()
        frame = self.get_frame('ifrArvore')
        anexos = frame.locator("xpath=//a[@target='ifrConteudoVisualizacao']")
        count = anexos.count()
        for i in range(count):
            anexo = anexos.nth(i)
            text = anexo.text_content()
            if "CANCELADO" not in (text or '') and (text or '').startswith("Ofício"):
                anexo.click()
                if self._debug:
                    try:
                        frame.keyboard.press('Enter')
                    except Exception:
                        pass
                break
        self.wait()
        frame = self.get_frame('ifrConteudoVisualizacao')
        try:
            url = frame.evaluate("document.getElementById('ifrArvoreHtml').src")
        except Exception:
            return
        response = requests.get(url, cookies=self.get_cookies())
        if response.status_code == 200:
            try:
                doc = pymupdf.open(stream=response.content, filetype='pdf')
            except Exception:
                return
            return doc


# ==============================================================================
# Parser
# ==============================================================================
def _make_aware(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def parse_date(value: str):
    try:
        dt = date_parse(value, dayfirst=True)
        return _make_aware(dt)
    except Exception:
        return None


def get_especificacao(soup: BeautifulSoup) -> str:
    el = soup.select_one('#txtDescricao')
    return el.get('value') if el else ''


def get_tipo_procedimento(soup: BeautifulSoup) -> Dict[str, Any]:
    option = soup.select_one('#selTipoProcedimento option[selected]')
    if not option:
        option = soup.select_one('#selTipoProcedimento option:checked')
    if option:
        return {"id": option.get('value'), "nome": option.text.strip()}
    return {"id": None, "nome": None}


def get_assunto(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    return [{"id": opt.get('value'), "nome": opt.text.strip()} for opt in soup.select('#selAssuntos option')]


def get_interessados(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    return [{"id": opt.get('value'), "nome": opt.text.strip()} for opt in soup.select('#selInteressadosProcedimento option')]


def get_observacoes(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    observacoes = []
    linhas = soup.select('table.infraTable tbody tr.infraTrClara')
    for linha in linhas:
        colunas = linha.select('td')
        if len(colunas) >= 2:
            unidade_el = colunas[0].select_one('.ancoraSigla')
            unidade = unidade_el.text.strip() if unidade_el else ''
            observacao = colunas[1].text.strip()
            observacoes.append({"unidade": unidade, "observacao": observacao})
    return observacoes


def get_anexos(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    anexos = []
    el_anexos = soup.select('.infraArvore a[target="ifrConteudoVisualizacao"]')
    for el_anexo in el_anexos[1:]:
        nome_anexo = el_anexo.text.strip()
        sibling = el_anexo.find_next_sibling()
        nome_unidade = sibling.text.strip() if sibling else ''
        anexos.append({"nome": nome_anexo, "unidade": nome_unidade})
    return anexos


def parse_metadata_from_frames(ifr_visualizacao_content: str, ifr_arvore_content: str, numero_processo: str) -> Dict[str, Any]:
    soup_vis = BeautifulSoup(ifr_visualizacao_content, 'html.parser')
    soup_arv = BeautifulSoup(ifr_arvore_content, 'html.parser')

    especificacao = get_especificacao(soup_vis)
    tipo_procedimento = get_tipo_procedimento(soup_vis)
    assunto = get_assunto(soup_vis)
    interessados = get_interessados(soup_vis)
    observacoes = get_observacoes(soup_vis)
    anexos = get_anexos(soup_arv)

    return {
        'protocolo': numero_processo,
        'especificacao': especificacao,
        'tipo_procedimento': tipo_procedimento,
        'assunto': assunto,
        'interessados': interessados,
        'observacoes': observacoes,
        'anexos': anexos,
    }


# ==============================================================================
# Core (orquestrador SEI)
# ==============================================================================
class SEI:
    """Orchestrator class for SEI scraping.

    Public API: `do_login()`, `search()`, `get_metadados()`, `get_historico()`,
    `save_pdf()` e `save_zip()`.
    """

    def __init__(self, url: str, usuario: str, senha: str, playwright: Optional[Playwright] = None, *args, **kwargs):
        headless = kwargs.get("headless", True)
        self._debug = kwargs.get("debug", False)
        self.url = url
        self.username = usuario
        self.password = senha
        self._external_playwright = playwright is not None
        self._p: Optional[Playwright] = playwright
        self._browser = None
        self._page: Optional[Page] = None
        self._headless = headless
        self._dumper = DebugDumper()
        self._navigator: Optional[Navigator] = None

        if self._external_playwright:
            self._browser = self._p.chromium.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    def _ensure_browser(self):
        if self._page:
            return
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(headless=self._headless, args=["--no-sandbox"])
        self._page = self._browser.new_page()
        self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if not self._external_playwright and self._p:
                try:
                    self._p.stop()
                except Exception:
                    pass

    def do_login(self):
        """Log in to SEI using site selectors."""
        self._ensure_browser()
        self._page.goto(self.url)
        self._page.wait_for_load_state("load")

        try:
            self._page.locator('xpath=//*[@id="selOrgao"]').select_option(value="MS")
        except Exception:
            pass

        self._page.locator('xpath=//*[@id="txtUsuario"]').fill(self.username)
        self._page.locator('xpath=//*[@id="pwdSenha"]').fill(self.password)

        clicked = False
        for sel in ['#sbmAcessar', '#Acessar', 'xpath=//*[@id="Acessar"]']:
            try:
                self._page.locator(sel).wait_for(state='visible', timeout=5000)
                self._page.locator(sel).click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            try:
                self._page.locator('xpath=//*[@id="pwdSenha"]').press('Enter')
            except Exception:
                raise

        self._page.wait_for_load_state("load")
        try:
            self._page.wait_for_selector(".sparkling-modal-title", timeout=5000)
            self._page.press("body", "Escape")
        except Exception:
            pass

    def change_inbox(self, position_number: int = 2):
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        self._navigator.page.locator(f'xpath=//*[@id="divInfraBarraSistemaPadraoD"]/div[{position_number}]/div').click()
        if self._debug:
            try:
                self._page.keyboard.press('Enter')
            except Exception:
                pass
        try:
            self._page.locator('xpath=//*[@id="divInfraAreaTabela"]/table/tbody/tr[4]/td[1]/div/label').click()
            if self._debug:
                try:
                    self._page.keyboard.press('Enter')
                except Exception:
                    pass
        except Exception:
            pass

    @staticmethod
    def _normalize_unidade_text(text: str) -> str:
        # O SEI costuma separar a sigla do órgão com um espaço não separável
        # (\xa0); normaliza para espaço comum antes de comparar.
        return ' '.join((text or '').replace('\xa0', ' ').split()).strip().lower()

    def select_unidade(self, sigla: str) -> bool:
        """Troca a unidade ativa no SEI para a unidade cuja sigla contenha `sigla`.

        O SEI não usa um <select> para isso: o cabeçalho tem um link
        (#lnkInfraUnidade, duplicado no DOM para mobile/desktop) mostrando a
        sigla da unidade atual; clicar nele abre uma tela com uma lista de
        rádios (um por unidade do usuário), cada um com o atributo
        title=<sigla> e um <label> visualmente por cima do <input> (então o
        clique precisa ser no label, não no input).
        """
        if not sigla:
            return False

        try:
            link = self._page.locator('#lnkInfraUnidade:visible').first
            link.wait_for(state='visible', timeout=5000)
        except Exception:
            # Não encontrar esse link é raro; provavelmente a conta só tem
            # uma unidade e o SEI nem mostra a opção de trocar.
            logger.info(
                f"Link de troca de unidade (#lnkInfraUnidade) não apareceu; "
                f"prosseguindo sem trocar para '{sigla}'."
            )
            return True

        unidade_atual = (link.text_content() or '').strip()
        alvo = self._normalize_unidade_text(sigla)
        atual_norm = self._normalize_unidade_text(unidade_atual)
        if alvo in atual_norm or atual_norm in alvo:
            logger.info(f"Unidade '{unidade_atual}' já é a unidade ativa.")
            return True

        link.click()
        self._page.wait_for_load_state('load')

        labels = self._page.locator('label.infraRadioLabel')
        disponiveis = []
        for i in range(labels.count()):
            label = labels.nth(i)
            texto = (label.get_attribute('title') or '').strip()
            texto_norm = self._normalize_unidade_text(texto)
            disponiveis.append(texto)
            if alvo in texto_norm or texto_norm in alvo:
                label.click()
                self._page.wait_for_load_state('load')
                logger.info(f"Unidade alterada para '{texto}'")
                return True

        logger.warning(
            f"Unidade '{sigla}' não corresponde a nenhuma opção. Unidades disponíveis: {', '.join(disponiveis) or '(nenhuma)'}"
        )
        return False

    def search(self, value: str):
        self._ensure_browser()
        self._page.locator('xpath=//*[@id="txtPesquisaRapida"]').fill(value)
        self._page.locator('#spnInfraUnidade').click()
        if self._debug:
            try:
                self._page.keyboard.press('Enter')
            except Exception:
                pass
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        self._navigator.click_consultar_processo()

    def get_metadados(self, numero_processo: str) -> dict:
        self._ensure_browser()
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        self._navigator.wait()

        frame = self._navigator.get_frame('ifrVisualizacao')
        soup = BeautifulSoup(frame.content(), 'html.parser')

        especificacao = get_especificacao(soup)
        tipo_procedimento = get_tipo_procedimento(soup)
        assunto = get_assunto(soup)
        interessados = get_interessados(soup)
        observacoes = get_observacoes(soup)

        self._navigator.abrir_todas_pastas()
        self._navigator.wait()

        frame_arvore = self._navigator.get_frame('ifrArvore')
        soup_arvore = BeautifulSoup(frame_arvore.content(), 'html.parser')
        anexos = get_anexos(soup_arvore)

        return {
            'protocolo': numero_processo,
            'especificacao': especificacao,
            'tipo_procedimento': tipo_procedimento,
            'assunto': assunto,
            'interessados': interessados,
            'observacoes': observacoes,
            'anexos': anexos,
        }

    def get_historico(self):
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        try:
            self._navigator.get_frame('ifrArvore').locator('xpath=//*[@id="divConsultarAndamento"]').click()
        except Exception:
            pass
        self._page.wait_for_selector('#ifrConteudoVisualizacao')
        frame = self._navigator.get_frame('ifrConteudoVisualizacao')
        self._navigator.wait()
        try:
            el_historico = frame.frame_locator('iframe#ifrVisualizacao').locator('#ancTipoHistorico')
        except Exception:
            return []

        try:
            if el_historico.inner_text() == 'Ver histórico completo':
                el_historico.click()
        except Exception:
            return []

        data = []

        while True:
            frame = self._navigator.get_frame('ifrConteudoVisualizacao')
            frame.wait_for_load_state('load')
            try:
                table_locator = frame.frame_locator('iframe#ifrVisualizacao').locator('#tblHistorico')
                soup = BeautifulSoup(table_locator.inner_html(), 'html.parser')
            except Exception:
                break

            if not soup:
                break

            tbody = soup.find('tbody')
            trs = tbody.find_all('tr')[1:]
            for tr in trs:
                tds = tr.find_all('td')

                if len(tds) < 4:
                    continue
                sei_id_el = tr.previous_element.previous_element
                sei_id = sei_id_el.strip() if sei_id_el else ''
                created = parse_date(tds[0].text.strip())
                data.append({
                    'sei_id': sei_id,
                    'criado_em': created,
                    'unidade': tds[1].text.strip(),
                    'usuario': tds[2].text.strip(),
                    'descricao': tds[3].text.strip(),
                })

            frame_visualizacao = self._navigator.get_frame('ifrVisualizacao')
            el_next_pg = frame_visualizacao.query_selector('[title="Próxima Página"]')

            if el_next_pg:
                el_next_pg.click()
            else:
                break

        df = pd.DataFrame(data)
        df.drop_duplicates(inplace=True)
        return df.to_dict(orient='records')

    def __save_file(self, file_name: Path, selector: str):
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        frame = self._navigator.get_frame('ifrConteudoVisualizacao')
        frame.locator(selector).click()
        if self._debug:
            try:
                self._page.keyboard.press('Enter')
            except Exception:
                pass

        # O link "Gerar Arquivo PDF/ZIP do Processo" tem target="ifrVisualizacao":
        # carrega a tela de confirmação (botão "Gerar") dentro desse iframe
        # aninhado, não na própria página nem numa aba nova. get_frame busca
        # por nome em toda a página, então acha mesmo estando aninhado.
        try:
            visualizacao = self._navigator.get_frame('ifrVisualizacao')
            with self._page.expect_download() as download_info:
                visualizacao.locator('xpath=//*[@name="btnGerar"]').click()
            download = download_info.value
            download.save_as(file_name)
        except Exception:
            try:
                self._dumper.dump(self._page, 'gerar_arquivo_falhou')
            except Exception:
                pass
            raise

    def save_pdf(self, file_name: Path):
        return self.__save_file(file_name, 'xpath=//*[@title="Gerar Arquivo PDF do Processo"]')

    def save_zip(self, file_name: Path):
        return self.__save_file(file_name, 'xpath=//*[@title="Gerar Arquivo ZIP do Processo"]')


# ==============================================================================
# Extractor
# ==============================================================================
class SEIExtractor:
    def __init__(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        url: Optional[str] = None,
        user: Optional[str] = None,
        passw: Optional[str] = None,
        sigla: Optional[str] = None,
        save_pdf_files: bool = False,
        save_zip_files: bool = False,
        output_dir: Optional[str] = None,
    ):
        self.url = url or SEI_URL
        self.user = user or SEI_USERNAME
        self.passw = passw or SEI_PASSWORD
        self.sigla = sigla or ""
        self.save_pdf_files = save_pdf_files
        self.save_zip_files = save_zip_files

        self.output_dir = output_dir or OUTPUT_DIR
        self.json_dir = os.path.join(self.output_dir, "json")
        self.pdf_dir = os.path.join(self.output_dir, "pdfs")
        self.zip_dir = os.path.join(self.output_dir, "zips")
        self.metadata_csv = os.path.join(self.output_dir, "metadata.csv")
        os.makedirs(self.json_dir, exist_ok=True)
        os.makedirs(self.pdf_dir, exist_ok=True)
        os.makedirs(self.zip_dir, exist_ok=True)

        self.progress = None
        self.overall_task = None
        self.lock = threading.Lock()
        self.work_queue: Optional[queue.Queue] = None
        self.thread_tasks: Dict[int, Any] = {}
        self.on_progress = on_progress

    @staticmethod
    def clean_process_number(number) -> str:
        """Remove formatação do número do processo."""
        return number.replace('/', '').replace('.', '').replace('-', '')

    def run(self, process_numbers):
        """Processa usando uma fila compartilhada - threads pegam trabalho dinamicamente (work stealing)."""
        self.work_queue = queue.Queue()

        for process_num in process_numbers:
            output_file = Path(self.json_dir, f"{self.clean_process_number(process_num)}.json")

            if not output_file.exists():
                self.work_queue.put(process_num)

        # Não abre mais threads do que processos pendentes: buscar 1 processo
        # não precisa logar 10 vezes no SEI.
        num_threads = max(1, min(THREADS, self.work_queue.qsize()))
        logger.info(f"Iniciando extração de {len(process_numbers)} processo(s) com {num_threads} thread(s)")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            refresh_per_second=4,
            transient=False,
        ) as progress:
            self.progress = progress

            self.overall_task = progress.add_task("[cyan]Total Progress", total=0)

            for i in range(num_threads):
                thread_task = progress.add_task(
                    f"[green]Thread {i + 1}",
                    total=0,
                    visible=False,
                )
                self.thread_tasks[i] = thread_task

            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [
                    executor.submit(self._process_from_queue, i)
                    for i in range(num_threads)
                ]
                for future in futures:
                    future.result()

        logger.info("Extração concluída")

    def _process_from_queue(self, thread_index):
        """Thread worker que consome processos da fila compartilhada até ela estar vazia."""
        thread_task = self.thread_tasks[thread_index]
        processed_count = 0

        with sync_playwright() as p:
            sei = SEI(self.url, self.user, self.passw, p, headless=HEADLESS)
            try:
                logger.info(f"[Thread {thread_index + 1}] Abrindo navegador e fazendo login em {self.url}...")
                sei.do_login()
                logger.info(f"[Thread {thread_index + 1}] Login efetuado")

                if self.sigla:
                    # select_unidade já loga o resultado (trocou, não precisou
                    # trocar, ou não encontrou a unidade), sem precisar duplicar aqui.
                    sei.select_unidade(self.sigla)

                with self.lock:
                    self.progress.update(thread_task, visible=True)

                while True:
                    try:
                        process_number = self.work_queue.get(timeout=1)

                        with self.lock:
                            current_total_thread = self.progress.tasks[thread_task].total
                            current_total_overall = self.progress.tasks[self.overall_task].total
                            self.progress.update(thread_task, total=current_total_thread + 1)
                            self.progress.update(self.overall_task, total=current_total_overall + 1)

                        success = self.process(process_number, sei)
                        processed_count += 1

                        if success:
                            with self.lock:
                                self.progress.update(thread_task, advance=1)
                                self.progress.update(self.overall_task, advance=1)
                                completed = self.progress.tasks[self.overall_task].completed
                                total = self.progress.tasks[self.overall_task].total

                            if self.on_progress:
                                try:
                                    self.on_progress(completed, total)
                                except Exception:
                                    pass

                        self.work_queue.task_done()

                    except queue.Empty:
                        break

            finally:
                sei.close()

                if processed_count > 0:
                    with self.lock:
                        self.progress.update(
                            thread_task,
                            description=f"[dim green]Thread {thread_index + 1} (✓ {processed_count})"
                        )

    def process(self, process_number, sei):
        max_retries = 3
        delay = 1
        attempt = 0
        output_file = Path(self.json_dir, f"{self.clean_process_number(process_number)}.json")

        while attempt < max_retries:
            attempt += 1
            try:
                logger.info(f"Buscando processo {process_number}...")
                sei.search(process_number)
                metadata = sei.get_metadados(process_number)

                if self.save_pdf_files:
                    try:
                        pdf_path = Path(self.pdf_dir, f"{self.clean_process_number(process_number)}.pdf")
                        sei.save_pdf(pdf_path)
                        logger.info(f"PDF salvo: {pdf_path}")
                    except Exception as e:
                        logger.warning(f"Falha ao salvar PDF de {process_number}: {e}")

                if self.save_zip_files:
                    try:
                        zip_path = Path(self.zip_dir, f"{self.clean_process_number(process_number)}.zip")
                        sei.save_zip(zip_path)
                        logger.info(f"ZIP salvo: {zip_path}")
                    except Exception as e:
                        logger.warning(f"Falha ao salvar ZIP de {process_number}: {e}")

                historico_andamento = sei.get_historico()
                metadata['historico_andamento'] = historico_andamento

                with open(output_file, "w") as f:
                    json.dump(metadata, f, indent=4, default=str)

                self._append_metadata_csv(metadata)

                logger.info(f"Processo {process_number} extraído com sucesso")
                return True
            except Exception as e:
                logger.warning(f"Tentativa {attempt}/{max_retries} falhou para {process_number}: {e}")
                time.sleep(delay)
                delay *= 2

        logger.error(f"Falha ao extrair {process_number} após {max_retries} tentativas")
        return False

    def _append_metadata_csv(self, metadata: dict):
        protocolo = metadata.get('protocolo')
        tipo = metadata.get('tipo_procedimento', {}).get('nome') if metadata.get('tipo_procedimento') else ''
        assunto_count = len(metadata.get('assunto') or [])
        interessados_count = len(metadata.get('interessados') or [])
        row = [protocolo, tipo, assunto_count, interessados_count]

        write_header = not os.path.exists(self.metadata_csv)
        with open(self.metadata_csv, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            if write_header:
                writer.writerow(['protocolo', 'tipo_procedimento', 'assunto_count', 'interessados_count'])
            writer.writerow(row)


# ==============================================================================
# Services: conversão JSON -> CSV
# ==============================================================================
class JSONToCSVService:
    """Service para converter JSONs do SEI em CSV consolidado usando pandas."""

    def __init__(self):
        self.errors = []

    def flatten_list_field(self, items: List[Dict], prefix: str) -> Dict[str, Any]:
        """Achata uma lista de objetos em campos separados, mantendo todos os dados."""
        result = {}
        if not items:
            result[f"{prefix}_count"] = 0
            result[f"{prefix}_completo_json"] = '[]'
            return result

        result[f"{prefix}_count"] = len(items)

        if all(isinstance(item, dict) for item in items):
            names = []
            ids = []
            for item in items:
                if 'nome' in item:
                    names.append(str(item['nome']))
                if 'id' in item:
                    ids.append(str(item['id']))

            if names:
                result[f"{prefix}_nomes"] = " | ".join(names)
            if ids:
                result[f"{prefix}_ids"] = " | ".join(ids)
        else:
            result[f"{prefix}_valores"] = " | ".join(str(item) for item in items)

        result[f"{prefix}_completo_json"] = json.dumps(items, ensure_ascii=False)

        return result

    def process_historico(self, historico: List[Dict]) -> Dict[str, Any]:
        """Processa o histórico de andamento - mantém todos os dados."""
        if not historico:
            return {
                'historico_count': 0,
                'historico_primeiro_evento': '',
                'historico_ultimo_evento': '',
                'historico_data_primeiro': '',
                'historico_data_ultimo': '',
                'historico_unidades_envolvidas': '',
                'historico_completo_json': '[]'
            }

        historico_sorted = sorted(
            historico,
            key=lambda x: x.get('criado_em', ''),
            reverse=True
        )

        unidades = list(set(h.get('unidade', '') for h in historico if h.get('unidade')))

        return {
            'historico_count': len(historico),
            'historico_primeiro_evento': historico_sorted[-1].get('descricao', '')[:200] if historico_sorted else '',
            'historico_ultimo_evento': historico_sorted[0].get('descricao', '')[:200] if historico_sorted else '',
            'historico_data_primeiro': historico_sorted[-1].get('criado_em', '') if historico_sorted else '',
            'historico_data_ultimo': historico_sorted[0].get('criado_em', '') if historico_sorted else '',
            'historico_unidades_envolvidas': " | ".join(sorted(unidades)),
            'historico_completo_json': json.dumps(historico, ensure_ascii=False)
        }

    def process_observacoes(self, observacoes: List[Dict]) -> Dict[str, Any]:
        """Processa observações - mantém todos os dados."""
        if not observacoes:
            return {
                'observacoes_count': 0,
                'observacoes_texto': '',
                'observacoes_completo_json': '[]'
            }

        textos = []
        for obs in observacoes:
            unidade = obs.get('unidade', 'N/A')
            texto = obs.get('observacao', '')
            textos.append(f"[{unidade}] {texto}")

        return {
            'observacoes_count': len(observacoes),
            'observacoes_texto': " | ".join(textos),
            'observacoes_completo_json': json.dumps(observacoes, ensure_ascii=False)
        }

    def process_anexos(self, anexos: List[Dict]) -> Dict[str, Any]:
        """Processa anexos - mantém todos os dados."""
        if not anexos:
            return {
                'anexos_count': 0,
                'anexos_nomes': '',
                'anexos_completo_json': '[]'
            }

        nomes = [f"{a.get('nome', 'N/A')} ({a.get('unidade', 'N/A')})" for a in anexos]

        return {
            'anexos_count': len(anexos),
            'anexos_nomes': " | ".join(nomes),
            'anexos_completo_json': json.dumps(anexos, ensure_ascii=False)
        }

    def json_to_flat_dict(self, data: Dict) -> Dict[str, Any]:
        """Converte JSON complexo em dicionário plano para DataFrame."""
        flat = {}

        flat['protocolo'] = data.get('protocolo', '')
        flat['especificacao'] = data.get('especificacao', '')

        tipo = data.get('tipo_procedimento', {})
        if tipo:
            flat['tipo_procedimento_id'] = tipo.get('id', '')
            flat['tipo_procedimento_nome'] = tipo.get('nome', '')
            flat['tipo_procedimento_completo_json'] = json.dumps(tipo, ensure_ascii=False)
        else:
            flat['tipo_procedimento_id'] = ''
            flat['tipo_procedimento_nome'] = ''
            flat['tipo_procedimento_completo_json'] = '{}'

        assuntos = data.get('assunto', [])
        flat.update(self.flatten_list_field(assuntos, 'assunto'))

        interessados = data.get('interessados', [])
        flat.update(self.flatten_list_field(interessados, 'interessado'))

        flat.update(self.process_observacoes(data.get('observacoes', [])))
        flat.update(self.process_anexos(data.get('anexos', [])))
        flat.update(self.process_historico(data.get('historico_andamento', [])))

        return flat

    def convert_jsons_to_dataframe(
        self,
        json_dir: Path,
        show_progress: bool = True
    ) -> Optional[pd.DataFrame]:
        """Converte todos os JSONs de um diretório para um DataFrame pandas."""
        json_files = list(json_dir.glob('*.json'))

        if not json_files:
            console.print(f"[red]❌ Nenhum arquivo JSON encontrado em: {json_dir}[/red]")
            return None

        all_rows = []
        self.errors = []

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                task = progress.add_task(
                    f"[cyan]Processando {len(json_files)} arquivos JSON...",
                    total=len(json_files)
                )

                for json_file in json_files:
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                        flat_data = self.json_to_flat_dict(data)
                        all_rows.append(flat_data)

                    except Exception as e:
                        self.errors.append(f"{json_file.name}: {str(e)}")

                    progress.update(task, advance=1)
        else:
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    flat_data = self.json_to_flat_dict(data)
                    all_rows.append(flat_data)

                except Exception as e:
                    self.errors.append(f"{json_file.name}: {str(e)}")

        if not all_rows:
            console.print("[red]❌ Nenhum dado válido encontrado[/red]")
            return None

        df = pd.DataFrame(all_rows)

        ordered_cols = self._order_columns(df.columns.tolist())
        df = df[ordered_cols]

        return df

    def _order_columns(self, columns: List[str]) -> List[str]:
        """Ordena as colunas de forma lógica."""
        ordered = []
        remaining = set(columns)

        main_fields = ['protocolo', 'especificacao', 'tipo_procedimento_id', 'tipo_procedimento_nome']
        for field in main_fields:
            if field in remaining:
                ordered.append(field)
                remaining.remove(field)

        for prefix in ['assunto', 'interessado', 'observacoes', 'anexos', 'historico']:
            prefix_cols = sorted([c for c in remaining if c.startswith(prefix)])
            ordered.extend(prefix_cols)
            remaining -= set(prefix_cols)

        ordered.extend(sorted(remaining))

        return ordered

    def save_to_csv(
        self,
        df: pd.DataFrame,
        output_path: Path,
        show_progress: bool = True
    ) -> bool:
        """Salva DataFrame em CSV."""
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if show_progress:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console
                ) as progress:
                    task = progress.add_task("[cyan]Salvando CSV...", total=None)
                    df.to_csv(output_path, index=False, encoding='utf-8-sig')
                    progress.update(task, completed=True)
            else:
                df.to_csv(output_path, index=False, encoding='utf-8-sig')

            return True

        except Exception as e:
            console.print(f"[red]❌ Erro ao salvar CSV: {e}[/red]")
            return False

    def convert_and_save(
        self,
        json_dir: Path,
        output_csv: Path,
        show_progress: bool = True
    ) -> bool:
        """Converte JSONs para CSV em um único processo."""
        df = self.convert_jsons_to_dataframe(json_dir, show_progress=show_progress)

        if df is None:
            return False

        success = self.save_to_csv(df, output_csv, show_progress=show_progress)

        if success:
            console.print(f"\n[green]✅ CSV criado com sucesso: {output_csv}[/green]")
            console.print(f"[green]📊 Total de processos: {len(df)}[/green]")
            console.print(f"[green]📋 Total de colunas: {len(df.columns)}[/green]")

            if self.errors:
                console.print(f"\n[yellow]⚠️  Erros encontrados ({len(self.errors)}):[/yellow]")
                for error in self.errors[:10]:
                    console.print(f"  [dim]- {error}[/dim]")
                if len(self.errors) > 10:
                    console.print(f"  [dim]... e mais {len(self.errors) - 10} erros[/dim]")

        return success


# ==============================================================================
# GUI (CustomTkinter)
# ==============================================================================
C_BG = "#F4F5F7"
C_SURFACE = "#FFFFFF"
C_ACCENT = "#1351B4"
C_ACCENT_HOVER = "#0C3F8E"
C_GREEN = "#168821"
C_YELLOW = "#FFCD07"
C_RED = "#C0392B"
C_LOG_BG = "#13141A"
C_LOG_FG = "#D7DDE6"
C_TEXT = "#1C1C1C"
C_MUTED = "#5B6472"
C_BORDER = "#DADEE3"

LOG_FONT = ("Consolas", 11)

# Colunas reconhecidas como "número do processo" em um CSV de NUPs
NUP_COLUMN_NAMES = {"nup", "nups", "processo", "processos", "protocolo", "numero_processo", "numero"}


class _QueueLogHandler(logging.Handler):
    """Handler que empilha registros de log numa fila thread-safe."""

    def __init__(self, log_queue: "queue.Queue[str]"):
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put(self.format(record))
        except Exception:
            pass


def load_nups_from_csv(path: str) -> List[str]:
    """Lê um CSV e devolve a lista de NUPs.

    Procura uma coluna com nome reconhecido (nup/processo/protocolo/...);
    se não encontrar cabeçalho compatível, assume que a 1ª coluna de cada
    linha já é o número do processo (CSV sem cabeçalho).
    """
    nups: List[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        delimiter = next((d for d in (";", "\t", ",") if d in first_line), ",")

        reader = csv.reader(f, delimiter=delimiter)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

    if not rows:
        return nups

    header = [cell.strip().lower() for cell in rows[0]]
    col_index = next((i for i, name in enumerate(header) if name in NUP_COLUMN_NAMES), None)

    data_rows = rows[1:] if col_index is not None else rows
    index = col_index if col_index is not None else 0

    for row in data_rows:
        if index < len(row):
            value = row[index].strip()
            if value:
                nups.append(value)

    return nups


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title("SEI Extractor")
        self.geometry("980x760")
        self.minsize(860, 640)
        self.configure(fg_color=C_BG)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # header
        self.grid_rowconfigure(1, weight=3)  # abas
        self.grid_rowconfigure(2, weight=1)  # console de log

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._busy_widgets: list = []
        self._busy = False
        self._loaded_nups: List[str] = []

        self._build_header()
        self._build_body()
        self._build_log_console()
        self._setup_logging()

        self.after(150, self._poll_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Layout geral
    # ------------------------------------------------------------------
    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=C_ACCENT, corner_radius=0, height=72)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)

        ctk.CTkLabel(
            header,
            text="SEI Extractor",
            text_color="white",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(side="left", padx=(20, 6), pady=10)

        ctk.CTkLabel(
            header,
            text="Painel de extração de processos SEI",
            text_color="#D6E3FA",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=6, pady=10)

        self.status_label = ctk.CTkLabel(
            header,
            text="Pronto",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.status_label.pack(side="right", padx=20)

    def _build_body(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=C_SURFACE,
            segmented_button_fg_color=C_BG,
            segmented_button_selected_color=C_ACCENT,
            segmented_button_selected_hover_color=C_ACCENT_HOVER,
            segmented_button_unselected_color=C_BG,
            text_color=C_TEXT,
        )
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=14, pady=(12, 8))

        tab_extracao = self.tabview.add("Extração")
        self._build_extracao_tab(tab_extracao)

    def _build_log_console(self):
        wrapper = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=8)
        wrapper.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))

        top = ctk.CTkFrame(wrapper, fg_color="transparent")
        top.pack(side="top", fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            top, text="Console", font=ctk.CTkFont(size=13, weight="bold"), text_color=C_TEXT
        ).pack(side="left")
        ctk.CTkButton(
            top,
            text="Limpar",
            width=80,
            height=24,
            fg_color=C_BG,
            text_color=C_TEXT,
            hover_color=C_BORDER,
            command=self._clear_log,
        ).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            wrapper,
            height=160,
            fg_color=C_LOG_BG,
            text_color=C_LOG_FG,
            font=LOG_FONT,
            wrap="word",
        )
        self.log_box.pack(side="bottom", fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _setup_logging(self):
        gui_logger = logging.getLogger("sei_extractor")
        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        gui_logger.addHandler(handler)
        gui_logger.setLevel(logging.INFO)
        self._gui_logger = gui_logger

    def _poll_log_queue(self):
        while True:
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(line)
        self.after(150, self._poll_log_queue)

    def _append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def log(self, message: str, level: int = logging.INFO):
        self._gui_logger.log(level, message)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Controle de execução (uma ação de background por vez)
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool, message: str = "Pronto"):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            try:
                widget.configure(state=state)
            except Exception:
                pass
        self.status_label.configure(text=message, text_color=C_YELLOW if busy else "white")

    def run_background(self, target: Callable, busy_message: str, done_message: str = "Pronto"):
        if self._busy:
            messagebox.showwarning("SEI Extractor", "Já existe uma operação em execução. Aguarde terminar.")
            return

        def _wrapper():
            self.after(0, lambda: self._set_busy(True, busy_message))
            try:
                target()
            except Exception as exc:
                self.log(f"Erro inesperado: {exc}", level=logging.ERROR)
            finally:
                self.after(0, lambda: self._set_busy(False, done_message))

        threading.Thread(target=_wrapper, daemon=True).start()

    def _on_close(self):
        if self._busy and not messagebox.askyesno(
            "SEI Extractor", "Uma operação ainda está em execução. Fechar mesmo assim?"
        ):
            return
        self.destroy()

    # ------------------------------------------------------------------
    # Aba: Extração
    # ------------------------------------------------------------------
    def _build_extracao_tab(self, tab):
        # Aba rolável: garante que todas as seções (inclusive o botão de
        # executar, no fim) fiquem acessíveis mesmo se a janela for pequena
        # demais para exibir tudo de uma vez.
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        self._build_credentials_section(scroll)
        self._build_nups_section(scroll)
        self._build_run_section(scroll)

    def _build_credentials_section(self, tab):
        card = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=8, border_width=1, border_color=C_BORDER)
        card.pack(fill="x", padx=14, pady=(12, 8))

        ctk.CTkLabel(
            card, text="Credenciais do SEI", font=ctk.CTkFont(size=14, weight="bold"), text_color=C_TEXT
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        ctk.CTkLabel(card, text="URL do SEI:").grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.url_entry = ctk.CTkEntry(card, width=320)
        self.url_entry.insert(0, SEI_URL)
        self.url_entry.configure(state="disabled")
        self.url_entry.grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(card, text="Usuário:").grid(row=2, column=0, sticky="w", padx=14, pady=6)
        self.user_entry = ctk.CTkEntry(card, width=320)
        self.user_entry.insert(0, SEI_USERNAME or "")
        self.user_entry.grid(row=2, column=1, sticky="w", padx=8, pady=6)

        ctk.CTkLabel(card, text="Senha:").grid(row=3, column=0, sticky="w", padx=14, pady=6)
        self.password_entry = ctk.CTkEntry(card, width=320, show="*")
        self.password_entry.insert(0, SEI_PASSWORD or "")
        self.password_entry.grid(row=3, column=1, sticky="w", padx=8, pady=6)

        self.show_password_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            card,
            text="Mostrar senha",
            variable=self.show_password_var,
            command=self._toggle_password_visibility,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=3, column=2, sticky="w", padx=8)

        ctk.CTkLabel(card, text="Sigla da unidade:").grid(row=4, column=0, sticky="w", padx=14, pady=(6, 14))
        self.sigla_entry = ctk.CTkEntry(card, width=320, placeholder_text="ex.: SE/MS")
        self.sigla_entry.grid(row=4, column=1, sticky="w", padx=8, pady=(6, 14))

        # url_entry fica de fora: é somente leitura (URL do SEI é fixa) e não
        # deve ser reabilitada pelo controle de "ocupado" (_busy_widgets).
        for widget in (self.user_entry, self.password_entry, self.sigla_entry):
            self._busy_widgets.append(widget)

    def _toggle_password_visibility(self):
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _build_nups_section(self, tab):
        card = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=8, border_width=1, border_color=C_BORDER)
        card.pack(fill="both", expand=True, padx=14, pady=8)

        ctk.CTkLabel(
            card, text="Processos (NUPs)", font=ctk.CTkFont(size=14, weight="bold"), text_color=C_TEXT
        ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            card,
            text="Carregue um ou mais arquivos CSV com a lista de NUPs a extrair.",
            font=ctk.CTkFont(size=12),
            text_color=C_MUTED,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14)

        load_btn = ctk.CTkButton(
            row, text="Selecionar CSV...", fg_color=C_ACCENT, hover_color=C_ACCENT_HOVER,
            command=self._pick_csv_files,
        )
        load_btn.pack(side="left")
        self._busy_widgets.append(load_btn)

        clear_btn = ctk.CTkButton(
            row, text="Limpar lista", fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
            command=self._clear_nups,
        )
        clear_btn.pack(side="left", padx=8)
        self._busy_widgets.append(clear_btn)

        self.nups_summary_label = ctk.CTkLabel(row, text="Nenhum processo carregado", text_color=C_MUTED)
        self.nups_summary_label.pack(side="left", padx=12)

        ctk.CTkLabel(card, text="ou digite um único NUP:", font=ctk.CTkFont(size=12), text_color=C_MUTED).pack(
            anchor="w", padx=14, pady=(10, 2)
        )
        self.single_nup_entry = ctk.CTkEntry(card, width=280, placeholder_text="25000.123456/2023-45")
        self.single_nup_entry.pack(anchor="w", padx=14, pady=(0, 8))
        self._busy_widgets.append(self.single_nup_entry)

        self.nups_preview = ctk.CTkTextbox(card, fg_color=C_BG, text_color=C_TEXT, height=120, wrap="none")
        self.nups_preview.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.nups_preview.configure(state="disabled")

    def _pick_csv_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if not paths:
            return

        novos = []
        for path in paths:
            try:
                novos.extend(load_nups_from_csv(path))
            except Exception as exc:
                messagebox.showerror("SEI Extractor", f"Erro ao ler {Path(path).name}: {exc}")
                return

        if not novos:
            messagebox.showwarning("SEI Extractor", "Nenhum NUP encontrado nos arquivos selecionados.")
            return

        for nup in novos:
            if nup not in self._loaded_nups:
                self._loaded_nups.append(nup)

        self._refresh_nups_preview(len(paths))

    def _clear_nups(self):
        self._loaded_nups = []
        self._refresh_nups_preview(0)

    def _refresh_nups_preview(self, files_loaded: int):
        total = len(self._loaded_nups)
        if total:
            self.nups_summary_label.configure(text=f"{total} processo(s) carregado(s) de {files_loaded} arquivo(s)")
        else:
            self.nups_summary_label.configure(text="Nenhum processo carregado")

        self.nups_preview.configure(state="normal")
        self.nups_preview.delete("1.0", "end")
        self.nups_preview.insert("end", "\n".join(self._loaded_nups))
        self.nups_preview.configure(state="disabled")

    def _pick_output_dir(self):
        path = filedialog.askdirectory(title="Escolher pasta de saída")
        if path:
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, path)

    def _build_run_section(self, tab):
        card = ctk.CTkFrame(tab, fg_color="transparent")
        card.pack(fill="x", padx=14, pady=(0, 12))

        output_row = ctk.CTkFrame(card, fg_color="transparent")
        output_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(output_row, text="Pasta de saída:").pack(side="left")

        self.output_dir_entry = ctk.CTkEntry(output_row, width=380)
        self.output_dir_entry.insert(0, str(Path(OUTPUT_DIR).resolve()))
        self.output_dir_entry.pack(side="left", padx=8)
        self._busy_widgets.append(self.output_dir_entry)

        output_dir_btn = ctk.CTkButton(
            output_row, text="Escolher pasta...", fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
            command=self._pick_output_dir,
        )
        output_dir_btn.pack(side="left")
        self._busy_widgets.append(output_dir_btn)

        options_row = ctk.CTkFrame(card, fg_color="transparent")
        options_row.pack(anchor="w", pady=(0, 8))

        self.save_pdf_var = ctk.BooleanVar(value=False)
        pdf_check = ctk.CTkCheckBox(
            options_row, text="Salvar PDF do processo", variable=self.save_pdf_var,
            checkbox_width=18, checkbox_height=18,
        )
        pdf_check.pack(side="left", padx=(0, 16))
        self._busy_widgets.append(pdf_check)

        self.save_zip_var = ctk.BooleanVar(value=False)
        zip_check = ctk.CTkCheckBox(
            options_row, text="Salvar ZIP do processo", variable=self.save_zip_var,
            checkbox_width=18, checkbox_height=18,
        )
        zip_check.pack(side="left")
        self._busy_widgets.append(zip_check)

        run_btn = ctk.CTkButton(
            card, text="Executar extração", fg_color=C_GREEN, hover_color="#0F5C17",
            command=self._start_extraction,
        )
        run_btn.pack(anchor="w")
        self._busy_widgets.append(run_btn)

        self.extract_progress = ctk.CTkProgressBar(card, progress_color=C_ACCENT)
        self.extract_progress.set(0)
        self.extract_progress.pack(fill="x", pady=(10, 4))

        self.extract_progress_label = ctk.CTkLabel(card, text="0 / 0", text_color=C_MUTED)
        self.extract_progress_label.pack(anchor="w")

    def _start_extraction(self):
        # Log incondicional: garante que o clique sempre deixe rastro no console,
        # mesmo que a validação abaixo interrompa a extração.
        self.log("Botão 'Executar extração' clicado, validando dados...")

        try:
            self._do_start_extraction()
        except Exception:
            self.log(f"Erro ao iniciar extração:\n{traceback.format_exc()}", level=logging.ERROR)
            messagebox.showerror("SEI Extractor", "Erro inesperado ao iniciar a extração. Veja o console para detalhes.")

    def _do_start_extraction(self):
        single = self.single_nup_entry.get().strip()
        processes = list(self._loaded_nups)
        if single and single not in processes:
            processes.append(single)

        if not processes:
            self.log("Nenhum NUP informado (carregue um CSV ou digite um NUP).", level=logging.WARNING)
            messagebox.showwarning("SEI Extractor", "Carregue um CSV ou informe um NUP para extrair.")
            return

        url = self.url_entry.get().strip()
        user = self.user_entry.get().strip()
        passw = self.password_entry.get().strip()
        sigla = self.sigla_entry.get().strip()
        output_dir = self.output_dir_entry.get().strip()

        if not url or not user or not passw:
            self.log("URL, usuário ou senha do SEI não informados.", level=logging.WARNING)
            messagebox.showwarning("SEI Extractor", "Informe URL, usuário e senha do SEI.")
            return

        if not output_dir:
            self.log("Nenhuma pasta de saída informada.", level=logging.WARNING)
            messagebox.showwarning("SEI Extractor", "Informe ou escolha a pasta de saída.")
            return

        save_pdf_files = self.save_pdf_var.get()
        save_zip_files = self.save_zip_var.get()

        self.log(
            f"Iniciando extração de {len(processes)} processo(s)"
            + (f" na unidade '{sigla}'" if sigla else "")
            + (" [PDF]" if save_pdf_files else "")
            + (" [ZIP]" if save_zip_files else "")
            + f" em '{output_dir}'"
        )

        self.extract_progress.set(0)
        self.extract_progress_label.configure(text=f"0 / {len(processes)}")

        def _on_progress(completed: int, total: int):
            def _update():
                self.extract_progress.set((completed / total) if total else 0)
                self.extract_progress_label.configure(text=f"{completed} / {total}")
            self.after(0, _update)

        def _task():
            extractor = SEIExtractor(
                on_progress=_on_progress, url=url, user=user, passw=passw, sigla=sigla,
                save_pdf_files=save_pdf_files, save_zip_files=save_zip_files,
                output_dir=output_dir,
            )
            extractor.run(processes)

        self.run_background(_task, busy_message="Extraindo processos...")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
