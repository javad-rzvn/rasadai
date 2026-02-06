import os
import json
import time
import logging
import requests
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
        self.ua = UserAgent()
        self.api_key = CONFIG['POLLINATIONS_KEY']
        self.existing_news = self._load_existing_news()
        self.seen_urls = {item.get('url') for item in self.existing_news if item.get('url')}

    def _get_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Referer': 'https://www.google.com/',
        }

    def _load_existing_news(self):
        if not os.path.exists(CONFIG['FILES']['NEWS']): return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except: return []

    # --- NEW TELEGRAM DIGEST SENDER ---
    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']

        if not token or not chat_id:
            logger.warning("Telegram credentials missing.")
            return

        # Header for the message
        current_time = datetime.now().strftime("%H:%M")
        header = f"📡 <b>Rasad AI Feed</b> | 🕒 {current_time}\n\n"
        footer = "\n📊 <a href='https://itsyebekhe.github.io/rasadai/'>Visit Rasad AI Dashboard</a>"

        # We need to build the message. Telegram limit is 4096 chars.
        # We will build chunks to stay safe.
        messages_to_send = []
        current_message = header

        for i, item in enumerate(items):
            # 1. Prepare Data
            title_fa = str(item.get('title_fa', 'News Update'))
            source = str(item.get('source', 'Unknown'))
            url = str(item.get('url', ''))
            impact = str(item.get('impact', ''))
            
            # Fix Tag
            raw_tag = item.get('tag')
            tag_str = str(raw_tag[0]) if isinstance(raw_tag, list) and raw_tag else str(raw_tag) if raw_tag else 'General'

            # 2. Escape HTML
            safe_title = html.escape(title_fa)
            safe_source = html.escape(source)
            safe_impact = html.escape(impact)
            safe_tag = html.escape(tag_str).replace(' ', '_')
            
            # Format Summary
            summary_list = item.get('summary', [])
            if isinstance(summary_list, str): summary_list = [summary_list]
            safe_summary = "\n".join([f"• {html.escape(str(s))}" for s in summary_list])

            # 3. Construct Item Block
            # Format: Link(Title - Source) \n Blockquote \n Hashtag
            item_html = (
                f"🔹 <b><a href='{url}'>{safe_title} - {safe_source}</a></b>\n"
                f"<blockquote>{safe_summary}\n"
                f"🎯 {safe_impact}</blockquote>\n"
                f"#{safe_tag}\n\n"
            )

            # 4. Check Length (Safety buffer of 200 chars for footer)
            if len(current_message) + len(item_html) + len(footer) > 3900:
                # Close current message and start new one
                messages_to_send.append(current_message + footer)
                current_message = header + item_html
            else:
                current_message += item_html

        # Append the final message
        if current_message != header:
            messages_to_send.append(current_message + footer)

        # 5. Send All Chunks
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        for msg in messages_to_send:
            payload = {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True 
            }
            try:
                requests.post(api_url, json=payload, timeout=10)
                time.sleep(1) # Short pause between chunks
            except Exception as e:
                logger.error(f"Telegram Send Error: {e}")
        
        logger.info(f" -> Sent {len(items)} items in {len(messages_to_send)} message(s).")

    # --- MARKET ---
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

    # --- SCRAPER ---
    def scrape_article(self, url):
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            final_url = resp.url
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "figure", "img"]): tag.extract()
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text()) > 60]
            return final_url, " ".join(paragraphs)[:4000]
        except: return url, ""

    # --- AI ANALYST ---
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
                clean_raw = raw.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_raw)
        except Exception as e: logger.error(f"AI Error: {e}")
        return None

    # --- MAIN PROCESS ---
    def process_item(self, entry):
        orig_url = entry.get('url')
        if orig_url in self.seen_urls: return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher_name = entry.get('publisher', {}).get('title', 'Source')
        
        real_url, full_text = self.scrape_article(orig_url)
        if real_url in self.seen_urls: return None

        ai = self.analyze_with_ai(raw_title, full_text)
        if not ai: 
            ai = {"title_fa": raw_title, "summary": ["تحلیل در دسترس نیست"], "impact": "بررسی نشده", "tag": "عمومی", "sentiment": 0}

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa'),
            "title_en": raw_title,
            "summary": ai.get('summary'),
            "impact": ai.get('impact'),
            "tag": ai.get('tag'),
            "sentiment": ai.get('sentiment'),
            "source": publisher_name,
            "url": real_url,
            "date": entry.get('published date'),
            "timestamp": ts
        }

    def run(self):
        logger.info(">>> Radar Started...")
        
        # 1. Market
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        # 2. News
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
                    logger.info(f" + Found: {res['title_en'][:20]}")

        # 3. Send & Save
        if new_items:
            # Sort by timestamp ascending for the Telegram Digest
            new_items.sort(key=lambda x: x.get('timestamp', 0))

            # Send One Digest Message
            self.send_digest_to_telegram(new_items)

            # Update Database (Sorted Newest First for JSON)
            updated_list = new_items + self.existing_news
            updated_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                json.dump(updated_list[:100], f, indent=4, ensure_ascii=False)
            
            logger.info(">>> Database updated.")
        else:
            logger.info(">>> No new news.")

if __name__ == "__main__":
    IranNewsRadar().run()
