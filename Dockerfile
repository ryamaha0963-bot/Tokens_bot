FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required by Playwright (minimal)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers and system dependencies (automatically)
RUN playwright install --with-deps chromium

# Copy bot code
COPY main.py .

# Run the bot
CMD ["python", "main.py"]
