# Streamlit + AWS Lambda Deployment
# Optimized for serverless web app on AWS Lambda

ARG APP_DIR="/app"

###
### Builder Image
###

FROM python:3.12-slim AS builder

ARG APP_DIR

# Copy app code
RUN mkdir -p ${APP_DIR}
COPY app.py ${APP_DIR}/
COPY requirements.txt ${APP_DIR}/
COPY src/ ${APP_DIR}/src/
COPY docs/ ${APP_DIR}/docs/
COPY repositories/ ${APP_DIR}/repositories/
COPY .env.example ${APP_DIR}/.env.example
COPY .streamlit/ ${APP_DIR}/.streamlit/

WORKDIR ${APP_DIR}

# Update CA certificates
RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential libffi-dev libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev python3-dev

# Apt cleanup
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install dependencies
RUN python -m venv ${APP_DIR}/.venv
ENV PATH="${APP_DIR}/.venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt
RUN pip install awslambdaric


###
### Final Image
###

FROM python:3.12-slim

ARG APP_DIR

# Copy AWS Lambda Web Adapter extension
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter

# Update CA certificates
RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates

# Set locale
RUN apt-get update && apt-get install -y locales && \
    echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Apt cleanup
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the app from builder image
COPY --from=builder ${APP_DIR} ${APP_DIR}
WORKDIR ${APP_DIR}

# Set environment for Lambda
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_ENABLE_CORS=false
ENV STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

# Copy Streamlit config for Lambda
COPY .streamlit/lambda.toml ${APP_DIR}/.streamlit/config.toml

# Lambda entry point - runs Streamlit with Web Adapter
CMD ["python", "-m", "awslambdaric", "lambda_handler.lambda_handler"]
