"""Refactored core orchestrator for SEI.

This module exposes the same `SEI` class API as before but delegates page/frame
interactions to a Navigator, parsing to a Parser and debug dumping to DebugDumper.
The refactor follows single-responsibility principles so each helper is small and
testable.
"""

from typing import Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Playwright, Page
from .navigator import Navigator
from .debug import DebugDumper
from .parser import (
    parse_metadata_from_frames,
    get_especificacao,
    get_tipo_procedimento,
    get_assunto,
    get_interessados,
    get_observacoes,
    get_anexos,
)
from bs4 import BeautifulSoup


class SEI:
    """Orchestrator class for SEI scraping.

    Public API is preserved: `do_login()`, `search()`, `get_metadados()`,
    `get_historico()`, `save_pdf()` and `save_zip()` remain available.
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
        self._navigator = None

        if self._external_playwright:
            # use provided Playwright context
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
        """Log in to SEI using site selectors. Keeps previous selectors/behavior."""
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
            # Last resort: submit via Enter on the password field so page behaves as before
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
        # delegate to navigator for UI interactions
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

    def select_unidade(self, sigla: str) -> bool:
        """Troca a unidade ativa no SEI para a unidade cuja sigla contenha `sigla`.

        No topo do SEI a unidade atual é um `<select id="selInfraUnidade">` com uma
        opção por unidade (ex.: "CGCUSTOS (MS)"). Aceita a sigla parcial ou completa
        (ex.: "CGCUSTOS" ou "CGCUSTOS (MS)") e seleciona a primeira opção cujo texto
        contenha o valor informado.
        """
        if not sigla:
            return False

        select = self._page.locator('#selInfraUnidade')
        try:
            select.wait_for(state='visible', timeout=5000)
        except Exception:
            return False

        alvo = sigla.strip().lower()
        options = select.locator('option')
        for i in range(options.count()):
            option = options.nth(i)
            texto = (option.text_content() or '').strip()
            if alvo in texto.lower():
                select.select_option(label=texto)
                return True

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
        # delegate click inside iframe
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        self._navigator.click_consultar_processo()

    def get_metadados(self, numero_processo: str) -> dict:
        # implementation restored to original approach: parse iframe HTML with BeautifulSoup
        self._ensure_browser()
        # ensure page/frame loaded
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        self._navigator.wait()

        # get content from visualization iframe
        frame = self._navigator.get_frame('ifrVisualizacao')
        soup = BeautifulSoup(frame.content(), 'html.parser')

        especificacao = get_especificacao(soup)
        tipo_procedimento = get_tipo_procedimento(soup)
        assunto = get_assunto(soup)
        interessados = get_interessados(soup)
        observacoes = get_observacoes(soup)

        # open all folders and parse attachments from the tree iframe
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
        # Keep behavior but delegate frame interactions
        if not self._navigator:
            self._navigator = Navigator(self._page, debug=self._debug, dumper=self._dumper)
        # click consult andamento
        try:
            self._navigator.get_frame('ifrArvore').locator('xpath=//*[@id="divConsultarAndamento"]').click()
        except Exception:
            pass
        # wait for visual frame then parse in parser if needed
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

        # fallback: parse table content from frame HTML as before
        from .parser import parse_date
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

        import pandas as pd
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
                frame.keyboard.press('Enter')
            except Exception:
                pass
        with self._page.expect_download() as download_info:
            frame.locator('xpath=//*[@name="btnGerar"]').click()
        download = download_info.value
        download.save_as(file_name)

    def save_pdf(self, file_name: Path):
        return self.__save_file(file_name, 'xpath=//*[@title="Gerar Arquivo PDF do Processo"]')

    def save_zip(self, file_name: Path):
        return self.__save_file(file_name, 'xpath=//*[@title="Gerar Arquivo ZIP do Processo"]')
