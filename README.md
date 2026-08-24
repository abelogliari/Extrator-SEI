# SEI Extractor

## Overview
The SEI Extractor is an independent Python module designed to scrape data from the SEI system. It supports multithreading, Docker execution, and CLI commands for seamless operation.

## Features
- Automatic login to SEI.
- Process search and metadata extraction.
- Multithreaded execution with ThreadPoolExecutor.
- Configurable via `.env` file.
- Docker support for containerized execution.

## Installation

### Local Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd sei-extractor
   ```
2. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Install Playwright browsers (required):
    - Preferred (manual):
       ```bash
       # if you use the provided venv
       source .venv/bin/activate
       python scripts/install_playwright.sh
       ```
    - Or run directly:
       ```bash
       python -m pip install playwright
       python -m playwright install --with-deps chromium
       ```

### Docker Installation
1. Build the Docker image:
   ```bash
   docker-compose build
   ```
2. Run the container:
   ```bash
   docker-compose up
   ```
Note: The Dockerfile runs `playwright install --with-deps chromium` during image build so no extra steps are required in the container.

## Usage

### CLI Commands
- **Login**:
  ```bash
  python -m sei_extractor login
  ```
- **Fetch Process**:
  ```bash
  python -m sei_extractor fetch --process <process_number>
  ```
- **Sync Processes**:
  ```bash
  python -m sei_extractor sync --threads 10
  ```

### Environment Variables
Create a `.env` file with the following variables:
```
SEI_URL=<SEI_URL>
SEI_USERNAME=<SEI_USERNAME>
SEI_PASSWORD=<SEI_PASSWORD>
THREADS=10
HEADLESS=True
```

## Output
- Logs are saved to `sei_extractor.log`.
- Extracted data can be saved in JSON, CSV, or PDF formats.

## Contributing
Feel free to submit issues or pull requests to improve the project.

## License
This project is licensed under the MIT License.