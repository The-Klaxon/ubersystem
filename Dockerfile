ARG BRANCH=main
FROM ghcr.io/magfest/sideboard:${BRANCH} as install
MAINTAINER RAMS Project "code@magfest.org"
LABEL version.rams-core ="0.1"

RUN apt-get update && apt-get install -y libxml2-dev libxmlsec1-dev git libpq-dev build-essential pkg-config && rm -rf /var/lib/apt/lists/*

ADD requirements*.txt plugins/uber/
ADD setup.py plugins/uber/
ADD uber/_version.py plugins/uber/uber/

RUN /app/env/bin/paver install_deps

FROM ghcr.io/magfest/sideboard:${BRANCH} as build
RUN apt-get update && apt-get install -y libxml2-dev libxmlsec1-dev && rm -rf /var/lib/apt/lists/*
COPY --from=install /app /app

ADD uber-development.ini.template ./uber-development.ini.template
ADD sideboard-development.ini.template ./sideboard-development.ini.template
ADD uber-wrapper.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/uber-wrapper.sh
ADD rebuild-config.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/rebuild-config.sh

ADD . plugins/uber/

# These are just semi-reasonable defaults. Use either -e or --env-file to set what you need
# I.e.:
# docker run -it -e HOST=192.168.0.10 -e PORT=80 ghcr.io/magfest/ubersystem:main
# or
# echo "HOST=192.168.0.10" > uberenv
# docker run -it --env-file uberenv ghcr.io/magfest/ubersystem:main
ENV HOST=0.0.0.0
ENV PORT=8282
ENV HOSTNAME=localhost
ENV DEFAULT_URL=/uber
ENV DEBUG=false
ENV SESSION_HOST=redis
ENV SESSION_PORT=6379
ENV SESSION_PREFIX=uber
ENV BROKER_PROTOCOL=amqp
ENV BROKER_HOST=rabbitmq
ENV BROKER_PORT=5672
ENV BROKER_USER=celery
ENV BROKER_PASS=celery
ENV BROKER_VHOST=uber

FROM build as test
RUN /app/env/bin/pip install mock pytest
CMD /app/env/bin/python3 -m pytest plugins/uber

FROM build as release
ENTRYPOINT ["/usr/local/bin/uber-wrapper.sh"]
CMD ["uber"]