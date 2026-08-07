# Testing Environment Setup

## Quick Start

Run the automated setup script:

```bash
# From project root
./scripts/setup-test-env.sh

# Or from scripts directory
cd scripts && ./setup-test-env.sh
```

## What the Script Does

1. **Configuration Setup**
    - Copies `templates/compose/compose.override.dev.yml` to `compose.override.yml`
    - Copies `.env.example` to `.env`
    - Generates a random 10-digit API key for `MDM_CHALLENGE_API_KEY`

2. **Docker Environment**
    - Builds and starts all containers with `docker compose up --build -d`

3. **Access URLs**
    - Displays local endpoints for testing
    - Shows network IP for sharing with other devices on the same network

## Manual Setup

If you prefer manual setup:

```bash
# 1. Copy configuration files
cp templates/compose/compose.override.dev.yml compose.override.yml
cp .env.example .env

# 2. Edit .env file to set your API key
# Replace MDM_CHALLENGE_API_KEY="your_api_key_here" with your key

# 3. Start containers
docker compose up --build -d
```

## Testing Endpoints

After setup, you can access:

- **Local**: `http://localhost:8000/_web?order_id=1`
- **Network**: `http://[your-network-ip]:8000/_web?order_id=1`

The script will display the exact URLs with your network IP address.

## Port Configuration

The default port is 8000. To change it, edit `MDM_PROXY_API_PORT` in your `.env` file.
