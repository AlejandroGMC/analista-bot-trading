"""
=============================================================================
BOT DE TRADING - ROMPIMIENTO DE ESTRUCTURA (BOS) | Temporalidad: 8H
=============================================================================
Autor       : Desarrollado para Alejandro (Asesor Financiero / Dev Jr.)
Exchange    : Binance (vía ccxt)
Estrategia  : Break of Structure (BOS) con confirmación por cierre de vela
Timeframe   : 8 horas
Optimizado  : PythonAnywhere / entornos gratuitos en la nube
=============================================================================
"""

import ccxt
import pandas as pd
import requests
import logging
import time
import os
from datetime import datetime, timezone


# =============================================================================
# 1. CONFIGURACIÓN CENTRAL
# =============================================================================

CONFIG = {
    # --- Binance ---
    "exchange": "binance",
    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],  # Activos a monitorear
    "timeframe": "8h",
    "candles_limit": 100,          # Velas a descargar (suficiente para swings)

    # --- Detección de Swings ---
    "swing_lookback": 5,           # Velas a cada lado para confirmar pivot

    # --- Telegram ---
    "telegram_token": "8709813717:AAGxGVULKMeQ7nnVwL4UcuPfJalemtVpfis",
    "telegram_chat_id": "1296239552",

    # --- Logs / Journal ---
    "log_csv": "trading_journal.csv",
    "log_txt": "bot_events.log",

    # --- Optimización nube ---
    "loop_interval_seconds": 60,   # Espera entre ciclos (ajustable)
    "request_delay_seconds": 1.5,  # Pausa entre llamadas a la API
}


# =============================================================================
# 2. LOGGER (archivo .log + consola)
# =============================================================================

def setup_logger(log_file: str) -> logging.Logger:
    """Configura logging dual: archivo y consola."""
    logger = logging.getLogger("BOS_Bot")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
        # Handler de archivo
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        # Handler de consola
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)

        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


logger = setup_logger(CONFIG["log_txt"])


# =============================================================================
# 3. MÓDULO: CONEXIÓN A BINANCE (ccxt)
# =============================================================================

