import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import List

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from sei_extractor.extractor import SEIExtractor
from sei_extractor.core import SEI
from sei_extractor.config import SEI_URL, SEI_USERNAME, SEI_PASSWORD, THREADS
from sei_extractor.services import JSONToCSVService

console = Console()


def load_process_list(file_path: str) -> List[str]:
    """Carrega lista de processos de um arquivo (um por linha ou JSON array)."""
    path = Path(file_path)

    if not path.exists():
        console.print(f"[red]Error: File not found: {file_path}[/red]")
        sys.exit(1)

    content = path.read_text(encoding='utf-8').strip()

    # Tentar parsear como JSON primeiro
    if content.startswith('['):
        try:
            processes = json.loads(content)
            return [str(p).strip() for p in processes if p]
        except json.JSONDecodeError:
            pass

    # Se não for JSON, tratar como texto (um processo por linha)
    processes = [line.strip() for line in content.split('\n') if line.strip()]
    return processes


def cmd_extract(args):
    """Extrai metadados de processos."""
    processes = []

    if args.process:
        # Processo único
        processes = [args.process]
    elif args.file:
        # Arquivo com lista de processos
        processes = load_process_list(args.file)
    else:
        console.print("[red]Error: Please provide --process or --file[/red]")
        sys.exit(1)

    if not processes:
        console.print("[yellow]Warning: No processes to extract[/yellow]")
        return

    console.print(Panel(
        f"[cyan]Extracting {len(processes)} process(es) using {THREADS} thread(s)[/cyan]",
        title="SEI Extractor",
        border_style="cyan"
    ))

    extractor = SEIExtractor()
    extractor.run(processes)

    console.print("\n[green]✓ Extraction completed successfully![/green]")


def cmd_debug(args):
    """Modo debug com browser visível para inspeção."""
    if not args.process:
        console.print("[red]Error: Please provide a process number with --process[/red]")
        sys.exit(1)

    process_number = args.process
    console.print(f"[yellow]Starting debug session for process: {process_number}[/yellow]")
    console.print("[dim]Browser will open in non-headless mode...[/dim]\n")

    with sync_playwright() as p:
        sei = SEI(SEI_URL, SEI_USERNAME, SEI_PASSWORD, p, headless=False, debug=True)
        try:
            sei.do_login()
            console.print("[green]✓ Login successful[/green]")

            # Salvar artefatos de debug
            debug_dir = Path("output") / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            safe_name = process_number.replace('/', '_')

            # Screenshot da página
            try:
                screenshot_path = debug_dir / f"{safe_name}_post_login.png"
                sei._page.screenshot(path=str(screenshot_path), full_page=True)
                console.print(f"[dim]Screenshot saved: {screenshot_path}[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not save screenshot: {e}[/yellow]")

            # HTML da página principal
            try:
                html_path = debug_dir / f"{safe_name}_post_login.html"
                html_path.write_text(sei._page.content(), encoding='utf-8')
                console.print(f"[dim]HTML saved: {html_path}[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not save HTML: {e}[/yellow]")

            # Frames
            try:
                for idx, frame in enumerate(sei._page.frames):
                    frame_name = frame.name or f'frame_{idx}'
                    safe_frame_name = frame_name.replace('/', '_')
                    frame_path = debug_dir / f"{safe_name}_frame_{safe_frame_name}.html"
                    try:
                        frame_path.write_text(frame.content(), encoding='utf-8')
                        console.print(f"[dim]Frame saved: {frame_path}[/dim]")
                    except Exception:
                        pass
            except Exception:
                pass

            console.print(f"\n[cyan]Debug artifacts saved in: {debug_dir}[/cyan]")
            console.print("[yellow]Browser is open for inspection.[/yellow]")
            input("\nPress Enter to continue and extract process data...")

            # Executar extração
            sei.search(process_number)
            console.print("[green]✓ Search completed[/green]")

            metadata = sei.get_metadados(process_number)
            console.print("[green]✓ Metadata extracted[/green]")

            historico = sei.get_historico()
            console.print("[green]✓ History extracted[/green]")

            final_dict = {**metadata, "historico_andamento": historico}

            # Exibir resultado
            console.print("\n" + "="*80)
            console.print(json.dumps(final_dict, indent=2, default=str, ensure_ascii=False))
            console.print("="*80)

        finally:
            sei.close()
            console.print("\n[dim]Browser closed.[/dim]")


