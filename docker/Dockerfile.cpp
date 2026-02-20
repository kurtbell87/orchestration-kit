FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# System build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    pkg-config \
    curl \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release \
    software-properties-common \
    libssl-dev \
    libzstd-dev \
    zlib1g-dev \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Apache Arrow (official apt repo for Arrow 18+)
RUN wget -qO- https://apache.jfrog.io/artifactory/arrow/ubuntu/apache-arrow-apt-source-latest-jammy.deb \
        -O /tmp/arrow.deb && \
    apt-get install -y /tmp/arrow.deb && rm /tmp/arrow.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        libarrow-dev \
        libparquet-dev \
    && rm -rf /var/lib/apt/lists/*

# libtorch CPU (stable release)
RUN curl -sL https://download.pytorch.org/libtorch/cpu/libtorch-cxx11-abi-shared-with-deps-2.5.1%2Bcpu.zip \
        -o /tmp/libtorch.zip && \
    unzip -q /tmp/libtorch.zip -d /opt && \
    rm /tmp/libtorch.zip

# ONNX Runtime CPU
RUN curl -sL https://github.com/microsoft/onnxruntime/releases/download/v1.20.1/onnxruntime-linux-x64-1.20.1.tgz \
        -o /tmp/onnxruntime.tgz && \
    tar xzf /tmp/onnxruntime.tgz -C /opt && \
    mv /opt/onnxruntime-linux-x64-1.20.1 /opt/onnxruntime && \
    rm /tmp/onnxruntime.tgz

# AWS CLI v2
RUN curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && \
    cd /tmp && unzip -q awscliv2.zip && ./aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

ENV CMAKE_PREFIX_PATH="/opt/libtorch;/opt/onnxruntime"
ENV LD_LIBRARY_PATH="/opt/libtorch/lib:/opt/onnxruntime/lib"

WORKDIR /work
