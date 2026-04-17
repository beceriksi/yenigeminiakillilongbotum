import requests
import pandas as pd
import numpy as np
import os
import time

# GitHub Secrets (GitHub Ayarlarından tanımlanmış olmalı)
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# --- ANA STRATEJİ AYARLARI ---
VOL_SPIKE_THRESHOLD = 1.3  # %30 hacim artışı (1.3x) yeterli
MIN_BUY_RATIO = 1.2        # Alıcılar satıcılardan %20 daha baskın olmalı
MAX_24H_CHANGE = 18        # %18'den fazla yükselmişse trene binme (Fırsat kaçmıştır)
PIVOT_LOOKBACK = 2         # Direnç tespiti hassasiyeti (Daha hızlı direnç bulur)

def send_telegram(msg):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            res = requests.post(url, json={
                "chat_id": CHAT_ID, 
                "text": msg, 
                "parse_mode": "Markdown", 
                "disable_web_page_preview": True
            })
            return res.status_code == 200
        except Exception as e:
            print(f"Telegram Hatası: {e}")
    return False

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params, timeout=15).json()
        return res.get('data', [])
    except Exception as e:
        print(f"Veri Çekme Hatası ({endpoint}): {e}")
        return []

def find_pivots(df, pivot_len=2):
    highs = df['h'].astype(float).values
    p_highs = []
    # Pivot tespiti: Sağında ve solunda pivot_len kadar düşük mum olan tepe noktası
    for i in range(pivot_len, len(highs) - pivot_len):
        if all(highs[i] > highs[i-j] for j in range(1, pivot_len + 1)) and \
           all(highs[i] > highs[i+j] for j in range(1, pivot_len + 1)):
            p_highs.append(highs[i])
    return p_highs

def scan_unusual_movements():
    print(">>> Tarama Başlatıldı...")
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    
    if not tickers:
        print("Borsadan veri alınamadı!")
        return

    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        
        try:
            last_p = float(t['last'])
            open_24h = float(t['open24h'])
            change_24h = (last_p / open_24h - 1) * 100
            
            # 1. FİLTRE: Hareketin başında olan coinleri seç (%18 altı)
            if -3 < change_24h < MAX_24H_CHANGE:
                
                # 2. SAATLİK ANALİZ: Direnç Bulma
                candles_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "50"})
                if not candles_1h: continue
                
                df_1h = pd.DataFrame(candles_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_1h[['h','c']] = df_1h[['h','c']].astype(float)
                
                p_highs = find_pivots(df_1h, pivot_len=PIVOT_LOOKBACK)
                if not p_highs: continue
                last_res = p_highs[-1]
                
                # 3. ŞART: Fiyat direncin üzerinde mi?
                if last_p > last_res:
                    
                    # 4. 15 DAKİKALIK ANALİZ: Hacim Patlaması (Anlık hareket tespiti)
                    candles_15m = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "15m", "limit": "20"})
                    if len(candles_15m) < 15: continue
                    
                    df_15m = pd.DataFrame(candles_15m, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                    df_15m['vq'] = df_15m['vq'].astype(float)
                    
                    # Henüz kapanmış olan (tamamlanmış) son mumun hacmi
                    prev_full_vol = df_15m['vq'].iloc[-2] 
                    # Ondan önceki 10 mumun ortalama hacmi
                    avg_vol_15m = df_15m['vq'].iloc[-12:-2].mean() 
                    
                    if avg_vol_15m > 0 and prev_full_vol > (avg_vol_15m * VOL_SPIKE_THRESHOLD):
                        
                        # 5. ONAY: Alım Baskısı (Taker Ratio)
                        ratio_res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
                        if ratio_res:
                            buy_v = float(ratio_res[0][1])
                            sell_v = float(ratio_res[0][2])
                            ratio = buy_v / sell_v if sell_v > 0 else 1.0
                            
                            if ratio >= MIN_BUY_RATIO:
                                tv_link = f"https://www.tradingview.com/chart/?symbol=OKX:{symbol.replace('-USDT-SWAP', 'USDTPERP')}"
                                
                                msg = (f"🎯 *STRATEJİ ONAYLANDI: PUMP BAŞLANGICI*\n\n"
                                       f"💎 *COIN:* `{symbol}`\n"
                                       f"📊 24s Değişim: `%{round(change_24h, 1)}` \n"
                                       f"━━━━━━━━━━━━━━━\n"
                                       f"✅ *DİRENÇ KIRILDI:* `{last_res}`\n"
                                       f"🔥 *HACİM ARTIŞI (15dk):* `{round(prev_full_vol/avg_vol_15m, 1)}x` \n"
                                       f"⚖️ *ALIM BASKISI:* `{round(ratio, 2)}` \n"
                                       f"━━━━━━━━━━━━━━━\n"
                                       f"🔗 [Grafiği İncele]({tv_link})")
                                
                                if send_telegram(msg):
                                    print(f"Sinyal gönderildi: {symbol}")
                                    time.sleep(1) # Rate limit engeli için
        except Exception as e:
            print(f"Hata ({symbol}): {e}")
            continue

    print(">>> Tarama Tamamlandı.")

if __name__ == "__main__":
    scan_unusual_movements()
