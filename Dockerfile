FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY savageops ./savageops
RUN pip install --no-cache-dir .
CMD ["savageops", "run", "--host", "0.0.0.0", "--port", "8000"]
