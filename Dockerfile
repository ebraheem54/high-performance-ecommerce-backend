FROM python:3.11-alpine3.18
LABEL maintainer="ebraheemdeveloper.com"
ENV PYTHONUNBUFFERED=1
COPY ./requirements.txt /tmp/requirements.txt
COPY ./requirements.dev.txt /tmp/requirements.dev.txt
WORKDIR /app
EXPOSE 8000
ARG DEV=false
RUN python -m venv /py && \
    /py/bin/pip install --upgrade pip && \
    apk add --no-cache \
        postgresql-client \
        jpeg-dev \
        zlib-dev \
        libffi-dev \
        cairo-dev \
        pango-dev \
        gdk-pixbuf-dev \
        netcat-openbsd && \
    apk add --no-cache --virtual .tmp-build-deps \
        build-base \
        postgresql15-dev \
        musl-dev && \
    /py/bin/pip install -r /tmp/requirements.txt && \
    if [ "$DEV" = "true" ]; then \
        /py/bin/pip install -r /tmp/requirements.dev.txt ; \
    fi && \
    rm -rf /tmp && \
    apk del .tmp-build-deps && \
    mkdir -p /prometheus-multiproc && \
    adduser \
        --disabled-password \
        --no-create-home \
        django-user && \
    chown -R django-user:django-user /prometheus-multiproc
ENV PATH="/py/bin:$PATH"
COPY . /app
USER django-user
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:8000", "config.wsgi:application"]
