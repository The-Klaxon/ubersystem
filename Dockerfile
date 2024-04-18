# syntax = docker/dockerfile:1.4.0

FROM python:3.12.3-alpine as build
WORKDIR /app
ENV PYTHONPATH=/app
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.cargo/bin

ADD https://astral.sh/uv/install.sh /tmp/install-uv.sh

# We're upgrading to edge because lxml comes with its own libxml2 which must match the system version for xmlsec to work
# We can remove this once python ships a docker container with a libxml2 that matches lxml
# Check lxml version with:
# import lxml.etree
# lxml.etree.LIBXML_VERSION
# Alternatively, build lxml from source to link against system libxml2: RUN uv pip install --system --no-binary lxml lxml
RUN --mount=type=cache,target=/var/cache/apk \
    sed -i 's/v3.19/edge/' /etc/apk/repositories && \
    apk --update-cache upgrade && \
    apk add git libxml2 xmlsec-dev build-base && \
    sh /tmp/install-uv.sh && \
    rm /tmp/install-uv.sh

ADD requirements.txt /app/
#RUN --mount=type=cache,target=/root/.cache \
RUN    uv pip install --system -r requirements.txt;

ADD uber-wrapper.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/uber-wrapper.sh

FROM build as test
RUN uv pip install -r requirements_test.txt
CMD python -m pytest
ADD . /app

FROM build as release
ENTRYPOINT ["/usr/local/bin/uber-wrapper.sh"]
CMD ["uber"]
ADD . /app
