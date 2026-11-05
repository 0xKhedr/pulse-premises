FROM thethingsnetwork/lorawan-stack:3.37

USER root
RUN apk add --no-cache gettext-envsubst

COPY --chmod=755 scripts/ /usr/local/bin/

USER thethings:thethings
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["start"]
