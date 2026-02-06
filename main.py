import os
import json
import time
import logging
import cloudscraper # NEW: Bypasses Cloudflare
import html
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
        'MARKET': 'market.json'
    },
    'TELEGRAM': {
        'BOT_TOKEN': os.environ.get('TG_BOT_TOKEN'), 
        'CHANNEL_ID': os.environ.get('TG_CHANNEL_ID') 
    },
    'TIMEOUT': 20,
    'MAX_WORKERS': 4,
    'POLLINATIONS_KEY': os.environ.get('POLLINATIONS_API_KEY')
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        # Cloudscraper allows us to read sites that block normal bots
        self.scraper = cloudscraper.create_scraper(browser='chrome') 
        self.api_key = CONFIG['POLLINATIONS_KEY']
        self.existing_news = self._load_existing_news()
        self.seen_urls = {item.get('url') for item in self.existing_news if item.get('url')}

    def _get_headers(self):
        ua = UserAgent()
        return {
            'User-Agent': ua.random,
            'Referer': 'https://www.google.com/'
        }

    def _load_existing_news(self):
        if not os.path.exists(CONFIG['FILES']['NEWS']): return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except: return []

    # --- ENHANCED MARKET DATA ---
    def fetch_market_rates(self):
        """Fetches USD (Toman), Gold, and Oil."""
        data = {"usd": "N/A", "gold": "N/A", "oil": "N/A", "updated": "--:--"}
        
        # 1. USD & Gold (AlanChand)
        try:
            resp = self.scraper.get("https://alanchand.com/en/currencies-price/usd", timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # USD
                usd_tag = soup.find('input', attrs={'data-curr': 'tmn'})
                if usd_tag:
                    val = usd_tag.get('data-price') or usd_tag.get('value')
                    if val: data["usd"] = f"{int(int(val.replace(',', '')) / 10):,}"

                # Gold (18k) - finding by general structure or specific tag if available
                # Note: This is an example selector, might need adjustment based on site changes
                gold_tag = soup.find('input', attrs={'data-curr': 'geram18'})
                if gold_tag:
                    val = gold_tag.get('data-price')
                    if val: data["gold"] = f"{int(int(val.replace(',', '')) / 10):,}"
        except Exception as e: logger.error(f"Market Error: {e}")

        # 2. Oil (OilPrice.com API or Scraping - Simplified scraping here)
        try:
            resp = self.scraper.get("https://oilprice.com/oil-price-charts/46", timeout=10) # 46 is Brent
            soup = BeautifulSoup(resp.text, 'html.parser')
            oil_tag = soup.select_one(".last_price")
            if oil_tag:
                data["oil"] = oil_tag.get_text().strip()
        except: pass

        data["updated"] = time.strftime("%H:%M")
        return data

    # --- TELEGRAM DIGEST ---
    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id: return

        # Load Market Data for Header
        try:
            with open(CONFIG['FILES']['MARKET'], 'r') as f: mkt = json.load(f)
            market_text = f"💵 <b>USD:</b> {mkt.get('usd')} | 🛢 <b>Brent:</b> {mkt.get('oil')}"
        except: market_text = "Market Data Unavailable"

        current_time = datetime.now().strftime("%H:%M")
        header = f"📡 <b>Rasad AI Feed</b> | 🕒 {current_time}\n{market_text}\n\n"
        footer = "\n📊 <a href='https://itsyebekhe.github.io/rasadai/'>Visit Dashboard</a>"

        messages_to_send = []
        current_message = header

        for item in items:
            title_fa = str(item.get('title_fa'))
            source = str(item.get('source'))
            url = str(item.get('url'))
            impact = str(item.get('impact'))
            urgency = item.get('urgency', 0)
            
            # Urgency Icons
            icon = "🔹"
            if urgency >= 8: icon = "🚨"
            elif urgency >= 6: icon = "⚠️"

            raw_tag = item.get('tag', 'General')
            tag_str = str(raw_tag[0]) if isinstance(raw_tag, list) and raw_tag else str(raw_tag)

            safe_title = html.escape(title_fa)
            safe_source = html.escape(source)
            safe_impact = html.escape(impact)
            safe_tag = html.escape(tag_str).replace(' ', '_')
            
            summary_list = item.get('summary', [])
            if isinstance(summary_list, str): summary_list = [summary_list]
            safe_summary = "\n".join([f"• {html.escape(str(s))}" for s in summary_list])

            item_html = (
                f"{icon} <b><a href='{url}'>{safe_title} - {safe_source}</a></b>\n"
                f"<blockquote>{safe_summary}\n"
                f"🎯 {safe_impact}</blockquote>\n"
                f"#{safe_tag}\n\n"
            )

            if len(current_message) + len(item_html) + len(footer) > 3900:
                messages_to_send.append(current_message + footer)
                current_message = header + item_html
            else:
                current_message += item_html

        if current_message != header:
            messages_to_send.append(current_message + footer)

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        for msg in messages_to_send:
            try:
                requests.post(api_url, json={
                    "chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True
                }, timeout=10)
                time.sleep(1)
            except: pass

    # --- ROBUST SCRAPER ---
    def scrape_article(self, url, fallback_snippet):
        """Scrapes with Cloudscraper. Returns Snippet if scrape fails."""
        try:
            # Check for files (PDFs)
            if url.lower().endswith('.pdf'):
                return url, fallback_snippet

            resp = self.scraper.get(url, timeout=15)
            final_url = resp.url
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "figure", "img", "iframe"]): 
                tag.extract()
            
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text()) > 60]
            clean_text = " ".join(paragraphs)[:4500]
            
            # If scraping got nothing (paywall?), return the fallback snippet
            if len(clean_text) < 100:
                return final_url, fallback_snippet
            
            return final_url, clean_text
        except Exception as e:
            # logger.warning(f"Scrape failed for {url}: {e}")
            return url, fallback_snippet

    # --- IMPROVED AI ANALYST ---
    def analyze_with_ai(self, headline, full_text):
        if not self.api_key: return None
        context_text = full_text if len(full_text) > 100 else headline
        current_date_str = datetime.now().strftime("%Y-%m-%d")

        url = "https://gen.pollinations.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        # Enhanced Prompt with Urgency
        system_prompt = (
            f"Date: {current_date_str}.\n"
            "CONTEXT: Iran/US/Israel relations. Trump is US President.\n"
            "Output valid JSON only:\n"
            "1. 'title_fa': Professional Persian headline.\n"
            "2. 'summary': [3 Persian bullet points].\n"
            "3. 'impact': Strategic impact (Persian, 1 sentence).\n"
            "4. 'sentiment': Float -1.0 to 1.0.\n"
            "5. 'tag': [Economy, Military, Nuclear, Politics, Energy].\n"
            "6. 'urgency': Int 1-10 (10=War/Crash, 1=Opinion).\n"
        )

        try:
            resp = requests.post(url, headers=headers, json={
                "model": "openai",
                "messages": [{"role": "system", "content": system_prompt}, 
                             {"role": "user", "content": f"HEADLINE: {headline}\nTEXT: {context_text}"}],
                "temperature": 0.1
            }, timeout=35)
            if resp.status_code == 200:
                raw = resp.json()['choices'][0]['message']['content']
                return json.loads(raw.replace("```json", "").replace("```", "").strip())
        except: pass
        return None

    def process_item(self, entry):
        orig_url = entry.get('url')
        if orig_url in self.seen_urls: return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        snippet = entry.get('description', raw_title) # Get fallback snippet
        
        # Pass snippet to scrape function
        real_url, full_text = self.scrape_article(orig_url, snippet)
        
        if real_url in self.seen_urls: return None

        ai = self.analyze_with_ai(raw_title, full_text)
        if not ai: 
            # Basic fallback if AI fails
            ai = {"title_fa": raw_title, "summary": [snippet], "impact": "تحلیل خودکار ناموفق", "tag": "News", "urgency": 3}

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa'),
            "title_en": raw_title,
            "summary": ai.get('summary'),
            "impact": ai.get('impact'),
            "tag": ai.get('tag'),
            "sentiment": ai.get('sentiment', 0),
            "urgency": ai.get('urgency', 5),
            "source": publisher,
            "url": real_url,
            "date": entry.get('published date'),
            "timestamp": ts
        }

    def run(self):
        logger.info(">>> Radar Started (Enhanced Mode)...")
        
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        try:
            results = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS']).get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.error(f"GNews failed: {e}")
            return

        new_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            futures = {exc.submit(self.process_item, i): i for i in results}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    new_items.append(res)
                    logger.info(f" + Analyzed: {res['title_en'][:25]}")

        if new_items:
            # Sort by urgency (Highest first) then timestamp
            new_items.sort(key=lambda x: (x.get('urgency', 0), x.get('timestamp', 0)), reverse=True)
            
            self.send_digest_to_telegram(new_items)

            updated_list = new_items + self.existing_news
            updated_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                json.dump(updated_list[:100], f, indent=4, ensure_ascii=False)
            
            logger.info(">>> Completed.")
        else:
            logger.info(">>> No new news.")

if __name__ == "__main__":
    IranNewsRadar().run()
