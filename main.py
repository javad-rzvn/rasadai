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
MARKET_FILE = 'market.json'
HISTORY_FILE = 'seen_news.txt'

# Robust Headers (Essential for scraping images from news sites)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.google.com/'
}

def get_seen():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def save_seen(urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in urls: f.write(url + '\n')

def fetch_market_rates():
    print(">>> Fetching Dollar Price...")
    url = "https://alanchand.com/en/currencies-price/usd"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # METHOD 1: Look for input field
            input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
            price_toman = 0
            
            if input_tag:
                if input_tag.has_attr('data-price'):
                    price_toman = int(int(input_tag['data-price']) / 10)
                elif input_tag.has_attr('value'):
                     price_toman = int(int(input_tag['value'].replace(',','')) / 10)

            # METHOD 2: JSON-LD
            if price_toman == 0:
                scripts = soup.find_all('script', type='application/ld+json')
                for s in scripts:
                    if '"sku":"USD"' in s.text or '"name":"US Dollar"' in s.text:
                        data = json.loads(s.text)
                        if 'offers' in data and 'price' in data['offers']:
                            price_toman = int(float(data['offers']['price']) / 10)
                            break
            
            if price_toman > 0:
                print(f"   > Success! Price: {price_toman}")
                return {"usd": f"{price_toman:,}", "updated": time.strftime("%H:%M")}
                
    except Exception as e:
        print(f"   > Market Scraping Error: {e}")
    
    return {"usd": "Check Source", "updated": "--:--"}

def get_category_and_sentiment(text):
    t = text.lower()
    tag, color = 'سیاسی', 'primary'
    if 'nuclear' in t or 'atomic' in t: tag, color = 'هسته‌ای', 'warning'
    elif 'attack' in t or 'war' in t or 'military' in t: tag, color = 'نظامی', 'danger'
    elif 'oil' in t or 'currency' in t or 'economy' in t: tag, color = 'اقتصادی', 'success'
    
    blob = TextBlob(text)
    return tag, color, blob.sentiment.polarity

# --- NEW FUNCTION: EXTRACT IMAGE ---
def fetch_article_image(url):
    """
    Visits the article URL and extracts the Open Graph image (og:image).
    """
    try:
        # Timeout is short (5s) so the script doesn't hang on slow sites
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 1. Try Open Graph Image (Standard for social media)
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                return og_image['content']
            
            # 2. Try Twitter Image
            twitter_image = soup.find('meta', name='twitter:image')
            if twitter_image and twitter_image.get('content'):
                return twitter_image['content']
            
            # 3. Fallback: First image tag in body (risky, often returns logos)
            # img_tag = soup.find('img')
            # if img_tag and img_tag.get('src'):
            #    return img_tag['src']
                
    except Exception:
        pass # If fails, return None
    return None

def main():
    print(">>> Starting Radar...")
    
    # 1. MARKET DATA
    market_data = fetch_market_rates()
    try:
        with open(MARKET_FILE, 'w', encoding='utf-8') as f:
            json.dump(market_data, f)
    except: pass

    # 2. NEWS DATA
    print(">>> Fetching News...")
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    
    try:
        results = google_news.get_news(SEARCH_QUERY)
    except Exception as e:
        print(f"News API Error: {e}")
        return

    seen = get_seen()
    new_entries = []
    new_urls = []
    translator = GoogleTranslator(source='auto', target='fa')

    for entry in results:
        url = entry.get('url')
        
        # Google News URLs sometimes need cleaning, but GNews lib usually handles it.
        # If URL is missing https, simple fix:
        if url and not url.startswith('http'):
            url = 'https://' + url

        if url in seen: continue
        
        raw_title = entry.get('title').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')
        
        print(f"   > Processing: {raw_title[:30]}...")

        try:
            # 1. Translate Title
            title_fa = translator.translate(raw_title)
            
            # 2. Analyze Sentiment
            tag, color, sentiment = get_category_and_sentiment(raw_title)
            
            # 3. Fetch Image (This is the fix)
            # Check if GNews gave us one first (rare), otherwise fetch from site
            img_url = entry.get('image')
            if not img_url:
                print("     - Extracting image...")
                img_url = fetch_article_image(url)
            
            # Default placeholder if no image found
            if not img_url:
                img_url = "https://placehold.co/600x400?text=News+Radar"

            new_entries.append({
                "title_fa": title_fa,
                "title_en": raw_title,
                "source": publisher,
                "url": url,
                "image": img_url, 
                "date": date,
                "tag": tag,
                "tag_color": color,
                "sentiment": sentiment
            })
            new_urls.append(url)
        except Exception as e:
            print(f"     - Error processing item: {e}")

    # 3. SAVE NEWS
    if new_entries:
        try:
            with open(NEWS_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
        except: old_data = []
        
        final_data = new_entries + old_data
        final_data = final_data[:60]
        
        with open(NEWS_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
        
        save_seen(new_urls)
        print(f">>> Added {len(new_entries)} news items.")
    else:
        print(">>> No new news.")

if __name__ == "__main__":
    main()
