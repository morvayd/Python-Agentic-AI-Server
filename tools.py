import datetime
import httpx
import yfinance as yf
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import List, Union

# Configuration
SEARCH_ENDPOINTS = [
    os.getenv('SEARX_BASE_URL', 'https://priv.au'),
    os.getenv('SEARCH_FALLBACK_URL', 'https://search.ononoki.org')
]
MAX_SEARCH_ATTEMPTS = 4
SEARCH_TIMEOUT = 5.0

async def current_date() -> str:
    return datetime.date.strftime(datetime.date.today(), "%A, %B %d, %Y")

async def current_time() -> str:
    return datetime.datetime.now().strftime("%I:%M:%S %p")

async def current_datetime() -> str:
    return datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M:%S %p")

async def model_identity(detail: str) -> str:
    model_name = os.getenv('LM_STUDIO_MODEL', 'google/gemma-4-26b-a4b-qat')
    
    if detail == 'model_name':
        return f"The configured LM Studio model name is {model_name}."
    elif detail == 'creator':
        # Trying to extract provider from model name if possible
        if '/' in model_name:
            provider = model_name.split('/')[0]
        else:
            provider = "not specified"
        return f"The configured LM Studio model provider is {provider}."
    else:
        return "Error: detail must be either 'model_name' or 'creator'."

async def searxng_search(query: str) -> str:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        for endpoint in SEARCH_ENDPOINTS:
            try:
                # Simplistic implementation for now
                # SearXNG API often returns JSON if specified in headers or via params
                response = await client.get(
                    f"{endpoint}/search",
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": "PythonAgent/1.0"}
                )
                if response.status_code == 200:
                    data = response.json()
                    results = data.get('results', [])
                    if not results:
                        continue
                    
                    # Format results for LLM
                    formatted_results = []
                    for res in results[:5]: # limit to top 5
                        formatted_results.append(f"Title: {res.get('title')}\nURL: {res.get('url')}\nSnippet: {res.get('content')}\n")
                    return "\n".join(formatted_results)
            except Exception as e:
                print(f"Search error with {endpoint}: {e}")
                continue
                
    return f"Search failed for '{query}'. No results found or error occurred."


async def stock_news(symbols: Union[str, List[str]]) -> str:
    if isinstance(symbols, str):
        symbols = [symbols]

    results = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PythonAgent/1.0)"}

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
        for symbol in symbols:
            feeds = [
                (
                    "https://feeds.finance.yahoo.com/rss/2.0/headline",
                    {"s": symbol, "region": "US", "lang": "en-US"},
                ),
                (
                    "https://news.google.com/rss/search",
                    {"q": f"{symbol} stock news", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                ),
            ]
            items = []

            for url, params in feeds:
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    channel = ET.fromstring(response.content).find("channel")
                    feed_items = channel.findall("item") if channel is not None else []
                    items = [
                        {
                            "title": item.findtext("title", default="").strip(),
                            "link": item.findtext("link", default="").strip(),
                            "date": item.findtext("pubDate", default="").strip(),
                        }
                        for item in feed_items
                        if item.findtext("title") and item.findtext("link")
                    ]
                    if items:
                        break
                except (httpx.HTTPError, ET.ParseError) as exc:
                    logger_message = f"News feed error for {symbol} from {url}: {exc}"
                    print(logger_message)

            if items:
                articles = [f"- {item['title']}\n  {item['date']}\n  {item['link']}" for item in items[:5]]
                results.append(f"News for {symbol}:\n" + "\n".join(articles))
            else:
                results.append(f"No recent news found for {symbol}.")

    return "\n\n".join(results)

async def stock_search(symbols: Union[str, List[str]], fields: List[str] = None) -> str:
    if isinstance(symbols, str):
        symbols = [symbols]
    
    if not fields:
        fields = ['currentPrice', 'regularMarketChange', 'regularMarketChangePercent', 'marketCap']
        
    results = []
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Build result string
            stock_info = f"Stock: {symbol}\n"
            found_fields = False
            for field in fields:
                # mapping typical yahoo finance fields to what's expected
                # yfinance .info keys might differ slightly from what user expects
                # but we'll try to match loosely
                val = info.get(field) or info.get(f"regularMarket{field}") or info.get(f"{field}Price")
                if val is not None:
                    stock_info += f"- {field}: {val}\n"
                    found_fields = True
            
            if found_fields:
                results.append(stock_info)
            else:
                results.append(f"No detailed info found for {symbol}.")
        except Exception as e:
            results.append(f"Error fetching stock info for {symbol}: {str(e)}")
            
    return "\n\n".join(results)

async def weather_search(location: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            safe_location = quote(location, safe="")
            url = f"https://wttr.in/{safe_location}?format=3"
            response = await client.get(url)
            if response.status_code == 200:
                return response.text.strip()
            else:
                return f"Error: Weather service returned status {response.status_code}"
        except Exception as e:
            return f"Error fetching weather: {str(e)}"
