# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY reqs.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r reqs.txt

# Copy application files
COPY scraper.py .
COPY gameloot.py .
COPY cex.py .
COPY db_utils.py .
COPY telegram_helper.py .
COPY logging_config.py .
COPY dict_list_search.py .

# Create a non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Set environment variables (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1

# Run the scraper
CMD ["python", "scraper.py"]

