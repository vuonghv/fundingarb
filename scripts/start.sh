#!/bin/bash
#
# Start the Funding Rate Arbitrage System
#
# Usage:
#   ./scripts/start.sh                    # Start in simulation mode
#   ./scripts/start.sh --live             # Start in live mode (DANGEROUS)
#   ./scripts/start.sh --docker           # Start with Docker
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${PROJECT_DIR}/config/config.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
LIVE_MODE=false
DOCKER_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --live)
            LIVE_MODE=true
            shift
            ;;
        --docker)
            DOCKER_MODE=true
            shift
            ;;
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    log_error "Configuration file not found: $CONFIG_FILE"
    log_info "Create one from the example: cp config/config.example.yaml config/config.yaml"
    exit 1
fi

# Live mode warning
if [ "$LIVE_MODE" = true ]; then
    echo ""
    log_warn "╔══════════════════════════════════════════════════════════════╗"
    log_warn "║                    LIVE TRADING MODE                         ║"
    log_warn "║                                                              ║"
    log_warn "║  You are about to start the system in LIVE trading mode.    ║"
    log_warn "║  Real money will be at risk!                                 ║"
    log_warn "║                                                              ║"
    log_warn "║  Make sure you have:                                         ║"
    log_warn "║    - Tested in simulation mode for at least 24 hours        ║"
    log_warn "║    - Verified all exchange API keys are correct             ║"
    log_warn "║    - Set appropriate position limits                        ║"
    log_warn "║    - Configured Telegram alerts                             ║"
    log_warn "║                                                              ║"
    log_warn "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    read -p "Type 'I UNDERSTAND THE RISKS' to continue: " confirmation
    if [ "$confirmation" != "I UNDERSTAND THE RISKS" ]; then
        log_error "Aborted."
        exit 1
    fi
    export FUNDINGARB_SIMULATION_MODE=false
else
    export FUNDINGARB_SIMULATION_MODE=true
    log_info "Starting in SIMULATION mode (use --live for live trading)"
fi

# Set config path
export FUNDINGARB_CONFIG_PATH="$CONFIG_FILE"

# Docker mode
if [ "$DOCKER_MODE" = true ]; then
    log_info "Starting with Docker..."
    cd "$PROJECT_DIR/docker"

    if [ "$LIVE_MODE" = true ]; then
        SIMULATION_MODE=false docker-compose up -d
    else
        SIMULATION_MODE=true docker-compose up -d
    fi

    log_info "Services started. View logs with: docker-compose logs -f"
    exit 0
fi

# Local mode
log_info "Starting locally..."
cd "$PROJECT_DIR"

# Check Python virtual environment
if [ ! -d "venv" ]; then
    log_warn "Virtual environment not found. Creating..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Run the application
log_info "Starting Funding Rate Arbitrage System..."
python -m backend.main
