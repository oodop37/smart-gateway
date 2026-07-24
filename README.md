# Smart Gateway

## 📖 Overview

Smart Gateway is an intelligent LLM (Large Language Model) aggregation gateway that provides an OpenAI-compatible API interface for accessing multiple LLM providers. It features intelligent model routing, automatic provider discovery, scoring-based model selection, context compression, and a comprehensive management dashboard.

Built with FastAPI and designed for NAS deployment (tested on fnOS/fiOS), Smart Gateway helps you aggregate free and paid LLM APIs into a single, reliable endpoint with smart failover and load balancing capabilities.

## ✨ Key Features

- **OpenAI Compatible API**: Drop-in replacement for OpenAI API endpoints
- **Intelligent Model Routing**: Routes requests to the best available model based on composite scoring (ability + stability)
- **Automatic Provider Discovery**: GitHub-based scanning for new free LLM APIs
- **Model Scoring System**: 
  - Ability score from leaderboards (LMSYS, etc.)
  - Stability score from usage success rate and latency
  - Composite score for final routing decision
- **Circuit Breaker**: Automatically skips failing models
- **Streaming Fault Tolerance**: Seamless failover during streaming responses
- **Context Compression**: Reduces token usage with intelligent prompt compression
- **Provider & Key Management**: Full CRUD operations for API providers and keys
- **Management Dashboard**: Web UI for monitoring and configuration
- **Prometheus Metrics**: Built-in metrics endpoint for monitoring
- **Rate Limiting**: Per-IP request limiting to prevent abuse
- **Docker Ready**: Optimized for containerized deployment
- **NAS Optimized**: Lightweight footprint suitable for home NAS devices

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Client App    │───▶│ Smart Gateway    │───▶│  LLM Providers   │
│ (OpenAI Compat) │    │ (FastAPI Server) │    │ (FreeLLM, etc.)  │
└─────────────────┘    └─────────────┬─────┘    └──────────┬───────┘
                                      │                   │
                      ┌───────────────▼───────────────┐   │
                      │     Management Dashboard      │   │
                      │   (Web UI for Admin/Ops)      │   │
                      └───────────────────────────────┘   │
                                      │                   │
                      ┌───────────────▼───────────────┐   │
                      │  Background Tasks (Scheduler)   │   │
                      │ • Leaderboard Updates         │   │
                      │ • GitHub Provider Discovery   │   │
                      │ • Score Synchronization       │   │
                      │ • Model SLA Probing           │   │
                      └───────────────────────────────┘   │
                                      │                   │
                      ┌───────────────▼───────────────┐   │
                      │         SQLite Database       │   │
                      │ (Providers, Models, Usage,    │   │
                      │  Leaderboard, Routing Groups) │   │
                      └───────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- At least one LLM provider API key (e.g., from FreeLLM API, NVIDIA, etc.)

### Using Docker Compose

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/smart-gateway.git
   cd smart-gateway
   ```

2. Configure your settings in `config.yaml`:
   ```yaml
   # Copy from config.example.yaml or edit config.yaml directly
   port: 8765
   host: 0.0.0.0
   database:
     path: /data/smartgateway.db
   logging:
     level: INFO
   # ... other configurations
   ```

3. Start the gateway:
   ```bash
   docker-compose up -d
   ```

4. Verify it's running:
   ```bash
   # Check health endpoint
   curl http://localhost:8765/health
   
   # View available models
   curl http://localhost:8765/v1/models
   ```

5. Use the OpenAI-compatible API:
   ```bash
   curl http://localhost:8765/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer sk-your-gateway-key" \
     -d '{
       "model": "auto",
       "messages": [{"role": "user", "content": "Hello, who are you?"}],
       "max_tokens": 100
     }'
   ```

### Manual Installation (Advanced)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure config.yaml (see above)

# Run the gateway
python app.py
```

## ⚙️ Configuration

The gateway is configured via `config.yaml` and environment variables. Key sections:

```yaml
port: 8765                    # Port to listen on
host: 0.0.0.0                 # Host to bind to

database:
  path: ./data/smartgateway.db  # SQLite database path

logging:
  level: INFO                 # Log level (DEBUG, INFO, WARNING, ERROR)

scoring:
  ability_weight: 0.4         # Weight for ability score (0.0-1.0)
  stability_weight: 0.6       # Weight for stability score (0.0-1.0)

leaderboard:
  enabled: true               # Enable automatic leaderboard updates
  sources:                    # Leaderboard sources to scrape
    - name: "lmsys"
      url: "https://raw.githubusercontent.com/lmsys/lmsys-chart/main/data/model_battle.yml"
      parser: "enabled": true

discovery:
  enabled: false              # GitHub auto-discovery (disabled by default after optimization)
  interval_minutes: 1440      # How often to scan for new providers (24h)

sla:
  interval_minutes: 1         # How often to probe model health (1 minute)

compression:
  enabled: true               # Enable context compression
  mode: builtin               # Compression mode (builtin or rtk)
  max_context_tokens: 4096    # Maximum tokens before compression triggers
  cache_ttl_minutes: 30       # How long to cache compressed results
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIG_PATH` | Path to configuration file | `./config.yaml` |
| `DB_PATH` | Path to SQLite database | `./data/smartgateway.db` |
| `LOG_LEVEL` | Logging level | `INFO` |

## 📡 API Endpoints

### OpenAI Compatible Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List available models (OpenAI format) |
| `/v1/chat/completions` | POST | Chat completions (streaming and non-streaming) |
| `/v1/completions` | POST | Legacy completions endpoint |

