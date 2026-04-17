import requests
import pandas as pd
import numpy as np
import os

# GitHub Secrets
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# ANA MANTIK AYARLARI (GÖSTERGELERE SADIK)
VOL_SPIKE_THRESHOLD = 1.4  # 15dk'lık hacim ortalamanın 1.4 katı olmalı
MIN_BUY_RATIO = 1.25       # Alıcılar biraz daha baskın olmalı
MAX_24H_CHANGE = 15        # %15'ten fazla yükselmişse trene binme (Ana kural)
PIVOT_LOOKBACK = 2         # Direnç tespiti hassasiyeti

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
        
        # ANA FİLTRE: Hareketin başında yakalama (Direnç Kırılımı Arayışı)
        if -2 < change_24h < MAX_24H_CHANGE:
            
            # 1. SAATLİK GRAFİK: Direnç (Pivot) Bulma
            candles_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "50"})
            if not candles_1h: continue
            df_1h = pd.DataFrame(candles_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
            df_1h[['h','c']] = df_1h[['h','c']].astype(float)
            
            p_highs = find_pivots(df_1h, pivot_len=PIVOT_LOOKBACK)
            if not p_highs: continue
            last_res = p_highs[-1]
            
            # ŞART 1: Fiyat şu an saatlik direncin üzerinde mi?
            is_above = df_1h['c'].iloc[-1] > last_res
            
            if is_above:
                # 2. 15 DAKİKALIK GRAFİK: Hacim Patlaması Onayı
                candles_15m = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "15m", "limit": "20"})
                if not candles_15m: continue
                df_15m = pd.DataFrame(candles_15m, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_15m['vq'] = df_15m['vq'].astype(float)
                
                avg_vol_15m = df_15m['vq'].iloc[-11:-1].mean()
                curr_vol_15m = df_15m['vq'].iloc[-1]
                
                # ŞART 2: Hacim anomalisi var mı?
                if curr_vol_15m > (avg_vol_15m * VOL_SPIKE_THRESHOLD):
                    
                    # 3. ONAY: Taker Volume (Alım Baskısı)
                    ratio_res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
                    if ratio_res:
                        buy_v = float(ratio_res[0][1])
                        sell_v = float(ratio_res[0][2])
                        ratio = buy_v / sell_v if sell_v > 0 else 1.0
                        
                        if ratio >= MIN_BUY_RATIO:
                            tv_link = f"https://www.tradingview.com/chart/?symbol=OKX:{symbol.replace('-USDT-SWAP', 'USDTPERP')}"
                            msg = (f"🎯 *STRATEJİ ONAYLANDI: KIRILIM & HACİM*\n\n"
                                   f"💎 *COIN:* `{symbol}`\n"
                                   f"📊 24s Değişim: `%{round(change_24h, 1)}` \n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"✅ *DİRENÇ GEÇİLDİ (1H):* `{last_res}`\n"
                                   f"🔥 *15DK HACİM ARTIŞI:* `{round(curr_vol_15m/avg_vol_15m, 1)}x` \n"
                                   f"⚖️ *ALIM BASKISI:* `{round(ratio, 2)}` \n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"🔗 [Grafiği İncele]({tv_link})")
                            send_telegram(msg)

if __name__ == "__main__":
    scan_unusual_movements()
