# Miner Commit - Bot Virus Challenge

This is a miner commit API example for Bot Virus Challenge.

## ✨ Features

- Miner commit
- Health check endpoint
- FastAPI
- Web service

---

## 🛠 Installation

### 1. 🚧 Prerequisites

- Install **Python (>= v3.10)** and **pip (>= 23)**:
    - **[RECOMMENDED] [Miniconda (v3)](https://www.anaconda.com/docs/getting-started/miniconda/install)**
    - *[arm64/aarch64] [Miniforge (v3)](https://github.com/conda-forge/miniforge)*
    - *[Python virtual environment] [venv](https://docs.python.org/3/library/venv.html)*

[OPTIONAL] For **DEVELOPMENT** environment:

- Install [**git**](https://git-scm.com/downloads)
- Setup an [**SSH key**](https://docs.github.com/en/github/authenticating-to-github/connecting-to-github-with-ssh)

### 2. 📦 Install dependencies

```sh
pip install -r ./requirements.txt
```

### 3. 🏁 Start the server

```sh
cd src
uvicorn app:app --host="0.0.0.0" --port=10002 --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips="*"

# For DEVELOPMENT:
uvicorn app:app --host="0.0.0.0" --port=10002 --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips="*" --reload
```

### 4. ✅ Check server is running

Check with CLI (curl):

```sh
# Send a ping request with 'curl' to API server:
curl -s http://localhost:10002/ping
```

Check with web browser:

- Health check: <http://localhost:10002/health>
- Swagger: <http://localhost:10002/docs>
- Redoc: <http://localhost:10002/redoc>
- OpenAPI JSON: <http://localhost:10002/openapi.json>

---

## 📋 Submission Requirements

Your submission is evaluated only if it meets all of the following requirements.

### 1. 🐍 `bot.py` must pass Ruff

`src/commit/bot.py` must pass Ruff with the provided `.ruff.toml` and report no findings:

```sh
ruff check --config .ruff.toml src/commit/bot.py
```

Also check the line limits enforced by `/solve`:

- `bot.py`: at most **2000** lines
- Any other submitted file: at most **500** lines

### 2. 🤖 The bot must perform the required page actions

The bot runs against the challenge web page and must, in order:

1. Click the three corner buttons: `click-here-1`, `click-here-2`, and `click-here-3`.
2. Type into the username and password fields:
    - `username` (`<input type="text">`): at least **16** characters
    - `password` (`<input type="password">`): at least **16** characters
3. Click the `submit` button.

Elements are identified by their `id`. Use these ids exactly as written.

### 3. 🐳 `src/commit/Dockerfile` must use only allowed sources

Only `src/commit/Dockerfile` is checked. It is one of the files `/solve` returns, and it is the Dockerfile the platform builds. Any other Dockerfile in the repository is ignored.

Your `src/commit/Dockerfile` must fetch content only from the locations below. The build network is restricted: a download from any other location fails.

| Category | Allowed provider |
| --- | --- |
| Base image (`FROM`) | Docker container registry (see note below) |
| Debian packages (`apt-get`) | Debian package repositories |
| Ubuntu packages (`apt-get`) | Ubuntu package repositories |
| Alpine packages (`apk`) | Alpine Linux package CDN |
| Python packages (`pip`) | PyPI and Python Package Index file host |
| JavaScript packages (`npm`, `yarn`) | npm registry and Yarn registry |
| Java packages (Maven) | Maven Central |
| Go modules (`go`) | Go module proxy and Go checksum database |
| Rust crates (`cargo`) | crates.io |

Note: the build network does not proxy container registries. `FROM` images are pulled by the host Docker daemon before the build, so only base images that the challenge operator has made available can be used.

### 4. 🔍 Validate before submission

Before submitting:

```sh
# Lint check (requirement 1):
ruff check --config .ruff.toml src/commit/bot.py

# Send a GET /solve request to confirm your submission is valid and within the line limits:
curl -s http://localhost:10002/solve | jq
```

---

## 🏗️ Build Docker Image

To build the docker image, run the following command:

```sh
docker build -t myhub/rest-bv-commit:0.0.1 .

# For MacOS (Apple Silicon) to build AMD64:
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -t myhub/rest-bv-commit:0.0.1 .
```