### Management API (Requires no auth by default - consider enabling in production)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/providers` | GET | List all providers |
| `/api/providers/{id}` | GET | Get provider details |
| `/api/providers` | POST | Add new provider |
| `/api/providers/{id}` | PUT | Update provider |
| `/api/providers/{id}` | DELETE | Delete provider |
| `/api/providers/{id}/keys` | GET | List provider API keys |
| `/api/providers/{id}/keys` | POST | Add API key |
| `/api/providers/{id}/keys/{key_id}` | DELETE | Delete API key |
| `/api/models` | GET | List all models |
| `/api/models/routing-groups` | GET | List routing groups |
| `/api/models/routing-groups` | POST | Create routing group |
| `/api/stats` | GET | Usage statistics (last 24h by default) |
| `/api/scores/sync` | POST | Manually trigger score synchronization |
| `/api/leaderboard` | GET | Get latest leaderboard |
| `/api/leaderboard/refresh` | POST | Manually refresh leaderboard |
| `/api/discovery/scan` | POST | Trigger GitHub provider discovery |
| `/api/discovery/scan-candidates` | POST | Preview discovery candidates |
| `/api/discovery/import-selected` | POST | Import selected discovery candidates |
| `/api/verify-provider` | POST | Test provider connectivity |
| `/api/config` | GET | Get current configuration |
| `/api/config/scoring` | POST | Update scoring weights |
| `/api/config/compression` | POST | Update compression settings |
| `/health` | GET | Health check endpoint |
| `/metrics` | GET | Prometheus metrics endpoint |

## 🖥️ Management Dashboard

Access the web dashboard at `http://your-host:8765/` to:

- View and manage API providers and keys
- Monitor model performance and leaderboard
- Configure scoring weights and compression settings
- Trigger manual leaderboard refreshes and provider discovery
- View usage statistics
- See your current API endpoint and key (displayed in the header)

## 🔐 Authentication & Security

By default, the management API is accessible without authentication for convenience in trusted environments. For production use:

1. The OpenAI-compatible `/v1/chat/completions` endpoint requires a Bearer token in the Authorization header
2. Management API endpoints are currently open (consider adding authentication middleware if exposing to public networks)
3. API keys for providers are stored encrypted in the database (basic obfuscation in UI)

**Important**: The gateway API key (used for `/v1/chat/completions`) is configured via:
- Environment variable: `GATEWAY_API_KEY`
- Or in `config.yaml` under `api_key` setting
- If not set, a default key is generated on first startup

## 🐳 Docker Deployment

The included `Dockerfile` and `docker-compose.yml` are optimized for NAS deployment:

```bash
# Build and start
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop and remove
docker-compose down

# Backup database (mounted volume)
cp ./data/smartgateway.db ./backups/
```

### Docker Configuration Tips

- The container runs as non-root user for security
- Data is persisted via volume mount (`./data:/data`)
- Adjust resource limits in `docker-compose.yml` if needed for your NAS:
  ```yaml
  services:
    smart-gateway:
      deploy:
        resources:
          limits:
            cpus: "0.5"
            memory: 512M
  ```

## 📊 Monitoring

### Prometheus Metrics

Access `http://your-host:8765/metrics` to scrape:
- Request counts and latency
- Active request gauges
- Model routing counters
- Custom application metrics

### Health Checks

- `GET /health` - Basic service health
- `GET /v1/models` - API availability check
- Management dashboard provides visual health indicators

## 🛠️ Development

### Project Structure

```
smart-gateway/
├── app.py                 # Main FastAPI application
├── database.py            # Database layer and models
├── router.py              # Core model routing logic
├── scorer.py              # Leaderboard scraping and score synchronization
├── discoverer.py          # GitHub-based provider discovery
├── compressor.py          # Context compression engine
├── scheduler.py           # Background task scheduler
├── tasks.py               # Shared background tasks (SLA probing)
├── constants.py           # Shared constants (model aliases, etc.)
├── requirements.txt       # Python dependencies
├── config.yaml            # Configuration file
├── Dockerfile             # Container image definition
├── docker-compose.yml     # Docker Compose definition
├── routes/                # API route modules
│   ├── providers.py       # Provider management API
│   ├── models.py          # Model and routing group management
│   ├── stats.py           # Statistics, discovery, score sync APIs
│   └── config.py          # Configuration update API
├── static/                # Static assets for dashboard
├── templates/             # HTML templates for dashboard
└── data/                  # SQLite database and runtime data
```

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

## 💡 Optimization Notes

Recent optimizations performed (v1.0.1):

1. **Code Splitting**: 
   - Split monolithic `app.py` (1063 lines) into modular route files
   - Reduced main app to ~300 lines for better maintainability

2. **Deduplication**:
   - Consolidated duplicate `MODEL_KEYWORDS` into `constants.py`
   - Unified duplicate `list_models` logic
   - Consolidated SLA probing logic into `tasks.py`

3. **Bug Fixes**:
   - Fixed `import json` placement in `compressor.py`
   - Replaced manual DB connection handling with context managers
   - Fixed provider deletion scoping bug

4. **Architecture Improvements**:
   - Separated concerns with clear module boundaries
   - Improved error handling and resource cleanup
   - Better separation of API routes from core logic

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Uses [Uvicorn](https://www.uvicorn.org/) as ASGI server
- Integrates with various free LLM APIs through [FreeLLM API](https://freellmapi.com/)
- Inspired by open-source LLM gateway projects
- Special thanks to the Hermes Agent framework for foundational concepts

## 📞 Support

For issues, questions, or contributions:
1. Check the [GitHub Issues](https://github.com/oodop37/smart-gateway/issues)
2. Submit a pull request for bug fixes or features
3. Contact the maintainer through GitHub discussions

---

*Smart Gateway v1.0.0 - Optimized for NAS deployment and efficient LLM routing*  
*Last updated: July 2026*