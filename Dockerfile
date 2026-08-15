FROM python:3.12.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 appuser
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY projects ./projects
COPY scripts ./scripts
RUN mkdir -p /var/lib/jarvis/media && chown -R appuser:appuser /app /var/lib/jarvis
USER appuser
CMD ["python", "-m", "src.agent_loop"]
