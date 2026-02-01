#!/bin/bash
# GeoLabeller Linux Build Script
# Builds the application using cx_Freeze in a temporary virtual environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Parse arguments
CLEAN=false
KEEP_VENV=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --clean) CLEAN=true ;;
        --keep-venv) KEEP_VENV=true ;;
        -h|--help)
            echo "Usage: $0 [--clean] [--keep-venv]"
            echo "  --clean      Clean build directory before building"
            echo "  --keep-venv  Keep the virtual environment after build"
            exit 0
            ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SCRIPT_DIR/.build_venv"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

echo -e "${CYAN}GeoLabeller Linux Build Script${NC}"
echo -e "${CYAN}=================================${NC}"
echo ""

# Check for Python
echo -e "${YELLOW}Checking Python installation...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "${RED}  ERROR: Python not found. Please install Python 3.${NC}"
    exit 1
fi
PYTHON_VERSION=$($PYTHON --version 2>&1)
echo -e "${GREEN}  Found: $PYTHON_VERSION${NC}"

# Clean if requested
if [ "$CLEAN" = true ]; then
    echo -e "${YELLOW}Cleaning build directory...${NC}"
    rm -rf "$SCRIPT_DIR/build"
    rm -rf "$SCRIPT_DIR/dist"
    rm -rf "$VENV_DIR"
    echo -e "${GREEN}  Cleaned${NC}"
fi

# Create virtual environment
echo -e "${YELLOW}Creating virtual environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo -e "${RED}  ERROR: Failed to create virtual environment${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}  Virtual environment ready${NC}"

# Activate venv
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Update pip, setuptools, and wheel using python -m pip
echo -e "${YELLOW}Updating pip, setuptools, and wheel...${NC}"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel > /dev/null
if [ $? -ne 0 ]; then
    echo -e "${RED}  ERROR: Failed to update pip, setuptools, and wheel${NC}"
    exit 1
fi
echo -e "${GREEN}  Updated pip, setuptools, and wheel${NC}"

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
"$VENV_PIP" install --upgrade pip > /dev/null
"$VENV_PIP" install -r "$REQUIREMENTS_FILE"
if [ $? -ne 0 ]; then
    echo -e "${RED}  ERROR: Failed to install dependencies${NC}"
    exit 1
fi
echo -e "${GREEN}  Dependencies installed${NC}"

# Change to build directory
cd "$SCRIPT_DIR"

# Build
echo -e "${YELLOW}Building executable...${NC}"
"$VENV_PYTHON" setup.py build
BUILD_RESULT=$?

if [ $BUILD_RESULT -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Build completed successfully!${NC}"
    
    # Find output directory
    OUTPUT_DIR=$(find "$SCRIPT_DIR/build" -maxdepth 1 -type d -name "exe.*" 2>/dev/null | head -n 1)
    if [ -n "$OUTPUT_DIR" ]; then
        echo -e "${CYAN}Output location: $OUTPUT_DIR${NC}"
    fi
else
    echo -e "${RED}Build failed!${NC}"
fi

# Clean up virtual environment unless --keep-venv is specified
if [ "$KEEP_VENV" = false ] && [ -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Cleaning up virtual environment...${NC}"
    rm -rf "$VENV_DIR"
    echo -e "${GREEN}  Done${NC}"
fi

exit $BUILD_RESULT
