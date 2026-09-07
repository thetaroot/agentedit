FROM python:3.12-slim

WORKDIR /app

# tree-sitter wheels build fine without compilers on slim for common platforms;
# install build deps only if a wheel is missing.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

RUN pip install --no-cache-dir .

# The graph lives under the repo being indexed; mount it read/write.
VOLUME ["/repo"]
ENV AGENTEDIT_DB=/repo/.agentedit/graph.db

ENTRYPOINT ["agentedit"]
CMD ["--help"]
