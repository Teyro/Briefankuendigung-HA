ARG BUILD_FROM
FROM $BUILD_FROM

# Node.js (IMAP und MIME sind selbst geschrieben, keine weiteren Pakete); tzdata für die Abrufzeiten
RUN apk add --no-cache nodejs tzdata

WORKDIR /app
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
