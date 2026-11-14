# Third-party software

What the lossless project store ships and runs, with licences as declared in each package's own metadata
(read with `importlib.metadata` inside the built `jstore-api` image; regenerate after changing `requirements.txt`).

## Not MIT-like: read these first

| Component | Licence | Where it runs | What that means here |
|---|---|---|---|
| **IfcOpenShell** 0.9.0.post1 | LGPL-3.0-or-later | imported by `jstore/ingest/ifc.py` in the API container | The one accepted copyleft Python dependency. Used unmodified as a pip wheel; anyone may replace it with their own build. Our code stays under its own licence. |
| **PostGIS** 3.7.0 | GPL-2.0-or-later | only inside the database container (`store/db/Dockerfile`), as a PostgreSQL extension | Reached over the PostgreSQL wire protocol; no PostGIS code is linked into or shipped with the API. Distributing the database image means distributing GPL software with its source offer (the Debian/PGDG packages provide it). |
| **LibreDWG** 0.13.3 (`dwg2dxf`) | GPL-3.0-or-later | **optional, off by default**: only in images built with `--build-arg WITH_LIBREDWG=1` | Compiled from the GNU release tarball in a separate build stage and started as a separate program (`subprocess`) to convert DWG to DXF; never linked or imported. An image built with it contains a GPL-3.0 binary (its `COPYING` is copied next to it). Without it, DWG uploads are registered but not parsed. |
| certifi 2026.10.23 | MPL-2.0 | API container (CA bundle of the HTTP client) | File-level copyleft; used unmodified. |
| tqdm 4.70.1 | MPL-2.0 AND MIT | API container (dependency of RapidOCR) | File-level copyleft; used unmodified. |
| Debian base image (glibc, libstdc++, libgomp ...) | LGPL / GPL with the GCC runtime exception, and others | both containers | The usual system libraries of `python:3.14-slim` and `postgres:17`; `/usr/share/doc/*/copyright` in the images lists them. |

Deliberately **not** used: PyMuPDF / `fitz` (AGPL-3.0) and psycopg (LGPL-3.0). PDFs are read with pypdfium2 and
pdfplumber, the database driver is pg8000.

## Database image (`jstore-db:17`)

| Component | Version | Licence |
|---|---|---|
| PostgreSQL (with the bundled `pg_trgm`, `unaccent`, `plpgsql`) | 17.12 | PostgreSQL License (permissive, BSD-like) |
| PostGIS (`postgresql-17-postgis-3`; brings GEOS LGPL-2.1, PROJ MIT, GDAL MIT, SFCGAL LGPL-2.0-or-later built on CGAL GPL-3.0 / LGPL-3.0) | 3.7.0 | GPL-2.0-or-later |
| pgvector (`postgresql-17-pgvector`) | 0.8.7 | PostgreSQL License |

## API image (`jstore-api`): system packages added to `python:3.14-slim` (Debian 13)

| Package | Version | Licence |
|---|---|---|
| Python | 3.14 | PSF-2.0 |
| tesseract-ocr + language data eng, deu, fra, ita, fin | 5.5.0 / 4.1.0 | Apache-2.0 |
| libgomp1 | 14.2.0 | GPL-3.0-or-later with the GCC Runtime Library Exception |
| libgl1 (libglvnd), libglib2.0-0, libxcb1 | Debian 13 | MIT-style / LGPL-2.1-or-later / MIT |
| fonts-dejavu-core | 2.37 | Bitstream Vera licence (permissive) |
| RapidOCR's bundled PP-OCRv4 models (inside the `rapidocr-onnxruntime` wheel) | 1.4.4 | Apache-2.0 |

## API image: Python packages (direct and transitive)

| Package | Version | Licence (package metadata) |
|---|---|---|
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| asn1crypto | 1.5.1 | MIT License |
| attrs | 26.1.0 | MIT |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| contourpy | 1.4.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| cycler | 0.12.1 | BSD License |
| ezdxf | 1.4.5 | MIT License |
| fastapi | 0.162.7 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fonttools | 4.68.0 | MIT |
| griffelib | 2.4.1 | ISC |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| ifcopenshell | 0.9.0.post1 | LGPL-3.0-or-later |
| ImageIO | 2.38.1 | BSD-2-Clause |
| iniconfig | 2.3.1 | MIT |
| isodate | 0.7.2 | BSD License |
| jiter | 0.19.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| kiwisolver | 1.5.1 | BSD License |
| lark | 1.3.1 | MIT License |
| lazy-loader | 0.6 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mcp | 2.3.0 | MIT License |
| mcp-types | 2.3.0 | MIT License |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.32.0 | MIT License |
| openai | 3.51.0 | Apache-2.0 |
| openai-agents | 0.24.0 | MIT |
| opencv-python-headless | 5.0.0.93 | Apache Software License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20260107 | MIT |
| pdfplumber | 0.11.10 | MIT License |
| pg8000 | 1.31.6 | BSD License |
| pillow | 13.0.0 | MIT-CMU |
| pluggy | 1.7.0 | MIT License |
| protobuf | 7.36.2 | 3-Clause BSD License |
| pyclipper | 1.4.0 | OSI Approved, MIT License |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| pyparsing | 3.3.3 | MIT |
| pypdfium2 | 5.15.2 | BSD-3-Clause, Apache-2.0, dependency licenses |
| pytesseract | 0.3.13 | Apache Software License |
| pytest | 9.2.0 | MIT |
| pytest-asyncio | 1.5.0 | Apache-2.0 |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| python-multipart | 0.0.33 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT License |
| rapidocr-onnxruntime | 1.4.4 | Apache-2.0 |
| referencing | 0.37.0 | MIT |
| requests | 2.34.2 | Apache Software License |
| rpds-py | 2026.9.1 | MIT |
| scikit-image | 0.26.0 | BSD License |
| scipy | 1.18.1 | BSD License |
| scramp | 1.5.0 | MIT-0 |
| shapely | 2.2.0 | BSD-3-Clause |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License, Apache Software License |
| sse-starlette | 3.7.0 | BSD-3-Clause |
| starlette | 1.10.0 | BSD-3-Clause |
| tifffile | 2026.11.12 | BSD-3-Clause |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |

Notes: pypdfium2 bundles PDFium (BSD-3-Clause / Apache-2.0) and its dependencies' licences inside the wheel;
numpy, scipy, opencv, onnxruntime and shapely wheels bundle permissively licensed native libraries (OpenBLAS BSD,
GEOS LGPL-2.1 as a dynamically loaded library inside shapely, libgfortran with the GCC runtime exception).

## Front end of the reference page

`jstore/service/ui/index.html` loads React, ReactDOM and htm (all MIT) from esm.sh at view time; nothing is vendored.
