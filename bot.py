import requests
import pandas as pd
import numpy as np
import os

# GitHub Secrets
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# STRATEJİ AYARLARI
VOL_SPIKE_THRESHOLD = 1.6  # 15dk'lık hacim son 10 mumun 1.6 katı olmalı
MAX_24H_CHANGE = 20        # %20'den fazla yükselmişse trene binme
PIVOT_LOOKBACK = 3         # Daha güçlü dirençler için pivot hassasiyeti

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

def find_pivots(df, pivot_len=3):
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
        
        # Filtre 1: Aşırı şişmiş coinleri ele, hareketin başındakileri tut
        if -3 < change_24h < MAX_24H_CHANGE:
            
            # 1. BÜYÜK RESİM: 1 Saatlik Direnç Analizi
            candles_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "60"})
            if not candles_1h: continue
            
            df_1h = pd.DataFrame(candles_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
            df_1h[['h','c']] = df_1h[['h','c']].astype(float)
            
            p_highs = find_pivots(df_1h, pivot_len=PIVOT_LOOKBACK)
            if not p_highs: continue
            last_res = p_highs[-1]
            
            # ŞART A: Fiyat SAATLİKTE direncin üzerinde mi? (Son 2 kapanış direnç üstü olmalı)
            is_above_res = df_1h['c'].iloc[-1] > last_res and df_1h['c'].iloc[-2] > last_res
            
            if is_above_res:
                # 2. HIZLI TAKİP: 15 Dakikalık Hacim Analizi
                candles_15m = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "15m", "limit": "20"})
                if not candles_15m: continue
                
                df_15m = pd.DataFrame(candles_15m, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_15m[['c','vq']] = df_15m[['c','vq']].astype(float)
                
                # ŞART B: 15 Dakikalıkta hacim patlaması var mı?
                avg_vol_15m = df_15m['vq'].iloc[-11:-1].mean()
                curr_vol_15m = df_15m['vq'].iloc[-1]
                
                if curr_vol_15m > (avg_vol_15m * VOL_SPIKE_THRESHOLD):
                    
                    # 3. ONAY: Alım Baskısı (Taker Ratio)
                    ratio_res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
                    buy_ratio = 1.0
                    if ratio_res:
                        buy_v = float(ratio_res[0][1])
                        sell_v = float(ratio_res[0][2])
                        buy_ratio = buy_v / sell_v if sell_v > 0 else 1.0

                    if buy_ratio >= 1.3: # Alım baskısı makul seviyede
                        tv_link = f"https://www.tradingview.com/chart/?symbol=OKX:{symbol.replace('-USDT-SWAP', 'USDTPERP')}"
                        
                        msg = (f"🚀 *PUMP ÖNCESİ KALICILIK & HACİM TESPİTİ*\n\n"
                               f"💎 *COIN:* `{symbol}`\n"
                               f"📈 24s Değişim: `%{round(change_24h, 1)}` \n"
                               f"━━━━━━━━━━━━━━━\n"
                               f"📍 *1H DİRENÇ ÜSTÜ:* `{last_res}` (Kalıcı)\n"
                               f"🔥 *15DK HACİM:* `{round(curr_vol_15m/avg_vol_15m, 1)}x` Artış!\n"
                               f"⚖️ *ALIM BASKISI:* `{round(buy_ratio, 2)}` \n"
                               f"━━━━━━━━━━━━━━━\n"
                               f"🔗 [Grafiği Aç]({tv_link})")
                        
                        send_telegram(msg)

if __name__ == "__main__":
    scan_unusual_movements()
