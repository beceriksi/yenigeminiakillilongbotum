import requests
import pandas as pd
import numpy as np
import os
import time
import json

TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# STRATEJİ AYARLARI (Long Odaklı)
HISTORY_FILE = "pump_history.json"
MIN_IMBALANCE = 2.5  # Alıcılar satıcılardan en az 2.5 kat güçlü olmalı
VOL_SPIKE_LIMIT = 1.8 # Hacim normalin 1.8 katı olmalı

def send_telegram(msg):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params).json()
        return res.get('data', [])
    except: return []

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_market_metrics(symbol):
    # Derinlik analizi (Alıcılar vs Satıcılar)
    depth = get_data("/api/v5/market/books", {"instId": symbol, "sz": "50"})
    bid_sum = sum([float(b[1]) for b in depth[0]['bids']]) if depth else 1
    ask_sum = sum([float(a[1]) for a in depth[0]['asks']]) if depth else 1
    imbalance = bid_sum / ask_sum if ask_sum > 0 else 1
    
    # Fonlama Oranı
    funding = get_data("/api/v5/public/funding-rate", {"instId": symbol})
    f_rate = float(funding[0]['fundingRate']) * 100 if funding else 0
    
    return imbalance, f_rate

# --- SADECE LONG ANALİZ MOTORU ---
def analyze_long(symbol, rsi, f_rate, imbalance, change):
    score = 5.0
    warnings = []

    # 1. Balina Birikim Kontrolü (Order Book)
    if imbalance > 4.0:
        score += 3.0
        warnings.append("🐋 DEV ALICI: Alıcılar satıcılara göre 4 kat daha baskın!")
    elif imbalance > 2.5:
        score += 1.5
        warnings.append("🟢 ALICI ÜSTÜNLÜĞÜ: Tahtada alım iştahı yüksek.")

    # 2. RSI ve Aşırı Satım
    if rsi < 30:
        score += 2.0
        warnings.append("📉 DİPTE: RSI aşırı satım bölgesinde, tepki yakın.")
    elif rsi > 75:
        score -= 2.0 # Çok yükselmiş olanı elemek için
        warnings.append("⚠️ ŞİŞMİŞ: Fiyat çok yükselmiş, girmek riskli olabilir.")

    # 3. Funding (Short Squeeze Potansiyeli)
    if f_rate < -0.1:
        score += 2.5
        warnings.append("🚀 SQUEEZE: Funding aşırı negatif! Shortları patlatıp yukarı sürebilirler.")
    elif f_rate > 0.05:
        score -= 1.5
        warnings.append("❌ PAHALI: Long maliyeti yüksek, balinalar satabilir.")

    # 4. Fiyat Durumu (Pump Başlangıcı mı?)
    if 0 < change < 5:
        score += 1.0
        warnings.append("✅ ERKEN EVRE: Henüz sert bir yükseliş yapmamış, yolun başında.")

    return round(score, 1), warnings

# --- HAFIZA VE TAKİP ---
def monitor_signals(symbol, score, imbalance, f_rate):
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f: json.dump({}, f)
    with open(HISTORY_FILE, "r") as f: history = json.load(f)
    
    update_msg = ""
    if symbol in history:
        prev = history[symbol]
        # Alıcılar kaçıyor mu?
        if imbalance < prev['imbalance'] * 0.5:
            update_msg = f"⚠️ *LONG UYARISI: {symbol}*\nAlıcı duvarı %50 çöktü! Balinalar emirlerini siliyor."
        # Shortçular pes mi etti? (Squeeze bitti mi?)
        elif f_rate > prev['f_rate'] + 0.05:
            update_msg = f"💡 *DURUM GÜNCELLEME: {symbol}*\nFonlama normale dönüyor, yükseliş hızı yavaşlayabilir."

    history[symbol] = {"score": score, "imbalance": imbalance, "f_rate": f_rate, "ts": time.time()}
    with open(HISTORY_FILE, "w") as f: json.dump(history, f)
    return update_msg

def scan():
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    tickers = sorted(tickers, key=lambda x: float(x['vol24h']), reverse=True)[:60]
    
    final_signals = []

    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        
        change = (float(t['last']) / float(t['open24h']) - 1) * 100
        imbalance, f_rate = get_market_metrics(symbol)
        
        # RSI Hesapla
        candles = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "50"})
        if not candles: continue
        df = pd.DataFrame(candles, columns=['ts','o','h','l','c','v','vc','vq','conf'])
        rsi = calculate_rsi(df['c'].astype(float)[::-1]).iloc[-1]

        # Sadece LONG odaklı analiz
        score, warnings = analyze_long(symbol, rsi, f_rate, imbalance, change)
        update_text = monitor_signals(symbol, score, imbalance, f_rate)

        if update_text:
            send_telegram(update_text)

        # 7.5 Üstü Gerçekten Sağlam Long Fırsatıdır
        if score >= 7.5:
            warn_str = "\n".join([f"- {w}" for w in warnings])
            msg = (f"🟢 *LONG FIRSATI: {symbol}*\n"
                   f"⭐ Güven Puanı: {score}/10\n\n"
                   f"📊 RSI: {round(rsi, 1)} | 💸 Funding: %{round(f_rate, 4)}\n"
                   f"🧱 Alıcı Gücü: {round(imbalance, 1)}x\n"
                   f"📈 24s Değişim: %{round(change, 2)}\n\n"
                   f"📝 *ANALİZ:*\n{warn_str}")
            final_signals.append((score, msg))

    # Puan sırasına göre gönder
    final_signals.sort(key=lambda x: x[0], reverse=True)
    for s in final_signals:
        send_telegram(s[1])

if __name__ == "__main__":
    scan()
