# The disposable box the loom_gates replay runs other repositories' code in.
# Built once with network; every replay runs it with --network none,
# --read-only, a tmpfs /tmp, a non-root user and nothing from the host mounted.
FROM python@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530

RUN apt-get update \
 && apt-get install -y --no-install-recommends git jq bash coreutils \
 && rm -rf /var/lib/apt/lists/*

# The review bot's modules import these at load time (its webhook service's
# requirements, minus the server, tracing and lint tools the scanners never run).
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# The review bot builds a cloud SDK client at import time: a placeholder
# region, and no credentials exist in the image.
ENV LOOM_GATES_CONTAINER=1 HOME=/tmp/home PYTHONDONTWRITEBYTECODE=1 \
    AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
USER 65534:65534
WORKDIR /tmp
