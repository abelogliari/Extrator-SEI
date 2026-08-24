# Use the official Python image as a base
FROM python:3.13-slim

# Set the working directory
WORKDIR /app

# Copy the project files
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium) and its system deps
RUN python -m pip install playwright && \
	playwright install --with-deps chromium

# Set the entrypoint
ENTRYPOINT ["python", "-m", "sei_extractor"]