# Multi-stage build for smaller final image
FROM python:3.13-slim AS builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.13-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY src/ ./src/
COPY ima_server_simple.py .

# Make sure scripts are in PATH
ENV PATH=/root/.local/bin:$PATH

# Set default environment variables (only optional ones)
# Required variables: IMA_X_IMA_COOKIE, IMA_X_IMA_BKN
# Knowledge base IDs can be synchronized later via sync_knowledge_bases

# Optional: Complete Cookie string (for enhanced authentication)
ENV IMA_COOKIES=""

# Optional: Device identifier and client ID (auto-generated if not provided)
ENV IMA_USKEY=""
ENV IMA_CLIENT_ID=""

# Server configuration
ENV IMA_MCP_HOST=0.0.0.0
ENV IMA_MCP_PORT=8081
ENV IMA_MCP_DEBUG=false
ENV IMA_MCP_LOG_LEVEL=INFO

# IMA API configuration
ENV IMA_REQUEST_TIMEOUT=30
ENV IMA_RETRY_COUNT=3
ENV IMA_ASK_CONCURRENCY_LIMIT=2
ENV IMA_KNOWLEDGE_BASE_CATALOG_FILE=/app/data/.ima_knowledge_bases.json
ENV IMA_PROXY=""

# Expose MCP server port
EXPOSE 8081

# Create writable directories for logs and synced catalog data
RUN mkdir -p /app/logs/debug/raw /app/data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081').read()" || exit 1

# Run the MCP server
CMD ["fastmcp", "run", "ima_server_simple.py:mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8081"]
