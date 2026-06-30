"""
Screener S&P500 - RSI Sobreventa con alertas Telegram
Revisa periódicamente las empresas del S&P500 y avisa cuando detecta RSI < 30
"""

import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, time as dtime
import pytz
from flask import Flask
import numpy as np

# Mini servidor web para que Render Free lo mantenga activo
app = Flask(__name__)

@app.route("/")
def home():
    return "Screener S&P500 activo"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ============ CONFIGURACIÓN ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8637021967:AAHCgbIktmaebDoQQi5RI85nxw-U3OBAEXE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "908626909")

RSI_THRESHOLD = 30          # Nivel de sobreventa
CHECK_INTERVAL_MINUTES = 15  # Cada cuántos minutos revisa (15 min recomendado para plan gratuito)
RSI_PERIOD = 14

# Para evitar spamear la misma alerta repetidamente
ya_avisados_hoy = set()

# ============ LISTA S&P500 ============
def get_sp500_tickers():
    """Obtiene la lista actualizada de tickers del S&P500 desde Wikipedia"""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # Yahoo Finance usa "-" en vez de "." para algunos tickers (ej BRK.B -> BRK-B)
        tickers = [t.replace(".", "-") for t in tickers]
        return tickers
    except Exception as e:
        print(f"Error obteniendo lista S&P500: {e}")
        # Fallback con una lista reducida si falla
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "BAC", "XOM"]


def is_market_open():
    """Comprueba si el mercado USA está abierto (9:30-16:00 ET, L-V)"""
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5:  # sábado=5, domingo=6
        return False
    market_open = dtime(9, 30)
    market_close = dtime(16, 0)
    return market_open <= now.time() <= market_close


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"Error enviando Telegram: {r.text}")
    except Exception as e:
        print(f"Error de conexión Telegram: {e}")


def compute_rsi_manual(close_prices, period=14):
    """Calcula RSI manualmente sin depender de pandas-ta"""
    delta = close_prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100)  # si avg_loss es 0, RSI = 100
    return rsi


def calculate_rsi(ticker):
    """Descarga datos y calcula RSI para un ticker"""
    try:
        data = yf.download(ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if data.empty or len(data) < RSI_PERIOD + 1:
            return None
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        rsi_series = compute_rsi_manual(close, period=RSI_PERIOD)
        if rsi_series is None or rsi_series.empty:
            return None
        last_rsi = rsi_series.iloc[-1]
        last_price = close.iloc[-1]
        if hasattr(last_rsi, 'item'):
            last_rsi = last_rsi.item()
        if hasattr(last_price, 'item'):
            last_price = last_price.item()
        return {"rsi": round(float(last_rsi), 2), "price": round(float(last_price), 2)}
    except Exception as e:
        return None


def scan_market():
    """Escanea todo el S&P500 buscando sobreventa"""
    tickers = get_sp500_tickers()
    print(f"[{datetime.now()}] Escaneando {len(tickers)} empresas...")

    alerts = []
    for i, ticker in enumerate(tickers):
        result = calculate_rsi(ticker)
        if result and result["rsi"] < RSI_THRESHOLD:
            alerts.append({"ticker": ticker, **result})
        if (i + 1) % 50 == 0:
            print(f"  Progreso: {i+1}/{len(tickers)}")
        time.sleep(0.1)  # evitar saturar la API de Yahoo

    return alerts


def run_scan_and_notify():
    today = datetime.now().strftime("%Y-%m-%d")
    alerts = scan_market()

    new_alerts = []
    for a in alerts:
        key = f"{today}_{a['ticker']}"
        if key not in ya_avisados_hoy:
            new_alerts.append(a)
            ya_avisados_hoy.add(key)

    if new_alerts:
        msg = f"🔴 <b>SOBREVENTA DETECTADA</b> (RSI &lt; {RSI_THRESHOLD})\n\n"
        for a in sorted(new_alerts, key=lambda x: x["rsi"]):
            msg += f"<b>{a['ticker']}</b> — RSI: {a['rsi']} — ${a['price']}\n"
        msg += f"\n🕐 {datetime.now().strftime('%H:%M')} | Total señales: {len(alerts)}"
        send_telegram_message(msg)
        print(f"Alerta enviada: {len(new_alerts)} nuevas señales")
    else:
        print(f"Sin nuevas señales. Total activas: {len(alerts)}")


def reset_daily_alerts():
    """Resetea la lista de avisados al cambiar de día"""
    global ya_avisados_hoy
    ya_avisados_hoy = set()


if __name__ == "__main__":
    # Lanzar mini servidor web en segundo plano (necesario para Render Free)
    threading.Thread(target=run_web_server, daemon=True).start()

    send_telegram_message("✅ Screener S&P500 iniciado. Vigilando RSI &lt; 30 cada " + str(CHECK_INTERVAL_MINUTES) + " min en horario de mercado.")

    last_day = datetime.now().day

    while True:
        current_day = datetime.now().day
        if current_day != last_day:
            reset_daily_alerts()
            last_day = current_day

        if is_market_open():
            try:
                run_scan_and_notify()
            except Exception as e:
                print(f"Error en el escaneo: {e}")
        else:
            print(f"[{datetime.now()}] Mercado cerrado, esperando...")

        time.sleep(CHECK_INTERVAL_MINUTES * 60)
