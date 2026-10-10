# Architecture Decision Record (ADR): Autonomous AI Agent Backend

## Status
**Accepted**

## Context
The project provides a browser chat interface backed by a FastAPI service and an OpenAI-compatible language model, typically running locally in LM Studio. The service can use external tools for current date and time, weather, stock quotes and news, and general web search.

The architecture must support asynchronous requests and streamed status, multiple model/tool rounds for open-ended tasks, reliable direct answers for common data lookups, and portability across OpenAI-compatible model servers.

## Decisions

### ADR 001: Use of FastAPI and Asynchronous I/O
**Decision:** Use **FastAPI** for the HTTP API and asynchronous request handling. Use asynchronous clients for the model API and HTTP-based tools where available.

**Rationale:**
*   **Performance:** FastAPI is built on Starlette and is highly efficient for handling many concurrent connections.
*   **Async Support:** The OpenAI-compatible client and `httpx` support `async/await`, allowing model and HTTP requests to avoid blocking the server while waiting on network I/O.
*   **Streaming Support:** FastAPI provides excellent support for `StreamingResponse`, which is critical for our requirement of streaming agent status updates.

**Consequences:**
*   **Pros:** The server can stream progress and handle asynchronous network requests.
*   **Cons:** Async streaming requires careful error handling. Not every dependency is inherently non-blocking; the finance integration uses `yfinance` inside an async tool wrapper.

---

### ADR 002: Agent Loop and Direct Tool Responses
**Decision:** Use a bounded iterative model/tool loop for general multi-step requests, with a maximum of `MAX_TOOL_ROUNDS` iterations. Handle recognizable date/time, weather, stock quote, and stock-news requests directly where possible, returning tool output without asking the model to rewrite it.

**Rationale:**
*   **Autonomous Capability:** Traditional "one-shot" LLM interactions are insufficient for tasks like "Find the stock price of Apple and tell me the weather in its headquarters." The model needs to see the result of the first tool call to decide the second.
*   **State Management:** In the general loop, assistant tool-call text and tool results are appended to the message history before the next model request. Tool results are added as system messages.
*   **Reliable Lookups:** Direct responses avoid empty, duplicated, or malformed model rewrites of simple tool results.

**Consequences:**
*   **Pros:** Supports multi-step tool use while keeping common data lookups concise and independent of a second model completion.
*   **Cons:** Direct routing relies on request matching and ticker extraction; ambiguous requests continue through the model loop. The loop is bounded, but message history and token use can grow with each round.

---

### ADR 003: Structured Tool Calling via Pseudo-XML Tags
**Decision:** Instruct the model to request tools using the XML-like syntax `<tool_call name="TOOL_NAME" args='{"arg": "val"}' />`. The parser also tolerates common function-call and bare-text variants emitted by local models.

**Rationale:**
*   **Model Agnostic:** While OpenAI provides a native "function calling" API, many local/open-source models (via LM Studio, Ollama, etc.) do not support it natively or may struggle with it. A text-based instruction/regex-based parsing approach is highly portable across different LLM providers.
*   **Compatibility:** A documented format gives the model a preferred shape, while tolerant parsing helps accommodate small output variations.

**Consequences:**
*   **Pros:** Works with models that do not provide native function calling through the compatible API.
*   **Cons:** Text parsing is less structured than native tool calls and remains sensitive to malformed model output.

---

### ADR 004: Real-time Status Streaming via NDJSON
**Decision:** The `/chat` endpoint uses `StreamingResponse` to emit one **Newline Delimited JSON (NDJSON)** event per line. Events have `status`, `final`, or `error` types.

**Rationale:**
*   **User Transparency:** Agentic processes can take several seconds. To prevent the user from feeling the system has "hung," we stream "status" messages (e.g., `{"type": "status", "message": "Thinking..."}`) before the final result is delivered.
*   **Interactivity:** The frontend reads events as they arrive and displays progress, such as a tool lookup, before displaying the final answer.

**Consequences:**
*   **Pros:** Significantly improved User Experience (UX).
*   **Cons:** The frontend must buffer partial chunks and parse complete newline-delimited events rather than a single JSON response.

---

### ADR 005: Specialized Data Sources
**Decision:** Use `yfinance` for stock quotes; Yahoo Finance RSS, with Google News RSS as a fallback, for ticker news; `httpx` with `wttr.in` for weather; and configured SearXNG endpoints for general web search. Parse RSS with Python's standard XML library.

**Rationale:**
*   **Domain Sources:** Specialized feeds and libraries provide structured results without asking the language model to invent current data.
*   **Low Setup Cost:** RSS and the existing HTTP/Yahoo Finance integrations do not require an additional search API key.
*   **General Search:** SearXNG remains available for open-ended web queries and can be configured through environment variables.

**Consequences:**
*   **Pros:** Common finance, news, and weather queries use direct data sources and return links or values from those sources.
*   **Cons:** External services can rate-limit, change, or become unavailable. General SearXNG search depends on reachable, correctly configured instances.

---

### ADR 006: Local OpenAI-Compatible Model
**Decision:** Use the OpenAI Python client against an OpenAI-compatible endpoint, configured by `LM_STUDIO_BASE_URL` and `LM_STUDIO_MODEL`, with local LM Studio defaults.

**Rationale:** This preserves a familiar client interface while allowing the model server and model identifier to be changed through configuration.

**Consequences:** The server must be running and expose a compatible chat-completions API. Model behavior, including adherence to the tool-call prompt, varies by model.

***

### Summary Table of Architecture

| Component | Technology / Pattern | Purpose |
| :--- | :--- | :--- |
| **Web Framework** | FastAPI | High-performance async API |
| **Execution Model** | Bounded model/tool loop plus direct lookup paths | Multi-step reasoning and dependable common lookups |
| **Communication** | NDJSON streaming | Status, final answer, and error delivery |
| **Tool Protocol** | XML-like pseudo-tags with tolerant parsing | Text-based model compatibility |
| **Data Fetching** | `yfinance`, Yahoo/Google News RSS, `httpx`/`wttr.in`, SearXNG | Quotes, news, weather, and general search |
| **LLM Interface** | Configurable OpenAI-compatible API | Local LM Studio defaults and compatible alternatives |
