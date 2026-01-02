#!/bin/bash
#
# Run testing
#
# Usage:
#   ./scripts/test.sh                    # Run test the whole project
#

# Check Python virtual environment
if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Creating..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    echo "Activating Python virtual environment..."
    source .venv/bin/activate
fi

# Run the application
python -m pytest tests/
