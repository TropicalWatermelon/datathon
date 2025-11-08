from fastapi import FastAPI, Request
from utils.ai_client import ask_ai
from dotenv import load_dotenv
import os
import requests

load_dotenv()

app = FastAPI()

# Environment variables
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
FINANCIAL_API_KEY = os.getenv("FINANCIAL_API_KEY")

@app.get("/")
def home():
    return {"message": "MCP server is live! Use POST /query to test."}

@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query = body.get("query", "")

    context = ""

    # --- CONTEXT FETCHING LOGIC ---
    if "weather" in query.lower():
        context = get_weather(query)
    elif ("finance" or "financial") in query.lower() or "stock" in query.lower():
        context = get_financial_context(query)
    elif "news" in query.lower():
        context = get_news_context(query)
    else:
        context = "No external context was found for this query."

    # Combine context + query → send to AI model
    ai_response = ask_ai(query, context)

    return {
        "query": query,
        "context": context,
        "response": ai_response
    }


import os
import requests

def get_weather(city: str):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return "Weather API key not found. Please check your .env file."

    # Clean city input
    city = city.strip().replace("?", "").replace(".", "")

    # Build the API URL
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"

    try:
        res = requests.get(url, timeout=10)
        print("Weather API status:", res.status_code)
        if res.status_code == 200:
            data = res.json()
            desc = data["weather"][0]["description"].capitalize()
            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            humidity = data["main"]["humidity"]
            return (
                f"Weather in {city}: {desc}. "
                f"Temperature: {temp}°C (feels like {feels_like}°C). "
                f"Humidity: {humidity}%."
            )
        else:
            print("Weather API response:", res.text)
            return f"Couldn't fetch weather data for {city}. (status: {res.status_code})"
    except Exception as e:
        print("Weather fetch error:", e)
        return f"Error fetching weather for {city}: {str(e)}"


def get_news_context(query: str) -> str:
    """Fetch top tech headlines using NewsAPI"""
    category = "technology"
    if "sports" in query.lower():
        category = "sports"
    elif "business" in query.lower():
        category = "business"

    url = "https://newsapi.org/v2/top-headlines"
    params = {"country": "us", "category": category, "apiKey": NEWS_API_KEY}

    try:
        response = requests.get(url, params=params)
        data = response.json()

        if data.get("status") != "ok":
            return "Couldn't fetch news data."

        articles = data.get("articles", [])[:3]  # take top 3 headlines
        headlines = [a["title"] for a in articles if a.get("title")]
        summary = " | ".join(headlines) if headlines else "No recent headlines found."

        return f"Top {category} news: {summary}"
    except Exception as e:
        return f"Error fetching news: {e}"

import os
import requests
import re # This import is crucial for the helper function
from typing import Optional

# --- HELPER FUNCTION: Ticker Extraction (MUST be defined first) ---
def extract_ticker(query: str) -> Optional[str]:
    """
    Tries to extract a single stock ticker from the query, prioritizing
    the common '$TICKER' format or plain capitalized 1-5 letter symbols.
    """
    # 1. Look for the $TICKER pattern (e.g., $GOOG, $MSFT)
    # The regex (?<=\$)([A-Z]{1,5}) looks for 1-5 uppercase letters
    # immediately following a dollar sign, without including the dollar sign.
    dollar_match = re.search(r'(?<=\$)([A-Z]{1,5})', query.upper())
    if dollar_match:
        return dollar_match.group(1)

    # 2. Fallback to the original logic (All Caps 1-5 letter word)
    # This is useful for queries like "price of AAPL"
    all_caps_match = re.search(r'\b[A-Z]{1,5}\b', query.upper())
    if all_caps_match:
        ticker = all_caps_match.group(0)
        # Exclude common stop words (can be expanded)
        if ticker not in ["THE", "IS", "WHAT", "FOR", "NEWS", "STOCK", "FINANCE", "ASK"]:
            return ticker
            
    return None


def get_financial_context(query: str) -> str:
    """Fetch the latest stock quote for a ticker found in the query using Alpha Vantage."""
    api_key = os.getenv("FINANCIAL_API_KEY")
    if not api_key:
        return "Financial API key not found. Please check your .env file."

    ticker = extract_ticker(query)
    if not ticker:
        return "Couldn't identify a stock ticker in the query."

    # Alpha Vantage Global Quote Endpoint
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": ticker,
        "apikey": api_key
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
        data = res.json()

        quote = data.get("Global Quote")
        if not quote or len(quote) < 5:
             # This means the ticker was likely not found or the market is closed and data is unavailable
            return f"No financial data available for ticker '{ticker}'. It may not exist."

        # Extract and format key financial metrics
        current_price = quote.get("05. price", "N/A")
        change_percent = quote.get("10. change percent", "N/A")
        volume = int(quote.get("06. volume", 0))

        # Format volume with commas for readability
        formatted_volume = f"{volume:,}"

        return (
            f"Current financial data for **{ticker}**: "
            f"Price: ${current_price}. "
            f"Change: {change_percent} since last close. "
            f"Volume today: {formatted_volume}."
        )

    except requests.exceptions.RequestException as e:
        print("Financial fetch error:", e)
        return f"Error connecting to financial data API: {str(e)}"

# ... (Remaining FastAPI setup, home, and handle_query functions)
# Note: You still need to place your API key for Alpha Vantage into your .env file