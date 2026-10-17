# Official Python 3.12 slim image
FROM python:3.12-slim

# Create a non-root user with fixed UID 10001 and no login shell
RUN useradd -u 10001 -m -s /usr/sbin/nologin runner

# Place runner.py in /runner owned by root and read-only
WORKDIR /runner
COPY runner.py /runner/runner.py
RUN chown -R root:root /runner && chmod 555 /runner/runner.py

# Set WORKDIR to tmp directory and switch to non-root user
WORKDIR /tmp
USER 10001

ENTRYPOINT ["python", "-u", "/runner/runner.py"]
