import os
import json
import time
import requests
from bs4 import BeautifulSoup
from gnews import GNews
from deep_translator import GoogleTranslator
from textblob import TextBlob

# --- CONFIG ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h'
MAX_RESULTS = 15
NEWS_FILE = 'news.json'
MARKET_FILE = 'market.json' # NEW FILE FOR PRICE
HISTORY_FILE = 'seen_news.txt'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_seen():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def save_seen(urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in urls: f.write(url + '\n')

# --- NEW: SCRAPE ALANCHAND ---
def fetch_market_rates():
    print(">>> Fetching Dollar Price from AlanChand...")
    url = "https://alanchand.com/en/currencies-price/usd"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Method 1: Try to find the input with data-price (Most accurate in your HTML)
        # <input ... data-price="1602000">
        input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
        
        price_irr = 0
        
        if input_tag and input_tag.has_attr('data-price'):
            price_irr = int(input_tag['data-price'])
        else:
            # Method 2: Fallback to JSON-LD Schema
            scripts = soup.find_all('script', type='application/ld+json')
            for s in scripts:
                if '"sku":"USD"' in s.text:
                    data = json.loads(s.text)
                    price_irr = int(data['offers']['price'])
                    break
        
        if price_irr > 0:
            price_toman = int(price_irr / 10) # Convert Rial to Toman
            return {
                "usd": f"{price_toman:,}", # Format: 160,200
                "updated": time.strftime("%H:%M")
            }
            
    except Exception as e:
        print(f"Error fetching market data: {e}")
    
    return {"usd": "N/A", "updated": "--:--"}

def get_category_and_sentiment(text):
    t = text.lower()
    tag, color = 'سیاسی', 'primary'
    if 'nuclear' in t or 'atomic' in t: tag, color = 'هسته‌ای', 'warning'
    elif 'attack' in t or 'war' in t or 'military' in t: tag, color = 'نظامی', 'danger'
    elif 'oil' in t or 'currency' in t or 'economy' in t: tag, color = 'اقتصادی', 'success'
    
    blob = TextBlob(text)
    return tag, color, blob.sentiment.polarity

def main():
    print(">>> Radar Scanning...")
    
    # 1. FETCH MARKET DATA
    market_data = fetch_market_rates()
    with open(MARKET_FILE, 'w', encoding='utf-8') as f:
        json.dump(market_data, f)

    # 2. FETCH NEWS
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    results = google_news.get_news(SEARCH_QUERY)
    
    seen = get_seen()
    new_entries = []
    new_urls = []
    translator = GoogleTranslator(source='auto', target='fa')

    for entry in results:
        url = entry.get('url')
        if url in seen: continue
        
        raw_title = entry.get('title').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')
        
        print(f"   > Processing: {raw_title[:30]}...")

        try:
            title_fa = translator.translate(raw_title)
            tag, color, sentiment = get_category_and_sentiment(raw_title)
            
            # Try to get image (Basic method)
            image_url = None
            
            new_entries.append({
                "title_fa": title_fa,
                "title_en": raw_title,
                "source": publisher,
                "url": url,
                "image": image_url,
                "date": date,
                "tag": tag,
                "tag_color": color,
                "sentiment": sentiment
            })
            new_urls.append(url)
        except Exception as e:
            print(f"Error: {e}")

    if new_entries:
        try:
            with open(NEWS_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
        except: old_data = []
        
        final_data = new_entries + old_data
        final_data = final_data[:60]
        
        with open(NEWS_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
        
        save_seen(new_urls)
        print(f">>> Added {len(new_entries)} items.")

if __name__ == "__main__":
    main()
