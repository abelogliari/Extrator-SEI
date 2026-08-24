"""Painel gráfico (CustomTkinter) do SEI Extractor.

Interface inspirada no ExtratorCatmat: paleta de cores em tom gov.br e um
console de log estilo terminal. A aba "Extração" reúne as credenciais do SEI
(usuário, senha, sigla da unidade) e a lista de NUPs a extrair (via CSV),
executando o mesmo SEIExtractor usado pelo CLI (`sei_extractor.extractor`).
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Permite rodar este arquivo diretamente (ex.: botão "Run" da IDE), sem ser
    # via `python -m sei_extractor.gui`. Sem isso, os imports relativos abaixo
    # (`from .extractor import ...`) falham com "attempted relative import
    # with no known parent package".
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "sei_extractor"

import csv
import logging
import queue
import threading
from typing import Callable, List

import customtkinter as ctk
from tkinter import filedialog, messagebox

# --------------------------------------------------------------------------
# Paleta (inspirada no gov.br / ExtratorCatmat)
# --------------------------------------------------------------------------
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
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

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
        self.tabview.pack(side="top", fill="both", expand=True, padx=14, pady=(12, 8))

        tab_extracao = self.tabview.add("Extração")
        self._build_extracao_tab(tab_extracao)

    def _build_log_console(self):
        wrapper = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=8)
        wrapper.pack(side="bottom", fill="both", padx=14, pady=(0, 14))

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
        logger = logging.getLogger("sei_extractor")
        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        self._gui_logger = logger

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
            except Exception as exc:  # pragma: no cover - defensivo, mostrado no console/log
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
        self._build_credentials_section(tab)
        self._build_nups_section(tab)
        self._build_run_section(tab)

    def _build_credentials_section(self, tab):
        card = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=8, border_width=1, border_color=C_BORDER)
        card.pack(fill="x", padx=14, pady=(12, 8))

        ctk.CTkLabel(
            card, text="Credenciais do SEI", font=ctk.CTkFont(size=14, weight="bold"), text_color=C_TEXT
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        try:
            from .config import SEI_URL, SEI_USERNAME, SEI_PASSWORD
        except Exception:
            SEI_URL = SEI_USERNAME = SEI_PASSWORD = ""

        ctk.CTkLabel(card, text="URL do SEI:").grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.url_entry = ctk.CTkEntry(card, width=320, placeholder_text="https://sei.orgao.gov.br/sei")
        self.url_entry.insert(0, SEI_URL or "")
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

        for widget in (self.url_entry, self.user_entry, self.password_entry, self.sigla_entry):
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

    def _build_run_section(self, tab):
        card = ctk.CTkFrame(tab, fg_color="transparent")
        card.pack(fill="x", padx=14, pady=(0, 12))

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
            import traceback
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

        if not url or not user or not passw:
            self.log("URL, usuário ou senha do SEI não informados.", level=logging.WARNING)
            messagebox.showwarning("SEI Extractor", "Informe URL, usuário e senha do SEI.")
            return

        save_pdf_files = self.save_pdf_var.get()
        save_zip_files = self.save_zip_var.get()

        self.log(
            f"Iniciando extração de {len(processes)} processo(s)"
            + (f" na unidade '{sigla}'" if sigla else "")
            + (" [PDF]" if save_pdf_files else "")
            + (" [ZIP]" if save_zip_files else "")
        )

        self.extract_progress.set(0)
        self.extract_progress_label.configure(text=f"0 / {len(processes)}")

        def _on_progress(completed: int, total: int):
            def _update():
                self.extract_progress.set((completed / total) if total else 0)
                self.extract_progress_label.configure(text=f"{completed} / {total}")
            self.after(0, _update)

        def _task():
            try:
                from .extractor import SEIExtractor
            except Exception as exc:
                self.log(f"Não foi possível carregar o extrator: {exc}", level=logging.ERROR)
                return
            extractor = SEIExtractor(
                on_progress=_on_progress, url=url, user=user, passw=passw, sigla=sigla,
                save_pdf_files=save_pdf_files, save_zip_files=save_zip_files,
            )
            extractor.run(processes)

        self.run_background(_task, busy_message="Extraindo processos...")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
