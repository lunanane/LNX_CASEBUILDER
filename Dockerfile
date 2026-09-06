# hwcase, hosted.
#
# The image is the whole installation: the engine, the curated part library,
# the editor, and the Hershey fonts the engraver draws with. What it does not
# contain is anybody's project -- see HWCASE_HOSTED below. That is what makes
# one container safe to point a crowd at: there is no scene directory for two
# people to collide in, because there is no scene directory.
#
#   docker build -t hwcase .
#   docker run --rm -p 8000:8000 hwcase
#
# Add the photographic renderer -- Mitsuba, about 100 MB, and the only reason
# this image is not small -- with:
#
#   docker build --build-arg WITH_RENDER=1 -t hwcase .

FROM python:3.12-slim

ARG WITH_RENDER=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HWCASE_HOSTED=1

# libgomp is what numpy, shapely and the tracer thread themselves with. Nothing
# else is needed: every dependency ships a wheel, so there is no compiler here.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies in their own layer, so editing the engine does not reinstall
# numpy. pytest and httpx come along with them, which is deliberate: the tests
# run in the image, and an installation you cannot test is one you are trusting
# rather than checking.
COPY backend/requirements.txt backend/requirements.txt
RUN python -m pip install --upgrade pip \
 && python -m pip install -r backend/requirements.txt \
 && if [ "$WITH_RENDER" = "1" ]; then python -m pip install mitsuba; fi

COPY backend/hwcase  backend/hwcase
COPY backend/parts   backend/parts
COPY backend/tests   backend/tests
COPY web             web
COPY vendor/fonts    vendor/fonts

# The vendor catalogue and the CAD it downloads are caches, not data: they are
# the same five and a half thousand Adafruit products for everybody, they
# refresh on their own, and losing them costs one slow search. Mount a volume
# here to keep them across restarts; leave it and the container rebuilds them.
RUN mkdir -p vendor/catalog vendor/cad \
 && useradd --system --uid 10001 --create-home hwcase \
 && chown -R hwcase:hwcase /app/vendor
VOLUME ["/app/vendor"]

USER hwcase
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# One worker by default. Geometry is CPU work behind the GIL, so a busy
# instance wants more -- but each worker gets its own render semaphore, so
# raise --workers and HWCASE_RENDER_SLOTS together, not separately.
CMD ["python", "-m", "uvicorn", "hwcase.api:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
