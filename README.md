# SEI Extractor

Extrator de metadados de processos do SEI, com interface gráfica (CustomTkinter). Todo o projeto vive em um único arquivo, [ExtratorSEI.py](ExtratorSEI.py) — sem pacote, sem instalação via pip, no estilo de um script standalone.

## Instalação

1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Instale o navegador do Playwright (necessário para a automação web):
   ```bash
   python -m playwright install chromium
   ```

## Uso

```bash
python ExtratorSEI.py
```

Isso abre o painel gráfico. Nele você informa:
- URL, usuário e senha do SEI (também podem vir de um arquivo `.env`, veja abaixo)
- Sigla da unidade (opcional)
- Lista de NUPs a extrair (via CSV ou digitando um único NUP)
- Pasta de saída onde os JSONs/CSV/PDFs/ZIPs serão salvos

### Variáveis de ambiente (opcional)

Crie um arquivo `.env` na mesma pasta para pré-preencher os campos de credenciais:
```
SEI_URL=<SEI_URL>
SEI_USERNAME=<SEI_USERNAME>
SEI_PASSWORD=<SEI_PASSWORD>
THREADS=10
HEADLESS=True
```

## Saída

Por padrão, os arquivos são salvos em `output/` (configurável na própria interface):
- `output/json/` — metadados extraídos de cada processo
- `output/metadata.csv` — resumo consolidado
- `output/pdfs/` e `output/zips/` — quando as opções "Salvar PDF/ZIP" estiverem marcadas
- `sei_extractor.log` — log de execução

## Empacotar como executável (opcional)

Como o projeto é um único arquivo, dá para gerar um executável standalone com [PyInstaller](https://pyinstaller.org/):
```bash
pip install pyinstaller
pyinstaller --onefile --windowed ExtratorSEI.py
```
