# Simple Python devcontainer
FROM mcr.microsoft.com/devcontainers/python:3.11

# Useful tools for diag
RUN apt-get update && apt-get install -y --no-install-recommends     iperf3 pciutils iproute2 openssh-client  && rm -rf /var/lib/apt/lists/*
