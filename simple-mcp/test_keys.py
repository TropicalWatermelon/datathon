# test_keys.py
import os
from dotenv import load_dotenv
import requests

load_dotenv()

print("OpenAI key present:", bool(os.getenv("OPENAI_API_KEY")))
print("OpenWeather key present:", bool(os.getenv("OPENWEATHER_API_KEY")))
print("NewsAPI key present:", bool(os.getenv("NEWS_API_KEY")))

# quick weather test
wkey = os.getenv("OPENWEATHER_API_KEY")
if wkey:
    r = requests.get("http://api.openweathermap.org/data/2.5/weather",
                     params={"q":"London","appid":wkey,"units":"metric"})
    print("Weather API status:", r.status_code)

# quick news test
nkey = os.getenv("NEWS_API_KEY")
if nkey:
    r = requests.get("https://newsapi.org/v2/top-headlines", params={"country":"us","apiKey":nkey})
    print("News API status:", r.status_code)
