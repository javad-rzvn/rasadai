import os
import json
import time
from gnews import GNews
from deep_translator import GoogleTranslator

# --- CONFIG ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h'
MAX_RESULTS = 20
JSON_FILE = 'news.json'
HISTORY_FILE = 'seen_news.txt'

def get_seen():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def save_seen(urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in urls: f.write(url + '\n')

def get_category(text):
    """Auto-tags the news based on keywords"""
    t = text.lower()
    if 'nuclear' in t or 'atomic' in t: return 'هسته‌ای', 'warning'
    if 'israel' in t or 'attack' in t or 'war' in t or 'strike' in t: return 'نظامی/امنیتی', 'danger'
    if 'protest' in t or 'rights' in t or 'woman' in t: return 'اجتماعی', 'info'
    if 'sanction' in t or 'oil' in t or 'currency' in t: return 'اقتصادی', 'success'
    return 'سیاسی', 'primary'

def clean_title(title):
    """Removes the Source Name from the end of the title (e.g., '... - CNN')"""
    if ' - ' in title:
        return title.rsplit(' - ', 1)[0]
    return title

def main():
    print(">>> Radar Update Started...")
    
    # 1. Fetch
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    results = google_news.get_news(SEARCH_QUERY)
    
    seen = get_seen()
    new_entries = []
    new_urls = []
    
    translator = GoogleTranslator(source='auto', target='fa')

    for entry in results:
        url = entry.get('url')
        if url in seen: continue
        
        raw_title = entry.get('title')
        clean_tit = clean_title(raw_title)
        publisher = entry.get('publisher', {}).get('title', 'News')
        date = entry.get('published date')
        
        print(f"Translating: {clean_tit[:30]}...")
        
        try:
            title_fa = translator.translate(clean_tit)
            tag_label, tag_color = get_category(raw_title)
            
            new_entries.append({
                "title_fa": title_fa,
                "title_en": clean_tit,
                "source": publisher,
                "url": url,
                "date": date,
                "tag": tag_label,
                "tag_color": tag_color
            })
            new_urls.append(url)
            time.sleep(0.5) # Be nice to translator API
        except Exception as e:
            print(f"Error: {e}")

    # 2. Save (Prepend new items to existing list)
    if new_entries:
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
        except: old_data = []
        
        # Merge and limit to 60 items
        final_data = new_entries + old_data
        final_data = final_data[:60]
        
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
        
        save_seen(new_urls)
        print(f">>> Successfully added {len(new_entries)} headlines.")
    else:
        print(">>> No new headlines found.")

if __name__ == "__main__":
    main()