def get_exchange() -> ccxt.Exchange:
    """
    Retorna una instancia del exchange.
    No requiere API Keys para datos públicos (OHLCV).
    """
    exchange = ccxt.binance({
        "enableRateLimit": True,          # Respeta los límites de Binance
        "options": {"defaultType": "spot"},
    })
    return exchange


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str,
                timeframe: str, limit: int) -> pd.DataFrame:
    """
    Descarga velas OHLCV y las devuelve como DataFrame limpio.

    Columnas: timestamp, open, high, low, close, volume
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low",
                                         "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms",
                                          utc=True)
        df = df.set_index("timestamp").astype(float)
        logger.info(f"[{symbol}] {len(df)} velas descargadas ({timeframe})")
        return df

    except ccxt.NetworkError as e:
        logger.error(f"Error de red al obtener {symbol}: {e}")
        return pd.DataFrame()
    except ccxt.ExchangeError as e:
        logger.error(f"Error del exchange para {symbol}: {e}")
        return pd.DataFrame()


# =============================================================================
# 4. MÓDULO: DETECCIÓN DE SWINGS (Puntos de Pivote)
# =============================================================================

def detect_swings(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """
    Identifica Swing Highs y Swing Lows locales.

    ¿Cómo funciona?
    ---------------
    Para cada vela 'i', compara su High/Low contra las 'lookback' velas
    anteriores Y las 'lookback' velas posteriores.

    - SWING HIGH: el High[i] es el máximo de toda la ventana 2*lookback+1
    - SWING LOW : el Low[i]  es el mínimo de toda la ventana 2*lookback+1

    Esto garantiza que el pivot es un extremo REAL, no un ruido puntual.
    Se usa la ventana centrada en i → requiere que las velas a la derecha
    ya existan (confirmación retrasada por 'lookback' velas, aceptable en 8h).

    Parámetros
    ----------
    df       : DataFrame con columnas high, low
    lookback : Número de velas a cada lado del pivot

    Retorna
    -------
    df con columnas nuevas:
        swing_high (float | NaN)  → precio del pivot alto
        swing_low  (float | NaN)  → precio del pivot bajo
    """
    df = df.copy()
    df["swing_high"] = float("nan")
    df["swing_low"]  = float("nan")

    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)

    for i in range(lookback, n - lookback):
        window_highs = highs[i - lookback : i + lookback + 1]
        window_lows  = lows[i  - lookback : i + lookback + 1]

        # El punto central debe ser el extremo de toda la ventana
        if highs[i] == max(window_highs):
            df.iloc[i, df.columns.get_loc("swing_high")] = highs[i]

        if lows[i] == min(window_lows):
            df.iloc[i, df.columns.get_loc("swing_low")] = lows[i]

    sh_count = df["swing_high"].notna().sum()
    sl_count = df["swing_low"].notna().sum()
    logger.info(f"Swings detectados → Highs: {sh_count} | Lows: {sl_count}")
    return df


def get_last_swings(df: pd.DataFrame) -> dict:
    """
    Extrae el último Swing High y Swing Low confirmados.

    Retorna dict con claves:
        last_high_price  : float
        last_high_time   : Timestamp
        last_low_price   : float
        last_low_time    : Timestamp
    """
    swings = {}

    highs = df["swing_high"].dropna()
    lows  = df["swing_low"].dropna()

    if not highs.empty:
        swings["last_high_price"] = highs.iloc[-1]
        swings["last_high_time"]  = highs.index[-1]
    else:
        swings["last_high_price"] = None
        swings["last_high_time"]  = None

    if not lows.empty:
        swings["last_low_price"] = lows.iloc[-1]
        swings["last_low_time"]  = lows.index[-1]
    else:
        swings["last_low_price"] = None
        swings["last_low_time"]  = None

    return swings


# =============================================================================
# 5. MÓDULO: LÓGICA BOS (Break of Structure)
# =============================================================================

def check_bos(df: pd.DataFrame, swings: dict) -> dict | None:
    """
    Detecta Rompimiento de Estructura (BOS) en la última vela cerrada.

    REGLA ESTRICTA (Body-Close, no wick):
    ─────────────────────────────────────
    Se usa el precio de CIERRE (close) de la vela, NO el High/Low (mechas).
    Esto elimina falsas señales por wicks agresivos que no cierran el nivel.

    BOS ALCISTA  → close[-1] > last_swing_high   (precio cierra SOBRE el máximo)
    BOS BAJISTA  → close[-1] < last_swing_low    (precio cierra BAJO el mínimo)

    Retorna None si no hay rompimiento, o un dict con los detalles del BOS.
    """
    # Usamos la penúltima vela como "última cerrada" para evitar vela en curso
    # En producción con scheduler por cierre de vela, puede usarse iloc[-1]
    last_closed  = df.iloc[-2]   # Vela completamente cerrada
    close_price  = last_closed["close"]
    close_time   = df.index[-2]

    last_high = swings.get("last_high_price")
    last_low  = swings.get("last_low_price")

    # --- BOS Alcista ---
    if last_high and close_price > last_high:
        logger.info(f"⚡ BOS ALCISTA detectado | Close: {close_price:.4f} > "
                    f"Swing High: {last_high:.4f}")
        return {
            "type"         : "BOS_ALCISTA 🟢",
            "close_price"  : close_price,
            "close_time"   : close_time,
            "broken_level" : last_high,
            "swing_time"   : swings["last_high_time"],
        }

    # --- BOS Bajista ---
    if last_low and close_price < last_low:
        logger.info(f"⚡ BOS BAJISTA detectado | Close: {close_price:.4f} < "
                    f"Swing Low: {last_low:.4f}")
        return {
            "type"         : "BOS_BAJISTA 🔴",
            "close_price"  : close_price,
            "close_time"   : close_time,
            "broken_level" : last_low,
            "swing_time"   : swings["last_low_time"],
        }

    logger.info(f"Sin BOS | Close: {close_price:.4f} | "
                f"Rango: [{last_low:.4f} – {last_high:.4f}]")
    return None


# =============================================================================
# 6. MÓDULO: NOTIFICACIONES TELEGRAM
# =============================================================================

def send_telegram_alert(token: str, chat_id: str,
                         symbol: str, bos: dict) -> bool:
    """
    Envía alerta de BOS a Telegram con formato estructurado.

    Incluye: tipo de BOS, símbolo, precio de cierre, nivel roto y timestamp.
    """
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚨 *ALERTA BOS DETECTADO*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *Símbolo:*  `{symbol}`\n"
        f"🔖 *Tipo:*     {bos['type']}\n"
        f"💵 *Cierre:*   `{bos['close_price']:.4f} USDT`\n"
        f"🎯 *Nivel roto:* `{bos['broken_level']:.4f} USDT`\n"
        f"🕐 *Tiempo cierre:* `{bos['close_time'].strftime('%Y-%m-%d %H:%M')} UTC`\n"
        f"📅 *Swing origen:* `{bos['swing_time'].strftime('%Y-%m-%d %H:%M')} UTC`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Confirmado por cierre de vela 8H (body, no wick)_"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id"    : chat_id,
        "text"       : msg,
        "parse_mode" : "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ Telegram OK → {symbol} {bos['type']}")
            return True
        else:
            logger.warning(f"Telegram error {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as e:
        logger.error(f"Error de conexión Telegram: {e}")
        return False


# =============================================================================
# 7. MÓDULO: TRADING JOURNAL (CSV)
# =============================================================================

def save_to_journal(csv_path: str, symbol: str, bos: dict) -> None:
    """
    Guarda cada detección de BOS en el journal CSV de Alejandro.

    Columnas: fecha_registro, simbolo, tipo_bos, precio_cierre,
              nivel_roto, tiempo_cierre, tiempo_swing
    """
    row = {
        "fecha_registro" : datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "simbolo"        : symbol,
        "tipo_bos"       : bos["type"],
        "precio_cierre"  : bos["close_price"],
        "nivel_roto"     : bos["broken_level"],
        "tiempo_cierre"  : bos["close_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "tiempo_swing"   : bos["swing_time"].strftime("%Y-%m-%d %H:%M:%S"),
    }

    file_exists = os.path.isfile(csv_path)
    df_row = pd.DataFrame([row])

    df_row.to_csv(csv_path, mode="a",
                  header=not file_exists,
                  index=False, encoding="utf-8")
    logger.info(f"📒 Journal actualizado → {csv_path}")


# =============================================================================
# 8. MOTOR PRINCIPAL: CICLO DE ANÁLISIS
# =============================================================================

def analyze_symbol(exchange: ccxt.Exchange, symbol: str) -> None:
    """
    Pipeline completo para un símbolo:
      1. Descarga datos
      2. Detecta swings
      3. Evalúa BOS
      4. Notifica y guarda si hay señal
    """
    logger.info(f"{'─'*50}")
    logger.info(f"Analizando: {symbol}")

    # Paso 1: Datos
    df = fetch_ohlcv(exchange,
                     symbol,
                     CONFIG["timeframe"],
                     CONFIG["candles_limit"])

    if df.empty or len(df) < CONFIG["swing_lookback"] * 2 + 5:
        logger.warning(f"Datos insuficientes para {symbol}, omitiendo.")
        return

    # Paso 2: Swings
    df = detect_swings(df, lookback=CONFIG["swing_lookback"])
    swings = get_last_swings(df)

    if not swings["last_high_price"] or not swings["last_low_price"]:
        logger.warning(f"No se encontraron swings para {symbol}, omitiendo.")
        return

    logger.info(f"Último Swing High: {swings['last_high_price']:.4f} "
                f"@ {swings['last_high_time']}")
    logger.info(f"Último Swing Low : {swings['last_low_price']:.4f} "
                f"@ {swings['last_low_time']}")

    # Paso 3: BOS
    bos = check_bos(df, swings)

    # Paso 4: Alertas
    if bos:
        send_telegram_alert(CONFIG["telegram_token"],
                             CONFIG["telegram_chat_id"],
                             symbol, bos)
        save_to_journal(CONFIG["log_csv"], symbol, bos)
    else:
        logger.info(f"Sin señal BOS para {symbol} en este ciclo.")


def run_bot() -> None:
    """
    Bucle principal del bot.
    Optimizado para PythonAnywhere:
      - Sin hilos, ciclo secuencial
      - Delay entre requests para no agotar cuota gratuita
      - Logs limpios para debugging remoto
    """
    logger.info("=" * 60)
    logger.info("  BOT BOS 8H INICIADO")
    logger.info(f"  Símbolos : {CONFIG['symbols']}")
    logger.info(f"  Timeframe: {CONFIG['timeframe']}")
    logger.info(f"  Lookback : {CONFIG['swing_lookback']} velas")
    logger.info("=" * 60)

    exchange = get_exchange()

    while True:
        cycle_start = datetime.now(timezone.utc)
        logger.info(f"\n🔄 NUEVO CICLO | {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        for symbol in CONFIG["symbols"]:
            try:
                analyze_symbol(exchange, symbol)
            except Exception as e:
                logger.error(f"Error inesperado en {symbol}: {e}")

            # Pausa entre activos → evita ban por rate limit
            time.sleep(CONFIG["request_delay_seconds"])

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        wait    = max(0, CONFIG["loop_interval_seconds"] - elapsed)
        logger.info(f"\n⏳ Esperando {wait:.0f}s hasta el próximo ciclo...\n")
        time.sleep(wait)


# =============================================================================
# 9. ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    run_bot()
