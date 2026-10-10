# Python Agent

A local browser-based chat assistant powered by an OpenAI-compatible language model, with tools for weather, stock quotes, stock news, date and time, and web search. The backend uses FastAPI and streams status and answer events to a lightweight HTML client.

## Features

- Chat with a model served locally by LM Studio or another compatible API.
- Look up current weather through wttr.in.
- Retrieve stock quotes and previous closing prices with yfinance.
- Retrieve ticker news from Yahoo Finance RSS, with Google News RSS as a fallback.
- Get the current date, time, or both.
- Use configurable SearXNG instances for general web searches.
- Stream status and final answer events to the browser using newline-delimited JSON (NDJSON).

## Requirements

- Python 3 and pip
- LM Studio, or another server exposing an OpenAI-compatible chat-completions API
- Network access for weather, stock, news, and search tools

## Setup

Clone the repository and enter the project directory:

```bash
git clone <repository-url>
cd <repository-directory>
```

Create a virtual environment and install the dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate the environment with `venv\Scripts\activate` instead.

## Configure LM Studio

In LM Studio, load a chat model and start the local server with the OpenAI-compatible API enabled. The default API URL is `http://localhost:1234/v1`.

The application defaults to the model identifier `google/gemma-4-26b-a4b-qat`. Set environment variables to use a different model or API URL. You can put these in a `.env` file in the project directory:

```dotenv
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_MODEL=google/gemma-4-26b-a4b-qat
```

The configured model identifier must be available to the server at the configured API URL.

## Run

Start the backend from the project directory with the virtual environment activated:

```bash
python main.py
```

The API listens on `http://localhost:3000`. Open `index.html` in a browser to use the chat page. On macOS, you can open it from the project directory with:

```bash
open index.html
```

### Port behavior

When launched with `python main.py`, the application attempts to terminate any process already using port 3000 before starting the server. Check that nothing important is using that port before launching; the process is force-killed.

## Example prompts

- `What is the weather in Mechanicville, NY?`
- `What was the last closing price for AMZN and IBM?`
- `Get the latest news on AMZN and IBM.`
- `What are the current date and time?`
- `Search the web for recent developments in renewable energy.`

Ticker news and quote lookups use recognized ticker symbols. General web search uses the configured SearXNG service.

## API

The browser sends a `POST` request to `/chat` with a JSON body:

```json
{"prompt":"What is the weather in Mechanicville, NY?"}
```

The response is a stream of newline-delimited JSON events. Events use `status`, `final`, or `error` types. To inspect the stream from a terminal:

```bash
curl -N http://localhost:3000/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"What is the weather in Mechanicville, NY?"}'
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | OpenAI-compatible model API URL |
| `LM_STUDIO_MODEL` | `google/gemma-4-26b-a4b-qat` | Model identifier sent to the API |
| `SEARX_BASE_URL` | `https://priv.au` | Primary SearXNG search endpoint |
| `SEARCH_FALLBACK_URL` | `https://search.ononoki.org` | Fallback SearXNG search endpoint |

SearXNG endpoints must be reachable and allow JSON search responses. Public instances can rate-limit requests or become unavailable; configure an instance you trust for more reliable general web search. Ticker news uses Yahoo Finance RSS first and Google News RSS as a fallback, independently of SearXNG.

## Troubleshooting and safety

- **Model connection errors:** Confirm the model is loaded and the LM Studio server is running. Verify `LM_STUDIO_BASE_URL` and `LM_STUDIO_MODEL`.
- **Search errors or HTTP 429:** The configured SearXNG endpoint may be unavailable or rate-limiting requests. Set `SEARX_BASE_URL` and `SEARCH_FALLBACK_URL` to reachable instances.
- **Port 3000 is in use:** The startup script attempts to force-terminate its listener. Inspect the process first if it may belong to another application.
- **External data:** Weather, news, search, and finance results depend on third-party services and may be unavailable or delayed. Stock information is not financial advice.
- **Local use only:** The backend binds to all network interfaces and currently enables permissive CORS without authentication. Do not expose it to an untrusted network without adding appropriate access controls.

## Project files

- [`main.py`](main.py): FastAPI server, streaming chat route, request routing, and model/tool-call handling.
- [`tools.py`](tools.py): Weather, finance, news, date/time, and search integrations.
- [`index.html`](index.html): Browser chat interface.
- [`adr/001-architecture-decisions.md`](adr/001-architecture-decisions.md): Architecture decisions and implementation rationale.
