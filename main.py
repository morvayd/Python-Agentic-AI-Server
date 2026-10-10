import os
import json
import re
import asyncio
import logging
import subprocess
import time
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from dotenv import load_dotenv

import tools

# ---------------------------------------------------------------------------
# Logging / startup setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-backend")

print("\n---------- Python Agent Server Started ----------")

# Load environment variables
load_dotenv()

MODEL_NAME = os.getenv("LM_STUDIO_MODEL", "google/gemma-4-26b-a4b-qat")
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
MAX_TOOL_ROUNDS = 6


def kill_process_on_port(port: int):
    """Stop whatever is listening on the requested port, if anything."""
    try:
        result = subprocess.check_output(
            ["lsof", "-t", f"-i:{port}"],
            stderr=subprocess.STDOUT,
        ).decode().splitlines()

        if not result:
            logger.info(f"Port {port} is free.")
            return

        for pid in result:
            logger.info(f"Port {port} is in use by PID {pid}. Killing process...")
            subprocess.run(["kill", "-9", pid], check=True)

        time.sleep(1)
        logger.info(f"Processes on port {port} were killed successfully.")
    except subprocess.CalledProcessError:
        logger.info(f"Port {port} is free.")
    except Exception as exc:
        logger.error(f"Error while trying to kill process on port {port}: {exc}")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AsyncOpenAI(
    base_url=LM_STUDIO_BASE_URL,
    api_key="not-needed",
)


TOOL_NAMES = {
    "current_date",
    "current_time",
    "current_datetime",
    "model_identity",
    "searxng_search",
    "stock_search",
    "stock_news",
    "weather_search",
}


def _strip_wrapping_quotes(value: str) -> str:
    if value is None:
        return ""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _extract_function_call_args(args_text: str):
    args_text = args_text.strip()
    if not args_text:
        return {}

    if args_text.startswith("{"):
        try:
            return json.loads(args_text)
        except json.JSONDecodeError:
            pass

    kwargs = {}
    matches = re.finditer(
        r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"(?P<double>.*?)"|\'(?P<single>.*?)\'|(?P<bare>[^,]+))',
        args_text,
        flags=re.DOTALL,
    )
    for match in matches:
        key = match.group("key").strip()
        value = match.group("double") or match.group("single") or match.group("bare")
        kwargs[key] = _strip_wrapping_quotes(value)

    if kwargs:
        return kwargs

    clean_value = _strip_wrapping_quotes(args_text)
    return {"location": clean_value} if clean_value else {}


