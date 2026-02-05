import os
import json
import time
import logging
import requests
import urllib.parse
import concurrent.futures
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from gnews import GNews
from fake_useragent import UserAgent

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency)',
    'LANGUAGE': 'en',
    'COUNTRY': 'US',
    'PERIOD': '6h', # Shorter period to save AI credits on old news
    'MAX_RESULTS': 20,
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json',
        'HISTORY': 'seen_news.txt'
    },
    'TIMEOUT': 10,
    'MAX_WORKERS': 4, # Reduce threads slightly to be gentle on API
    'POLLINATIONS_KEY': os.environ.get('POLLINATIONS_API_KEY') # Loaded from GitHub Secrets
}

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        self.ua = UserAgent()
        self.seen_urls = self._load_seen()
        self.api_key = CONFIG['POLLINATIONS_KEY']
        
        if not self.api_key:
            logger.warning("⚠️ No Pollinations API Key found! AI features will fail.")

    def _get_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.google.com/'
        }

    def _load_seen(self):
        if not os.path.exists(CONFIG['FILES']['HISTORY']): return set()
        with open(CONFIG['FILES']['HISTORY'], 'r', encoding='utf-8') as f:
            return set(f.read().splitlines())

    def _save_seen(self, new_urls):
        with open(CONFIG['FILES']['HISTORY'], 'a', encoding='utf-8') as f:
            for url in new_urls: f.write(url + '\n')

    # --- MARKET DATA (unchanged) ---
    def fetch_market_rates(self):
        url = "https://alanchand.com/en/currencies-price/usd"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=CONFIG['TIMEOUT'])
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                price = 0
                input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
                if input_tag:
                    val = input_tag.get('data-price') or input_tag.get('value')
                    if val: price = int(int(val.replace(',', '')) / 10)
                
                if price > 0: return {"usd": f"{price:,}", "updated": time.strftime("%H:%M")}
        except Exception: pass
        return {"usd": "N/A", "updated": "--:--"}

    # --- AI ANALYSIS (TEXT) ---
    def analyze_with_ai(self, text):
        """
        Uses Pollinations AI (OpenAI model) to Translate, Tag, and Sentiment Check.
        """
        if not self.api_key:
            return None
            
        url = "https://gen.pollinations.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        system_prompt = (
            "You are an intelligence analyst. Receive a news headline. "
            "Return a JSON object with 3 keys: "
            "1. 'fa': Persian translation (news style). "
            "2. 'tag': One category from [نظامی, هسته‌ای, اقتصادی, سیاسی]. "
            "3. 'sentiment': A float score between -1.0 (Negative) and 1.0 (Positive). "
            "Return ONLY raw JSON."
        )

        payload = {
            "model": "openai",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.3
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            
            # clean potential markdown code blocks
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            return None

    # --- AI IMAGE GENERATION ---
    def generate_ai_image(self, prompt):
        """
        Generates an image URL using Pollinations Flux model if scraping fails.
        """
        try:
            # Clean prompt for URL
            safe_prompt = urllib.parse.quote(f"Editorial news illustration, {prompt}, photorealistic, 4k, dark style")
            image_url = f"https://gen.pollinations.ai/image/{safe_prompt}?model=flux&width=800&height=600&nologo=true"
            return image_url
        except Exception:
            return "https://placehold.co/800x600?text=News"

    # --- MAIN PROCESSOR ---
    def process_single_news_item(self, entry):
        original_url = entry.get('url')
        if original_url in self.seen_urls: return None

        # Resolve URL
        try:
            real_url = requests.head(original_url, allow_redirects=True, timeout=5).url
        except: real_url = original_url
        
        if real_url in self.seen_urls: return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')

        # 1. Scrape Real Image
        image_url = None
        try:
            response = requests.get(real_url, headers=self._get_headers(), timeout=5)
            soup = BeautifulSoup(response.text, 'html.parser')
            meta_img = soup.find('meta', property='og:image')
            if meta_img: image_url = urljoin(real_url, meta_img['content'])
        except: pass

        # 2. AI Analysis (Text)
        ai_data = self.analyze_with_ai(raw_title)
        
        # Fallback if AI fails or key missing
        if not ai_data:
            ai_data = {
                "fa": raw_title, # No translation fallback to keep it simple or use simple lib
                "tag": "سیاسی",
                "sentiment": 0
            }

        # 3. Image Fallback (AI Generation)
        if not image_url:
            # Generate image based on English title
            image_url = self.generate_ai_image(raw_title)

        return {
            "title_fa": ai_data['fa'],
            "title_en": raw_title,
            "source": publisher,
            "url": real_url,
            "image": image_url,
            "date": date,
            "tag": ai_data['tag'],
            "sentiment": ai_data['sentiment'],
            "_original_url": original_url
        }

    def run(self):
        logger.info(">>> Starting AI Radar...")
        
        # Market
        try:
            with open(CONFIG['FILES']['MARKET'], 'w', encoding='utf-8') as f:
                json.dump(self.fetch_market_rates(), f)
        except: pass

        # News
        google_news = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS'])
        try:
            results = google_news.get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.error(e)
            return

        new_entries = []
        urls_to_save = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as executor:
            future_to_entry = {executor.submit(self.process_single_news_item, entry): entry for entry in results}
            for future in concurrent.futures.as_completed(future_to_entry):
                res = future.result()
                if res:
                    urls_to_save.extend([res.pop('_original_url'), res['url']])
                    new_entries.append(res)
                    logger.info(f" + AI Processed: {res['title_en'][:20]}")

        if new_entries:
            try:
                with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f: old = json.load(f)
            except: old = []
            
            final = new_entries + old
            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f:
                json.dump(final[:50], f, ensure_ascii=False, indent=4)
            
            self._save_seen(urls_to_save)

if __name__ == "__main__":
    IranNewsRadar().run()
