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
        context = get_weather_context(query)
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
