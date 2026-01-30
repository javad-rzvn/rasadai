import os
import json
import time
import requests
from gnews import GNews
from newspaper import Article, Config
from deep_translator import GoogleTranslator
from textblob import TextBlob # For sentiment analysis

# --- CONFIG ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h'
MAX_RESULTS = 15
JSON_FILE = 'news.json'
HISTORY_FILE = 'seen_news.txt'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'

def get_seen():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def save_seen(urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in urls: f.write(url + '\n')

def get_category_and_sentiment(text):
    """Tags news and calculates sentiment score (-1 to 1)"""
    t = text.lower()
    
    # Tagging
    tag, color = 'سیاسی', 'primary'
    if 'nuclear' in t or 'atomic' in t or 'iaea' in t: tag, color = 'هسته‌ای', 'warning'
    elif 'attack' in t or 'war' in t or 'military' in t or 'drone' in t: tag, color = 'نظامی', 'danger'
    elif 'oil' in t or 'currency' in t or 'rial' in t or 'economy' in t: tag, color = 'اقتصادی', 'success'
    elif 'woman' in t or 'rights' in t or 'protest' in t: tag, color = 'اجتماعی', 'info'
    
    # Sentiment (Simple analysis)
    blob = TextBlob(text)
    sentiment_score = blob.sentiment.polarity 
    
    return tag, color, sentiment_score

def fetch_image_and_clean_url(url):
    """Fetches og:image using Newspaper3k"""
    try:
        # Resolve Google Redirect
        response = requests.head(url, allow_redirects=True, timeout=5)
        final_url = response.url
        
        # Config for Newspaper
        conf = Config()
        conf.browser_user_agent = USER_AGENT
        conf.request_timeout = 5
        
        article = Article(final_url, config=conf)
        article.download()
        article.parse()
        return final_url, article.top_image
    except:
        return url, None

def main():
    print(">>> Radar Scanning...")
    
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    results = google_news.get_news(SEARCH_QUERY)
    
    seen = get_seen()
    new_entries = []
    new_urls = []
    
    translator = GoogleTranslator(source='auto', target='fa')

    for entry in results:
        orig_url = entry.get('url')
        if orig_url in seen: continue
        
        raw_title = entry.get('title').rsplit(' - ', 1)[0] # Remove source name
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')
        
        print(f"   > Processing: {raw_title[:30]}...")

        # 1. Get Image & Final URL
        final_url, image_url = fetch_image_and_clean_url(orig_url)
        
        # 2. Translate & Tag
        try:
            title_fa = translator.translate(raw_title)
            tag, color, sentiment = get_category_and_sentiment(raw_title)
            
            new_entries.append({
                "title_fa": title_fa,
                "title_en": raw_title,
                "source": publisher,
                "url": final_url,
                "image": image_url, # NEW
                "date": date,
                "tag": tag,
                "tag_color": color,
                "sentiment": sentiment # NEW
            })
            new_urls.append(orig_url)
        except Exception as e:
            print(f"Error: {e}")

    # Save
    if new_entries:
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
        except: old_data = []
        
        final_data = new_entries + old_data
        final_data = final_data[:60] # Keep last 60
        
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
        
        save_seen(new_urls)
        print(f">>> Added {len(new_entries)} items.")
    else:
        print(">>> No new items.")

if __name__ == "__main__":
    main()
