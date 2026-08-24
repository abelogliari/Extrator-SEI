import logging
import json
import os
import time
import csv
import threading
from queue import Queue, Empty

from pathlib import Path
from math import ceil
from concurrent.futures import ThreadPoolExecutor

from logging.handlers import RotatingFileHandler
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

from playwright.sync_api import sync_playwright

from .core import SEI
from .config import SEI_URL, SEI_USERNAME, SEI_PASSWORD, HEADLESS, THREADS

LOG_FILE = "sei_extractor.log"
logger = logging.getLogger("sei_extractor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5_242_880, backupCount=3)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # logger.addHandler(logging.StreamHandler())

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
JSON_DIR = os.path.join(OUTPUT_DIR, "json")
PDF_DIR = os.path.join(OUTPUT_DIR, "pdfs")
ZIP_DIR = os.path.join(OUTPUT_DIR, "zips")
os.makedirs(JSON_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(ZIP_DIR, exist_ok=True)
METADATA_CSV = os.path.join(OUTPUT_DIR, "metadata.csv")

class SEIExtractor:
    def __init__(self, include_related=True):
        self.url = SEI_URL
        self.user = SEI_USERNAME
        self.passw = SEI_PASSWORD
        self.progress = None
        self.overall_task = None
        self.lock = threading.Lock()
        self.work_queue = None
        self.thread_tasks = {}  # Mapeia thread_index -> task_id
        self.include_related = include_related  # Se True, adiciona processos relacionados à fila
        self.processed_set = set()  # Rastreia processos já adicionados à fila (evita duplicatas)

    @staticmethod
    def clean_process_number(number) -> str:
        """
        Remove formatação do número do processo.
        """
        return number.replace('/', '').replace('.', '').replace('-', '')

    def run(self, process_numbers):
        """
        Processa usando uma fila compartilhada - threads pegam trabalho dinamicamente (work stealing).
        """
        # Criar fila compartilhada com todos os processos
        self.work_queue = Queue()
        self.processed_set.clear()  # Limpar set de processos já adicionados

        # Adicionar apenas processos que ainda não foram baixados
        for process_num in process_numbers:
            output_file = Path(JSON_DIR, f"{self.clean_process_number(process_num)}.json")

            if output_file.exists():
                # Se arquivo já existe e include_related está ativo, adicionar seus relacionados
                if self.include_related:
                    try:
                        with open(output_file, "r") as f:
                            metadata = json.load(f)
                            if metadata.get('processos_relacionados'):
                                self._add_related_to_queue(metadata['processos_relacionados'])
                    except:
                        pass  # Se der erro ao ler JSON, ignora e continua
            else:
                # Só adiciona à fila se ainda não foi processado
                self.work_queue.put(process_num)
                self.processed_set.add(process_num)  # Marcar como já adicionado à fila

        # Criar Progress com colunas personalizadas e refresh rate otimizado
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            refresh_per_second=4,  # Atualizar apenas 4x por segundo para evitar spam
            transient=False,  # Manter visível após concluir
        ) as progress:
            self.progress = progress

            # Criar task principal (pai) - começa em 0 e será incrementado apenas com processos novos
            self.overall_task = progress.add_task(
                "[cyan]Total Progress",
                total=0  # Será incrementado conforme encontramos processos que precisam ser baixados
            )

            # Criar tasks para cada thread (inicialmente com total=0, será ajustado dinamicamente)
            for i in range(THREADS):
                thread_task = progress.add_task(
                    f"[green]Thread {i + 1}",
                    total=0,  # Será incrementado conforme a thread pega trabalho
                    visible=False  # Oculto até a thread começar
                )
                self.thread_tasks[i] = thread_task

            # Executar threads
            with ThreadPoolExecutor(max_workers=THREADS) as executor:
                futures = [
                    executor.submit(self._process_from_queue, i)
                    for i in range(THREADS)
                ]
                # Aguardar conclusão
                for future in futures:
                    future.result()


    def _process_from_queue(self, thread_index):
        """
        Thread worker que consome processos da fila compartilhada até ela estar vazia.
        Implementa work stealing - threads rápidas ajudam as lentas.
        """
        thread_task = self.thread_tasks[thread_index]
        processed_count = 0

        with sync_playwright() as p:
            sei = SEI(self.url, self.user, self.passw, p, headless=HEADLESS)
            try:
                sei.do_login()

                # Tornar a task visível agora que a thread começou
                with self.lock:
                    self.progress.update(thread_task, visible=True)

                # Consumir processos da fila até estar vazia
                while True:
                    try:
                        # Pegar próximo processo da fila (timeout de 1s)
                        process_number = self.work_queue.get(timeout=1)

                        # Incrementar o total desta thread e do overall (todos os processos na fila são novos)
                        with self.lock:
                            current_total_thread = self.progress.tasks[thread_task].total
                            current_total_overall = self.progress.tasks[self.overall_task].total
                            self.progress.update(thread_task, total=current_total_thread + 1)
                            self.progress.update(self.overall_task, total=current_total_overall + 1)

                        # Processar
                        success = self.process(process_number, sei)
                        processed_count += 1

                        # Atualizar ambas as progress bars
                        if success:
                            with self.lock:
                                self.progress.update(thread_task, advance=1)
                                self.progress.update(self.overall_task, advance=1)

                        # Marcar item como concluído na fila
                        self.work_queue.task_done()

                    except Empty:
                        # Fila vazia - thread terminou seu trabalho
                        break

            finally:
                sei.close()

                # Atualizar descrição para mostrar que a thread terminou
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
        output_file = Path(JSON_DIR, f"{self.clean_process_number(process_number)}.json")

        while attempt < max_retries:
            attempt += 1
            try:
                sei.search(process_number)
                metadata = sei.get_metadados(process_number)
                historico_andamento = sei.get_historico()
                metadata['historico_andamento'] = historico_andamento

                # Se include_related=True, adicionar processos relacionados à fila
                if self.include_related and metadata.get('processos_relacionados'):
                    self._add_related_to_queue(metadata['processos_relacionados'])

                with open(output_file, "w") as f:
                    json.dump(metadata, f, indent=4, default=str)

                self._append_metadata_csv(metadata)

                return True
            except Exception as e:
                time.sleep(delay)
                delay *= 2

        return False

    def _add_related_to_queue(self, processos_relacionados):
        """
        Adiciona processos relacionados à fila, evitando duplicatas.
        Thread-safe para chamadas concorrentes.
        """
        with self.lock:
            for proc_rel in processos_relacionados:
                # Normalizar o número do processo (pode vir sem formatação)
                if proc_rel and proc_rel not in self.processed_set:
                    # Verificar se arquivo já não existe
                    output_file = Path(JSON_DIR, f"{self.clean_process_number(proc_rel)}.json")
                    if not output_file.exists():
                        self.work_queue.put(proc_rel)
                        self.processed_set.add(proc_rel)

    def _append_metadata_csv(self, metadata: dict):
        # Create a flat summary row for CSV (protocolo, tipo_procedimento, assunto_count, interessados_count)
        protocolo = metadata.get('protocolo')
        tipo = metadata.get('tipo_procedimento', {}).get('nome') if metadata.get('tipo_procedimento') else ''
        assunto_count = len(metadata.get('assunto') or [])
        interessados_count = len(metadata.get('interessados') or [])
        row = [protocolo, tipo, assunto_count, interessados_count]

        write_header = not os.path.exists(METADATA_CSV)
        with open(METADATA_CSV, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            if write_header:
                writer.writerow(['protocolo', 'tipo_procedimento', 'assunto_count', 'interessados_count'])
            writer.writerow(row)
