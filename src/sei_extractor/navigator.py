from typing import Optional
from playwright.sync_api import Page, Frame
from pathlib import Path
import requests
import pymupdf
import time


class Navigator:
    def __init__(self, page: Page, debug: bool = False, dumper=None):
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
            # in debug mode, dump page/frame artifacts for inspection
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
