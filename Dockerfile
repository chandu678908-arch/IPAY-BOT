FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESSERACT_CMD=/usr/bin/tesseract

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN test -x /usr/bin/tesseract \
    && /usr/bin/tesseract --version \
    && REQUIRE_TESSERACT_TEST=1 python -m unittest discover -s tests -p test_ocr_integration.py -v

CMD ["python", "bot.py"]
