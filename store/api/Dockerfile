# API image of the lossless project store.  Build context: the store folder.
#   docker build -f api/Dockerfile -t jstore-api .
#   docker build -f api/Dockerfile --build-arg WITH_LIBREDWG=1 -t jstore-api .   (adds dwg2dxf, GPL-3.0, see THIRD_PARTY.md)
ARG PYTHON=python:3.14-slim
ARG WITH_LIBREDWG=0

# ---- optional: LibreDWG's dwg2dxf, compiled from the GNU release tarball.  It is GPL-3.0-or-later and is only ever
# started as a separate program (never linked or imported).  Off by default: stage dwg-0 is an empty stand-in.
FROM ${PYTHON} AS dwg-0
RUN mkdir -p /opt/libredwg/bin

FROM ${PYTHON} AS dwg-1
ARG LIBREDWG_VERSION=0.13.3
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl xz-utils \
 && curl -fsSL https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz | tar -xJ -C /tmp \
 && cd /tmp/libredwg-${LIBREDWG_VERSION} \
 && ./configure --prefix=/opt/libredwg --disable-shared --disable-bindings --disable-write --disable-docs \
 && make -j"$(nproc)" && make install-strip \
 && cp COPYING /opt/libredwg/bin/dwg2dxf.COPYING \
 && find /opt/libredwg/bin -type f ! -name 'dwg2dxf*' -delete

FROM dwg-${WITH_LIBREDWG} AS dwg

# ---- the service
FROM ${PYTHON}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg MPLCONFIGDIR=/tmp JSTORE_DATA_DIR=/data/store JSTORE_DATASET_DIR=/data/dataset
# tesseract + language data: OCR of scanned plans; libgomp1: OpenMP runtime of the numeric wheels;
# libgl1/libglib2.0-0/libxcb1: shared libraries the geometry and imaging wheels load; fonts: page renders.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-ita tesseract-ocr-fin \
      libgomp1 libgl1 libglib2.0-0 libxcb1 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && pip install --no-deps --ignore-requires-python rapidocr-onnxruntime==1.4.4
COPY --from=dwg /opt/libredwg/bin/ /usr/local/bin/
COPY jstore ./jstore
COPY prompts ./prompts
RUN useradd --create-home --uid 10001 jstore && mkdir -p /data/store /data/dataset && chown -R jstore /data/store
USER jstore
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "jstore.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