def cmd_setup_browsers(args):
    """Instala os browsers do Playwright."""
    console.print("[cyan]Installing Playwright browsers (Chromium)...[/cyan]")

    try:
        # Instalar playwright
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "playwright"],
            stdout=subprocess.DEVNULL if not args.verbose else None
        )

        # Instalar browsers
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            stdout=subprocess.DEVNULL if not args.verbose else None
        )

        console.print("[green]✓ Playwright browsers installed successfully![/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error installing browsers: {e}[/red]")
        sys.exit(1)


def cmd_status(args):
    """Exibe informações sobre a configuração atual."""
    table = Table(title="SEI Extractor Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("SEI URL", SEI_URL or "[red]Not configured[/red]")
    table.add_row("Username", SEI_USERNAME or "[red]Not configured[/red]")
    table.add_row("Password", "••••••••" if SEI_PASSWORD else "[red]Not configured[/red]")
    table.add_row("Threads", str(THREADS))
    table.add_row("Output Dir", "output/")

    # Verificar se há JSONs salvos
    json_dir = Path("output") / "json"
    if json_dir.exists():
        json_count = len(list(json_dir.glob("*.json")))
        table.add_row("Extracted Processes", str(json_count))

    console.print(table)


def cmd_convert_csv(args):
    """Converte JSONs extraídos para um CSV consolidado usando pandas."""
    json_dir = Path(args.input)
    output_csv = Path(args.output)

    if not json_dir.exists():
        console.print(f"[red]Error: Directory not found: {json_dir}[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[cyan]Converting JSONs from {json_dir}[/cyan]\n"
        f"[cyan]Output: {output_csv}[/cyan]",
        title="JSON to CSV Converter",
        border_style="cyan"
    ))

    # Usar o service
    service = JSONToCSVService()
    success = service.convert_and_save(
        json_dir=json_dir,
        output_csv=output_csv,
        show_progress=True
    )

    if not success:
        console.print("\n[red]✗ Conversion failed[/red]")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="SEI Extractor - Extract metadata from SEI processes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract a single process
  sei-extractor extract --process 25000.123456/2023-45
  
  # Extract multiple processes from a file
  sei-extractor extract --file processes.txt
  
  # Debug mode (visible browser)
  sei-extractor debug --process 25000.123456/2023-45
  
  # Show current configuration
  sei-extractor status
  
  # Convert extracted JSONs to CSV
  sei-extractor convert-csv
  sei-extractor convert-csv --input output/json --output my_data.csv
  
  # Install Playwright browsers
  sei-extractor setup-browsers
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract process metadata")
    extract_parser.add_argument("--process", type=str, help="Single process number")
    extract_parser.add_argument("--file", type=str, help="File with process numbers (one per line or JSON array)")

    # Debug command
    debug_parser = subparsers.add_parser("debug", help="Debug mode with visible browser")
    debug_parser.add_argument("--process", type=str, required=True, help="Process number to debug")

    # Setup browsers command
    setup_parser = subparsers.add_parser("setup-browsers", help="Install Playwright browsers")
    setup_parser.add_argument("--verbose", action="store_true", help="Show installation output")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show configuration and status")

    # Convert CSV command
    convert_parser = subparsers.add_parser("convert-csv", help="Convert extracted JSONs to consolidated CSV")
    convert_parser.add_argument("--input", type=str, default="output/json", help="Input directory with JSON files")
    convert_parser.add_argument("--output", type=str, default="output/processos_consolidados.csv", help="Output CSV file")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Executar comando
    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "debug":
        cmd_debug(args)
    elif args.command == "setup-browsers":
        cmd_setup_browsers(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "convert-csv":
        cmd_convert_csv(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()