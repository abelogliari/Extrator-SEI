#!/usr/bin/env python3
"""
Converte JSONs extraídos do SEI para um CSV consolidado.
Usa o JSONToCSVService para processamento com pandas e rich progress bars.
"""

import sys
from pathlib import Path
import argparse

# Adicionar o diretório src ao path para importar o service
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sei_extractor.services import JSONToCSVService
from rich.console import Console

console = Console()


def main():
    """Função principal"""
    parser = argparse.ArgumentParser(
        description='Converte JSONs do SEI para CSV consolidado usando pandas',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de uso:
  # Usar diretórios padrão
  python json_to_csv.py
  
  # Especificar diretório de entrada
  python json_to_csv.py --input output/json
  
  # Especificar arquivo de saída
  python json_to_csv.py --output meus_processos.csv
  
  # Ambos
  python json_to_csv.py --input data/json --output resultados.csv
        """
    )

    parser.add_argument(
        '--input', '-i',
        type=str,
        default='output/json',
        help='Diretório contendo os arquivos JSON (padrão: output/json)'
    )

    parser.add_argument(
        '--output', '-o',
        type=str,
        default='output/processos_consolidados.csv',
        help='Arquivo CSV de saída (padrão: output/processos_consolidados.csv)'
    )

    args = parser.parse_args()

    json_dir = Path(args.input)
    output_csv = Path(args.output)

    if not json_dir.exists():
        console.print(f"[red]❌ Diretório não encontrado: {json_dir}[/red]")
        sys.exit(1)

    console.print("="*60)
    console.print("🔄 [cyan]Conversão JSON para CSV[/cyan]")
    console.print("="*60)
    console.print(f"📁 Input:  [cyan]{json_dir.absolute()}[/cyan]")
    console.print(f"📄 Output: [cyan]{output_csv.absolute()}[/cyan]")
    console.print("="*60)
    console.print()

    # Usar o service
    service = JSONToCSVService()
    success = service.convert_and_save(
        json_dir=json_dir,
        output_csv=output_csv,
        show_progress=True
    )

    console.print()
    console.print("="*60)
    if success:
        console.print("✨ [green]Concluído![/green]")
    else:
        console.print("❌ [red]Falhou![/red]")
    console.print("="*60)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()



