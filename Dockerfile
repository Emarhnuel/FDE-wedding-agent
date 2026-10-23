# Lambda image for the api, worker, and cron functions (terraform/lambda.tf sets each handler).
# Pattern from uv's AWS Lambda guide. Build for one architecture, without attestations, as Lambda requires:
#   docker buildx build --platform linux/amd64 --provenance=false -t <ecr repo>:<git sha> .

FROM ghcr.io/astral-sh/uv:0.9.26 AS uv

# Stage 1: install the locked dependencies into the Lambda task root.
FROM public.ecr.aws/lambda/python:3.13 AS builder

# Compile bytecode for faster cold starts; no installer metadata, for a reproducible layer.
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_INSTALLER_METADATA=1 \
    UV_LINK_MODE=copy

# Exact versions from uv.lock, without dev tools (pytest) or the project itself (src/ is copied below).
# This layer stays cached until uv.lock changes.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv export --frozen --no-emit-workspace --no-dev --no-editable -o requirements.txt && \
    uv pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# Stage 2: the runtime image, without uv or build files.
FROM public.ecr.aws/lambda/python:3.13

COPY --from=builder ${LAMBDA_TASK_ROOT} ${LAMBDA_TASK_ROOT}
COPY src ${LAMBDA_TASK_ROOT}/src

# Default handler; Terraform overrides it for the worker and cron functions.
CMD ["src.api.handler"]
