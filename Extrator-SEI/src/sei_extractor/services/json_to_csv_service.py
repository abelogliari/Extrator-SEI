"""
Service para conversão de JSONs extraídos do SEI para CSV/DataFrame.
Utiliza pandas para processamento eficiente e rich para progress bars.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn
from rich.console import Console

console = Console()


class JSONToCSVService:
    """Service para converter JSONs do SEI em CSV consolidado usando pandas."""

    def __init__(self):
        self.errors = []

    def flatten_list_field(self, items: List[Dict], prefix: str) -> Dict[str, Any]:
        """
        Achata uma lista de objetos em campos separados.
        Ex: assunto_count, assunto_nomes, assunto_ids
        MANTÉM TODOS OS DADOS em JSON completo.
        """
        result = {}
        if not items:
            result[f"{prefix}_count"] = 0
            result[f"{prefix}_completo_json"] = '[]'
            return result

        result[f"{prefix}_count"] = len(items)

        # Concatenar todos os nomes/valores em uma string separada por ' | '
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
            # Se são strings simples
            result[f"{prefix}_valores"] = " | ".join(str(item) for item in items)

        # SEMPRE adiciona JSON completo
        result[f"{prefix}_completo_json"] = json.dumps(items, ensure_ascii=False)

        return result

    def process_historico(self, historico: List[Dict]) -> Dict[str, Any]:
        """Processa o histórico de andamento - MANTÉM TODOS OS DADOS."""
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

        # Ordenar por data
        historico_sorted = sorted(
            historico,
            key=lambda x: x.get('criado_em', ''),
            reverse=True
        )

        # Pegar unidades únicas
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
        """Processa observações - MANTÉM TODOS OS DADOS."""
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
        """Processa anexos - MANTÉM TODOS OS DADOS."""
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
        """
        Converte JSON complexo em dicionário plano para DataFrame.
        """
        flat = {}

        # Campos simples
        flat['protocolo'] = data.get('protocolo', '')
        flat['especificacao'] = data.get('especificacao', '')

        # Tipo de procedimento
        tipo = data.get('tipo_procedimento', {})
        if tipo:
            flat['tipo_procedimento_id'] = tipo.get('id', '')
            flat['tipo_procedimento_nome'] = tipo.get('nome', '')
            flat['tipo_procedimento_completo_json'] = json.dumps(tipo, ensure_ascii=False)
        else:
            flat['tipo_procedimento_id'] = ''
            flat['tipo_procedimento_nome'] = ''
            flat['tipo_procedimento_completo_json'] = '{}'

        # Assuntos (lista)
        assuntos = data.get('assunto', [])
        flat.update(self.flatten_list_field(assuntos, 'assunto'))

        # Interessados (lista)
        interessados = data.get('interessados', [])
        flat.update(self.flatten_list_field(interessados, 'interessado'))

        # Observações
        flat.update(self.process_observacoes(data.get('observacoes', [])))

        # Anexos
        flat.update(self.process_anexos(data.get('anexos', [])))

        # Histórico de andamento
        flat.update(self.process_historico(data.get('historico_andamento', [])))

        return flat

    def convert_jsons_to_dataframe(
        self,
        json_dir: Path,
        show_progress: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Converte todos os JSONs de um diretório para um DataFrame pandas.

        Args:
            json_dir: Diretório contendo os arquivos JSON
            show_progress: Se True, mostra progress bar

        Returns:
            DataFrame com todos os processos ou None se não houver dados
        """
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
            # Sem progress bar
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

        # Criar DataFrame
        df = pd.DataFrame(all_rows)

        # Ordenar colunas de forma lógica
        ordered_cols = self._order_columns(df.columns.tolist())
        df = df[ordered_cols]

        return df

    def _order_columns(self, columns: List[str]) -> List[str]:
        """Ordena as colunas de forma lógica."""
        ordered = []
        remaining = set(columns)

        # Campos principais primeiro
        main_fields = ['protocolo', 'especificacao', 'tipo_procedimento_id', 'tipo_procedimento_nome']
        for field in main_fields:
            if field in remaining:
                ordered.append(field)
                remaining.remove(field)

        # Depois assuntos, interessados, etc
        for prefix in ['assunto', 'interessado', 'observacoes', 'anexos', 'historico']:
            prefix_cols = sorted([c for c in remaining if c.startswith(prefix)])
            ordered.extend(prefix_cols)
            remaining -= set(prefix_cols)

        # Resto em ordem alfabética
        ordered.extend(sorted(remaining))

        return ordered

    def save_to_csv(
        self,
        df: pd.DataFrame,
        output_path: Path,
        show_progress: bool = True
    ) -> bool:
        """
        Salva DataFrame em CSV.

        Args:
            df: DataFrame a ser salvo
            output_path: Caminho do arquivo CSV de saída
            show_progress: Se True, mostra mensagem de progresso

        Returns:
            True se salvou com sucesso, False caso contrário
        """
        try:
            # Criar diretório se não existir
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if show_progress:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console
                ) as progress:
                    task = progress.add_task("[cyan]Salvando CSV...", total=None)

                    # Salvar com encoding UTF-8 com BOM para Excel
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
        """
        Converte JSONs para CSV em um único processo.

        Args:
            json_dir: Diretório com JSONs
            output_csv: Arquivo CSV de saída
            show_progress: Se True, mostra progress bars

        Returns:
            True se sucesso, False caso contrário
        """
        # Converter para DataFrame
        df = self.convert_jsons_to_dataframe(json_dir, show_progress=show_progress)

        if df is None:
            return False

        # Salvar CSV
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

