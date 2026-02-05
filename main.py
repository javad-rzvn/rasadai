import os
import json
import time
import logging
import requests
import concurrent.futures
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from gnews import GNews
from deep_translator import GoogleTranslator
from textblob import TextBlob
from fake_useragent import UserAgent

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency)',
    'LANGUAGE': 'en',
    'COUNTRY': 'US',
    'PERIOD': '12h',
    'MAX_RESULTS': 30,
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json',
        'HISTORY': 'seen_news.txt'
    },
    'TIMEOUT': 10,
    'MAX_WORKERS': 5  # Number of parallel threads
}

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        self.ua = UserAgent()
        self.translator = GoogleTranslator(source='auto', target='fa')
        self.seen_urls = self._load_seen()

    def _get_headers(self):
        """Generates random headers to avoid bot detection."""
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Referer': 'https://www.google.com/'
        }

    def _load_seen(self):
        if not os.path.exists(CONFIG['FILES']['HISTORY']):
            return set()
        with open(CONFIG['FILES']['HISTORY'], 'r', encoding='utf-8') as f:
            return set(f.read().splitlines())

    def _save_seen(self, new_urls):
        with open(CONFIG['FILES']['HISTORY'], 'a', encoding='utf-8') as f:
            for url in new_urls:
                f.write(url + '\n')

    def fetch_market_rates(self):
        """Fetches USD price from AlanChand with fallback logic."""
        logger.info("Fetching Dollar Price...")
        url = "https://alanchand.com/en/currencies-price/usd"
        
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=CONFIG['TIMEOUT'])
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                price_toman = 0

                # Strategy 1: Input field
                input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
                if input_tag:
                    val = input_tag.get('data-price') or input_tag.get('value')
                    if val:
                        price_toman = int(int(val.replace(',', '')) / 10)

                # Strategy 2: JSON-LD (Fallback)
                if not price_toman:
                    scripts = soup.find_all('script', type='application/ld+json')
                    for s in scripts:
                        if '"sku":"USD"' in s.text:
                            data = json.loads(s.text)
                            try:
                                price_toman = int(float(data['offers']['price']) / 10)
                                break
                            except (KeyError, ValueError):
                                continue
                
                if price_toman > 0:
                    logger.info(f"Market Success: {price_toman}")
                    return {"usd": f"{price_toman:,}", "updated": time.strftime("%H:%M")}
                    
        except Exception as e:
            logger.error(f"Market Fetch Error: {e}")
        
        return {"usd": "N/A", "updated": "--:--"}

    def resolve_url(self, url):
        """
        Follows Google News redirects to get the actual publisher URL.
        Important for scraping the correct OpenGraph image.
        """
        try:
            # allow_redirects=True will follow the google link to the destination
            response = requests.head(url, allow_redirects=True, timeout=5)
            return response.url
        except:
            return url

    def extract_metadata(self, url):
        """
        Fetches the high-res image from the resolved URL.
        """
        image_url = None
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Priority list for images
                meta_checks = [
                    {'property': 'og:image'},
                    {'property': 'og:image:secure_url'},
                    {'name': 'twitter:image'},
                    {'name': 'thumbnail'}
                ]
                
                for check in meta_checks:
                    tag = soup.find('meta', check)
                    if tag and tag.get('content'):
                        img_candidate = tag['content']
                        # Filter out tiny tracking pixels or icons
                        if 'icon' not in img_candidate and len(img_candidate) > 10:
                            image_url = urljoin(url, img_candidate)
                            break
        except Exception:
            pass
            
        return image_url

    def analyze_content(self, text):
        t = text.lower()
        tag, color = 'سیاسی', 'primary'
        
        if any(x in t for x in ['nuclear', 'atomic', 'iaea', 'uranium']):
            tag, color = 'هسته‌ای', 'warning'
        elif any(x in t for x in ['attack', 'war', 'military', 'strike', 'missile', 'drone']):
            tag, color = 'نظامی', 'danger'
        elif any(x in t for x in ['oil', 'currency', 'economy', 'sanction', 'inflation']):
            tag, color = 'اقتصادی', 'success'
        
        polarity = TextBlob(text).sentiment.polarity
        return tag, color, polarity

    def process_single_news_item(self, entry):
        """
        Worker function to process one news item.
        """
        original_url = entry.get('url')
        
        # 1. Check history first to save processing time
        if original_url in self.seen_urls:
            return None

        # 2. Resolve Google Redirect to Real URL
        real_url = self.resolve_url(original_url)
        
        # Double check seen after resolving (sometimes different google links go to same place)
        if real_url in self.seen_urls:
            return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')

        try:
            # 3. Parallel Tasks (Translate, Scrape, Analyze)
            title_fa = self.translator.translate(raw_title)
            tag, color, sentiment = self.analyze_content(raw_title)
            
            # 4. Get Image (Try scraper, fallback to GNews thumb, fallback to placeholder)
            image = self.extract_metadata(real_url)
            if not image:
                image = entry.get('image') # The tiny thumbnail from Google
            if not image:
                image = "https://placehold.co/600x400?text=No+Image"

            return {
                "title_fa": title_fa,
                "title_en": raw_title,
                "source": publisher,
                "url": real_url,
                "image": image,
                "date": date,
                "tag": tag,
                "tag_color": color,
                "sentiment": sentiment,
                "_original_url": original_url # Used for updating seen list
            }
        except Exception as e:
            logger.error(f"Error processing {raw_title[:20]}: {e}")
            return None

    def run(self):
        logger.info(">>> Starting Radar...")

        # 1. Fetch Market Data
        market_data = self.fetch_market_rates()
        with open(CONFIG['FILES']['MARKET'], 'w', encoding='utf-8') as f:
            json.dump(market_data, f)

        # 2. Fetch GNews
        logger.info(">>> Querying GNews...")
        google_news = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS'])
        try:
            results = google_news.get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.critical(f"GNews API Failed: {e}")
            return

        # 3. Process Items in Parallel
        logger.info(f">>> Processing {len(results)} items with {CONFIG['MAX_WORKERS']} threads...")
        new_entries = []
        urls_to_save = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as executor:
            # Submit all tasks
            future_to_entry = {executor.submit(self.process_single_news_item, entry): entry for entry in results}
            
            for future in concurrent.futures.as_completed(future_to_entry):
                result = future.result()
                if result:
                    # Separate the internal tracking URL from the data
                    orig_url = result.pop('_original_url')
                    urls_to_save.append(orig_url)
                    # Also save the real resolved URL to history to prevent duplicates
                    urls_to_save.append(result['url']) 
                    
                    new_entries.append(result)
                    logger.info(f"   + Processed: {result['title_en'][:30]}...")

        # 4. Save Data
        if new_entries:
            try:
                with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                old_data = []

            # Merge and Sort (Optional: sort by date if needed, currently simply appending new on top)
            final_data = new_entries + old_data
            final_data = final_data[:60] # Keep file size manageable

            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f:
                json.dump(final_data, f, ensure_ascii=False, indent=4)

            self._save_seen(urls_to_save)
            logger.info(f">>> Successfully added {len(new_entries)} news items.")
        else:
            logger.info(">>> No new news found.")

if __name__ == "__main__":
    radar = IranNewsRadar()
    radar.run()
