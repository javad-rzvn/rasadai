import os
import json
import time
import logging
import requests
import urllib.parse
import concurrent.futures
from datetime import datetime
from dateutil import parser
from bs4 import BeautifulSoup
from gnews import GNews
from fake_useragent import UserAgent

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency OR IRGC)',
    'LANGUAGE': 'en',
    'COUNTRY': 'US',
    'PERIOD': '4h',
    'MAX_RESULTS': 30,
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json',
        'HISTORY': 'seen_news.txt'
    },
    'TIMEOUT': 20,
    'MAX_WORKERS': 4,
    'POLLINATIONS_KEY': os.environ.get('POLLINATIONS_API_KEY')
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        self.ua = UserAgent()
        self.seen_urls = self._load_seen()
        self.api_key = CONFIG['POLLINATIONS_KEY']

    def _get_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Referer': 'https://www.google.com/',
        }

    def _load_seen(self):
        if not os.path.exists(CONFIG['FILES']['HISTORY']): return set()
        with open(CONFIG['FILES']['HISTORY'], 'r', encoding='utf-8') as f:
            return set(f.read().splitlines())

    def _save_seen(self, new_urls):
        with open(CONFIG['FILES']['HISTORY'], 'a', encoding='utf-8') as f:
            for url in new_urls: f.write(url + '\n')

    # --- 1. MARKET DATA ---
    def fetch_market_rates(self):
        url = "https://alanchand.com/en/currencies-price/usd"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
                if input_tag:
                    val = input_tag.get('data-price') or input_tag.get('value')
                    if val:
                        price = int(int(val.replace(',', '')) / 10)
                        return {"usd": f"{price:,}", "updated": time.strftime("%H:%M")}
        except: pass
        return {"usd": "N/A", "updated": "--:--"}

    # --- 2. SCRAPER (TEXT ONLY - NO IMAGES) ---
    def scrape_article(self, url):
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            final_url = resp.url
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Remove junk to get clean text
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "figure", "img"]): 
                tag.extract()
            
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text()) > 60]
            clean_text = " ".join(paragraphs)[:4000]
            
            return final_url, clean_text
        except:
            return url, ""

    # --- 3. AI ANALYST ---
    def analyze_with_ai(self, headline, full_text):
        if not self.api_key: return None
        context_text = full_text if len(full_text) > 100 else headline
        current_date_str = datetime.now().strftime("%Y-%m-%d")

        url = "https://gen.pollinations.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        system_prompt = (
            f"Current Date: {current_date_str}.\n"
            "CONTEXT: Donald Trump is the CURRENT President of the USA. "
            "Role: Intelligence Analyst. "
            "Output strictly valid JSON:\n"
            "1. 'title_fa': Professional Persian headline.\n"
            "2. 'summary': Array of 3 short Persian bullet points.\n"
            "3. 'impact': One sentence on strategic impact on Iran (Persian).\n"
            "4. 'sentiment': Float -1.0 to 1.0.\n"
            "5. 'tag': [نظامی, هسته‌ای, اقتصادی, سیاسی, اجتماعی].\n"
        )

        try:
            resp = requests.post(url, headers=headers, json={
                "model": "openai",
                "messages": [{"role": "system", "content": system_prompt}, 
                             {"role": "user", "content": f"HEADLINE: {headline}\nTEXT: {context_text}"}],
                "temperature": 0.1
            }, timeout=30)
            if resp.status_code == 200:
                raw = resp.json()['choices'][0]['message']['content']
                return json.loads(raw.replace("```json", "").replace("```", "").strip())
        except Exception as e:
            logger.error(f"AI Error: {e}")
        return None

    # --- PROCESSOR ---
    def process_item(self, entry):
        orig_url = entry.get('url')
        if orig_url in self.seen_urls: return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher_name = entry.get('publisher', {}).get('title', 'Source')
        
        # A. Scrape (Text Only)
        real_url, full_text = self.scrape_article(orig_url)
        if real_url in self.seen_urls: return None

        # B. Analyze
        ai = self.analyze_with_ai(raw_title, full_text)
        if not ai: 
            ai = {"title_fa": raw_title, "summary": ["تحلیل در دسترس نیست"], "impact": "بررسی نشده", "tag": "عمومی", "sentiment": 0}

        # C. Time
        try:
            ts = parser.parse(entry.get('published date')).timestamp()
        except:
            ts = time.time()

        return {
            "title_fa": ai.get('title_fa'),
            "title_en": raw_title,
            "summary": ai.get('summary'),
            "impact": ai.get('impact'),
            "tag": ai.get('tag'),
            "sentiment": ai.get('sentiment'),
            "source": publisher_name,
            "url": real_url,
            "image": None, # Force No Image
            "date": entry.get('published date'),
            "timestamp": ts,
            "_orig_url": orig_url
        }

    def run(self):
        logger.info(">>> Radar Started (No Images Mode)...")
        
        # 1. Market
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        # 2. News
        try:
            results = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS']).get_news(CONFIG['SEARCH_QUERY'])
        except: return

        new_items = []
        seen_updates = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            futures = {exc.submit(self.process_item, i): i for i in results}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    seen_updates.extend([res.pop('_orig_url'), res['url']])
                    new_items.append(res)
                    logger.info(f" + Processed: {res['title_en'][:20]}")

        # 3. Clean & Save
        if new_items:
            try:
                with open(CONFIG['FILES']['NEWS'], 'r') as f: old = json.load(f)
            except: old = []

            # Clean old data (Simplified - no image checking needed)
            clean_old = []
            for item in old:
                # Only check if summary exists (AI success)
                if 'summary' in item:
                    clean_old.append(item)

            combined = new_items + clean_old
            combined.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            with open(CONFIG['FILES']['NEWS'], 'w') as f: json.dump(combined[:50], f, indent=4)
            self._save_seen(seen_updates)
            logger.info(">>> Database Updated.")

if __name__ == "__main__":
    IranNewsRadar().run()
