#!/bin/bash
# Install typefuzz (yinyang) mutation fuzzer
# Requires: Python 3.8+, pip
set -e

echo "Installing typefuzz (yinyang)..."

pip3 install yinyang

if command -v typefuzz >/dev/null 2>&1; then
    echo "typefuzz installed successfully: $(typefuzz --version 2>&1 || echo 'version check not supported')"
else
    echo "ERROR: typefuzz not found in PATH after installation"
    exit 1
fi
