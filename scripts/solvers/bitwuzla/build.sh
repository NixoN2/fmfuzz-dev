#!/bin/bash
# Bitwuzla Build Script
# Usage: ./build.sh [--coverage] [--static]

set -e

ENABLE_COVERAGE=false
ENABLE_STATIC=false
for arg in "$@"; do
    if [[ "$arg" == "--coverage" ]]; then
        ENABLE_COVERAGE=true
    elif [[ "$arg" == "--static" ]]; then
        ENABLE_STATIC=true
    fi
done

echo "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
  build-essential git libgmp-dev libmpfr-dev meson ninja-build python3 python3-pip

if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "Installing coverage tools..."
    sudo apt-get install -y lcov gcc libgtest-dev
    pip3 install fastcov psutil
fi

echo "Cloning Bitwuzla..."
if [ ! -d "bitwuzla" ]; then
    git clone https://github.com/bitwuzla/bitwuzla.git bitwuzla
fi

cd bitwuzla

echo "Setting up Python environment..."
python3 -m venv ~/.venv
source ~/.venv/bin/activate
python3 -m pip install meson pytest cython>=3.*

echo "Configuring..."
if [[ "$ENABLE_STATIC" == "true" ]] && [[ "$ENABLE_COVERAGE" == "true" ]]; then
    ./configure.py debug --coverage --static
elif [[ "$ENABLE_STATIC" == "true" ]]; then
    ./configure.py --static
elif [[ "$ENABLE_COVERAGE" == "true" ]]; then
    ./configure.py debug --coverage
else
    ./configure.py
fi

cd build
ninja

if [[ "$ENABLE_STATIC" != "true" ]]; then
    sudo ninja install
fi

echo "Verifying binary..."
./src/main/bitwuzla --version

echo "Bitwuzla build complete."
