import requests
import pandas as pd
import numpy as np
import os

# GitHub Secrets
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# SERT FİLTRE AYARLARI (Sadece absürt hareketlerde çalışır)
VOL_SPIKE_THRESHOLD = 2.0  # Hacim ortalamanın 3 katı olmalı (Anormallik belirtisi)
MIN_BUY_RATIO = 2.0        # Alıcılar satıcıların en az 2 katı olmalı
MAX_24H_CHANGE = 10        # %10'dan fazla yükselmişse zaten hareket bitmiştir, bakma

def send_telegram(msg):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True})
        except: pass

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params, timeout=10).json()
        return res.get('data', [])
    except: return []

def find_pivots(df, pivot_len=2):
    highs = df['h'].astype(float).values
    p_highs = []
    for i in range(pivot_len, len(highs) - pivot_len):
        if all(highs[i] > highs[i-j] for j in range(1, pivot_len + 1)) and \
           all(highs[i] > highs[i+j] for j in range(1, pivot_len + 1)):
            p_highs.append(highs[i])
    return p_highs

def scan_unusual_movements():
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        
        last_p = float(t['last'])
        change_24h = (last_p / float(t['open24h']) - 1) * 100
        
        # Filtre 1: Hareket henüz çok eskimemiş olmalı
        if -2 < change_24h < MAX_24H_CHANGE:
            candles = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "50"})
            if not candles: continue
            
            df = pd.DataFrame(candles, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
            df[['h','c','vq']] = df[['h','c','vq']].astype(float)
            
            # Filtre 2: Absürt Hacim Girişi (Volume Anomaly)
            avg_vol = df['vq'].iloc[-21:-1].mean() # Son mum hariç önceki 20 mum ortalaması
            curr_vol = df['vq'].iloc[-1]
            
            if curr_vol > (avg_vol * VOL_SPIKE_THRESHOLD):
                p_highs = find_pivots(df)
                if not p_highs: continue
                last_res = p_highs[-1]
                
                # Filtre 3: Sert Direnç Kırılımı (1H)
                if df['c'].iloc[-2] <= last_res and df['c'].iloc[-1] > last_res:
                    
                    # Filtre 4: Alıcı Baskısı (Taker Volume Ratio)
                    ratio_res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
                    if ratio_res:
                        buy_v = float(ratio_res[0][1])
                        sell_v = float(ratio_res[0][2])
                        ratio = buy_v / sell_v if sell_v > 0 else 1.0
                        
                        if ratio >= MIN_BUY_RATIO:
                            tv_link = f"https://www.tradingview.com/chart/?symbol=OKX:{symbol.replace('-USDT-SWAP', 'USDTPERP')}"
                            
                            msg = (f"🚨 *ANORMAL HACİM & KIRILIM UYARISI*\n\n"
                                   f"💎 *COIN:* `{symbol}`\n"
                                   f"📊 24s Değişim: `%{round(change_24h, 1)}` \n"
                                   f"⚠️ *DURUM:* Sıra dışı para girişi tespit edildi!\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"📈 *HACİM PATLAMASI:* Ortalamanın `{round(curr_vol/avg_vol, 1)}x` katı!\n"
                                   f"✅ *DİRENÇ GEÇİLDİ (1H):* `{last_res}`\n"
                                   f"⚖️ *ALIM BASKISI (Oran):* `{round(ratio, 2)}` \n\n"
                                   f"🛒 *DETAY:* Alış: `${round(buy_v/1000, 1)}K` / Satış: `${round(sell_v/1000, 1)}K` \n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"🔗 [Grafiği İncele]({tv_link})")
                            
                            send_telegram(msg)

if __name__ == "__main__":
    scan_unusual_movements()
