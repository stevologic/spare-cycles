# SpareCycles donor node — donate idle AI compute to a pool.
#
# Pair once (saves the node identity into the volume):
#   docker run -it -v sparecycles:/data ghcr.io/stevologic/spare-cycles \
#     --server https://your-pool.example.com --code AB12-CD34
#
# Then serve (keys stay on YOUR machine; only prompts/answers move):
#   docker run -d --restart unless-stopped -v sparecycles:/data \
#     -e ANTHROPIC_API_KEY=sk-... ghcr.io/stevologic/spare-cycles
#
# The connector is stdlib-only Python, so this image is just python:slim
# plus one file.

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/stevologic/spare-cycles"
LABEL org.opencontainers.image.description="SpareCycles donor node - donate your idle AI compute"
LABEL org.opencontainers.image.licenses="MIT"

# Node identity (node.json) lives here — mount a volume to persist pairing.
ENV SPARECYCLES_HOME=/data
VOLUME /data

RUN useradd -r -m -d /home/node node && mkdir -p /data && chown node /data
USER node
WORKDIR /app

COPY connector/node_connector.py /app/node_connector.py

ENTRYPOINT ["python", "/app/node_connector.py"]