def extract_weather_location(prompt: str) -> str:
    if not prompt:
        return ""

    match = re.search(r'\bweather\b(?:\s+\w+){0,4}\s+(?:in|for|at|around|of)\s+(.+?)(?:\?|\.|!|$)', prompt, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip().rstrip('?.!')
        if value:
            return value

    match = re.search(r'\b(?:in|for|at)\s+([A-Za-z0-9 ,.-]+)', prompt, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip('?.!')

    return ""


def extract_stock_symbols(prompt: str):
    excluded = {"A", "I", "AI", "CEO", "ETF", "ETFS", "NASDAQ", "NYSE", "THE", "USD"}
    candidates = re.findall(r'(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9.-]{0,5})(?![A-Za-z0-9])', prompt)
    return list(dict.fromkeys(symbol for symbol in candidates if symbol not in excluded))


# ---------------------------------------------------------------------------
# Output sanitization and tool-call parsing
# ---------------------------------------------------------------------------
def sanitize_final_text(text: str) -> str:
    """Remove only tool-call artifacts and obvious junk prefixes; leave normal chat text alone."""
    if not text:
        return ""

    cleaned = text.strip()

    # Remove explicit tool-call tags if the model emitted them in the answer.
    cleaned = re.sub(r'(?is)<\s*tool_call\b.*?</\s*tool_call\s*>', '', cleaned)
    cleaned = re.sub(r'(?is)<\s*tool_call\b[^>]*?/?>', '', cleaned)

    # Remove common prefix junk like: channel>, <channel>, pipe, stray angle brackets.
    cleaned = re.sub(r'(?is)^\s*(?:channel\s*>|<\s*channel\s*>|\|\s*)+', '', cleaned)
    cleaned = re.sub(r'(?is)^\s*<+\s*', '', cleaned)
    cleaned = re.sub(r'(?is)^\s*>+', '', cleaned)
    cleaned = cleaned.replace('|', '')

    # Remove only leading punctuation noise.
    cleaned = re.sub(r'(?is)^\s*[:\-\s]+', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned if cleaned else ""


def parse_tool_call(text: str):
    """Extract a tool call from model output and return (tool_name, args_dict)."""
    if not text:
        return None, {}

    candidate = text.strip()

    # Tolerate garbage prefixes from local models.
    candidate = re.sub(r'(?is)^\s*(?:channel\s*>|<\s*channel\s*>|\|\s*)+', '', candidate)
    candidate = re.sub(r'(?is)<\s*tool_call\s*\|', '<tool_call', candidate)

    # Function-call style: weather_search("Mechanicville, NY")
    func_match = re.search(
        r'(?is)\b(?P<name>current_date|current_time|current_datetime|model_identity|searxng_search|stock_search|stock_news|weather_search)\s*\((?P<args>.*?)(?:\)\s*\Z|\)\s*\n)',
        candidate,
    )
    if func_match:
        tool_name = func_match.group("name")
        args = _extract_function_call_args(func_match.group("args"))
        if tool_name in {"current_date", "current_time", "current_datetime"}:
            return tool_name, {}
        return tool_name, args

    # Bare token form: tool_call weather_search location="Mechanicville, NY"
    legacy_match = re.search(
        r'(?is)\b(?:tool_call|toolcall)\b\s+(?P<name>current_date|current_time|current_datetime|model_identity|searxng_search|stock_search|stock_news|weather_search)\s+(?P<args>.+)',
        candidate,
    )
    if legacy_match:
        tool_name = legacy_match.group("name")
        args = _extract_function_call_args(legacy_match.group("args"))
        if tool_name in {"current_date", "current_time", "current_datetime"}:
            return tool_name, {}
        return tool_name, args

    patterns = [
        r'<\s*tool_call\s+name\s*=\s*(?P<q1>["\'])?(?P<name>[A-Za-z0-9_]+)(?P=q1)\s+args\s*=\s*(?:(?P<q2>["\'])(?P<args>.*?)(?P=q2)|(?P<args_json>\{.*?\}))\s*/?\s*>',
        r'<\s*tool_call\s+name\s*=\s*(?P<q1>["\'])(?P<name>[A-Za-z0-9_]+)(?P=q1)\s+args\s*=\s*(?P<args>\{.*?\})\s*/?\s*>',
    ]

    for pattern in patterns:
        match = re.search(pattern, candidate, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            continue

        tool_name = match.group('name')
        raw_args = match.group('args')
        if raw_args is None:
            raw_args = match.group('args_json')
        if raw_args is None:
            continue

        args_raw = raw_args.strip()
        if args_raw.startswith('"') and args_raw.endswith('"'):
            args_raw = args_raw[1:-1]

        try:
            return tool_name, json.loads(args_raw)
        except json.JSONDecodeError:
            try:
                return tool_name, json.loads(args_raw.replace("'", '"'))
            except json.JSONDecodeError:
                return tool_name, {}

    return None, {}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def get_system_prompt() -> str:
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    time_str = datetime.now().strftime("%I:%M:%S %p")

    return f"""You are an autonomous agent with access to several tools.
Your goal is to assist the user by executing tasks.

Current system date and time: {date_str} at {time_str}

Available tools:
1. current_date(): Returns the current local calendar date.
2. current_time(): Returns the current local time.
3. current_datetime(): Returns the current local date and time.
4. model_identity(detail): Returns the configured LM Studio model name or its provider.
5. searxng_search(query): Searches the web using SearXNG.
6. stock_search(symbols, fields): Gets quotes for one or more stock symbols and requested quote fields.
7. stock_news(symbols): Gets recent news articles for one or more stock symbols.
8. weather_search(location): Gets current weather.

Rules:
- If you need to use a tool, respond with exactly this format:
  <tool_call name="TOOL_NAME" args='{{"arg1": "val1", "arg2": "val2"}}' />
- If a tool requires no arguments, send args='{{}}' exactly.
- Use no arguments for current_date, current_time, current_datetime.
- For model_identity, use detail='model_name' or detail='creator'.
- For searxng_search, use query.
- For stock_search, use symbols and optional fields.
- For stock_news, use symbols (an array for multiple tickers).
- For weather_search, use location.
- If you are not using a tool, answer naturally as a helpful assistant.
"""


# ---------------------------------------------------------------------------
# Chat route
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_prompt = data.get("prompt")
    if not user_prompt:
        raise HTTPException(status_code=400, detail="No prompt provided")

    async def event_generator():
        messages = [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": user_prompt},
        ]

        asks_date = re.search(r'\b(?:date|today|today\'s date)\b', user_prompt, flags=re.IGNORECASE)
        asks_time = re.search(r'\b(?:time|clock)\b', user_prompt, flags=re.IGNORECASE)
        if asks_date or asks_time:
            if asks_date and asks_time:
                tool_name = "current_datetime"
            elif asks_date:
                tool_name = "current_date"
            else:
                tool_name = "current_time"
            yield json.dumps({"type": "status", "message": "Checking the date and time"}) + "\n"
            result = await getattr(tools, tool_name)()
            yield json.dumps({"type": "final", "content": result}) + "\n"
            return

        if "weather" in user_prompt.lower():
            location = extract_weather_location(user_prompt)
            if location:
                yield json.dumps({"type": "status", "message": "Checking the weather"}) + "\n"
                result = await tools.weather_search(location)
                yield json.dumps({"type": "final", "content": result}) + "\n"
                return

        news_request = re.search(r'\b(?:news|headlines|articles)\b', user_prompt, flags=re.IGNORECASE)
        news_symbols = extract_stock_symbols(user_prompt)
        if news_request and news_symbols:
            yield json.dumps({"type": "status", "message": f"Looking up news for {', '.join(news_symbols)}"}) + "\n"
            result = await tools.stock_news(news_symbols)
            yield json.dumps({"type": "final", "content": result}) + "\n"
            return

        stock_request = re.search(
            r'\b(?:stock|stocks|share|shares|ticker|quote|price|closing)\b',
            user_prompt,
            flags=re.IGNORECASE,
        )
        stock_symbols = extract_stock_symbols(user_prompt)
        if stock_request and stock_symbols:
            yield json.dumps({"type": "status", "message": f"Looking up {', '.join(stock_symbols)}"}) + "\n"
            asks_for_close = re.search(
                r'\b(?:last|previous|most recent)\s+clos(?:e|ing)\b|\bclosing price\b|\bprevious close\b',
                user_prompt,
                flags=re.IGNORECASE,
            )
            fields = ["previousClose"] if asks_for_close else None
            result = await tools.stock_search(stock_symbols, fields=fields)
            yield json.dumps({"type": "final", "content": result}) + "\n"
            return

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                )

                message_content = response.choices[0].message.content
                if not message_content:
                    yield json.dumps({"type": "final", "content": ""}) + "\n"
                    break

                tool_name, args = parse_tool_call(message_content)

                if tool_name:
                    yield json.dumps({"type": "status", "message": f"Executing tool: {tool_name}"}) + "\n"

                    func = getattr(tools, tool_name, None)
                    if func and asyncio.iscoroutinefunction(func):
                        result = await func(**args)
                    elif func and not asyncio.iscoroutinefunction(func):
                        result = func(**args)
                    else:
                        result = f"Error: Tool '{tool_name}' not found or not callable."

                    if tool_name in {
                        "current_date",
                        "current_time",
                        "current_datetime",
                        "stock_search",
                        "stock_news",
                    }:
                        yield json.dumps({"type": "final", "content": str(result)}) + "\n"
                        return

                    messages.append({"role": "assistant", "content": message_content})
                    messages.append({"role": "system", "content": f"Tool result from {tool_name}: {result}"})
                    continue

                cleaned_content = sanitize_final_text(message_content)
                yield json.dumps({"type": "final", "content": cleaned_content}) + "\n"
                break

            except Exception as exc:
                logger.error(f"Error in chat loop: {exc}")
                yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
                break
        else:
            yield json.dumps({"type": "error", "message": "Maximum tool rounds reached."}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    kill_process_on_port(3000)
    uvicorn.run(app, host="0.0.0.0", port=3000)
