FROM python:3.12-slim

# Create non-root system user and group
RUN groupadd -r savageops && useradd -r -g savageops -d /srv -s /sbin/nologin savageops

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY savageops ./savageops

RUN pip install --no-cache-dir .

RUN chown -R savageops:savageops /srv

USER savageops

EXPOSE 8000

CMD ["savageops", "run", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
