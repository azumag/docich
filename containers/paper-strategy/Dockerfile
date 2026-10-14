FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
LABEL org.docich.paper-strategy.abi="1"
COPY src/docich/trading/free_strategy/guest.py /opt/docich/guest.py
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/python", "-I", "-B", "/opt/docich/guest.py"]
