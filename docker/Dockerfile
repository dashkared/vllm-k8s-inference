# Rootless vLLM Inference Image on CPU with Pinned Dependencies
FROM vllm/vllm-openai-cpu:v0.33.0

LABEL maintainer="Dmitriy Shkarednyy"
LABEL description="Rootless vLLM CPU inference server with custom privacy logging"

# Create unprivileged user (UID 10001) for rootless execution
RUN groupadd -g 10001 vllm && \
    useradd -u 10001 -g vllm -m -s /bin/bash vllm

WORKDIR /app

# Copy pinned dependencies for build reproducibility and audit
COPY requirements.txt /app/requirements.txt

# Copy custom privacy logging module and configuration
COPY privacy_filter.py /app/privacy_filter.py
COPY logging_config.json /app/logging_config.json

# Copy and prepare entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Prepare data mount and cache directories with non-root ownership
RUN mkdir -p /data /home/vllm/.cache && \
    chown -R 10001:10001 /data /app /home/vllm

# Expose default HTTP port
EXPOSE 8000

# Set environment variables for non-root execution and custom logging
ENV PYTHONPATH="/app" \
    VLLM_LOGGING_CONFIG_PATH="/app/logging_config.json" \
    HOME="/home/vllm"

# Switch to non-root user
USER 10001

ENTRYPOINT ["/app/entrypoint.sh"]
