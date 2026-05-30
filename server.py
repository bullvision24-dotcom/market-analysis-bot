import asyncio
import logging
import os
import requests
import yfinance as yf
import pytz
from datetime import datetime
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("markets-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
FMP_API_KEY = os.environ.get("FMP_API_KEY")

ITALY_TZ = pytz.timezone("Europe/Rome")

bot = Bot(token=BOT_TOKEN)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

DISCLAIMER = "⚠️ <i>Contenuto generato da AI. Non costituisce consiglio finanziario. Fai sempre le tue valutazioni.</i>"
SEPARATOR = "━━━━━━━━━━━━━━━"


def is_weekend():
    return datetime.now(ITALY_TZ).weekday() >= 5


def get_market_data():
    data = {}

    tickers = {
        "Oro (XAU)": "GC=F",
        "Argento (XAG)": "SI=F",
        "EUR/USD": "EURUSD=X",
        "GBP/USD": "GBPUSD=X",
        "S&P 500": "^GSPC",
        "Nasdaq": "^IXIC",
        "Apple": "AAPL",
        "Meta": "META",
    }

    if is_weekend():
        tickers = {
            "Oro (XAU)": "GC=F",
            "Argento (XAG)": "SI=F",
        }

    for name, ticker in tickers.items():
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info

            price = info.last_price
            prev_close = info.previous_close

            if price is None or prev_close is None:
                continue

            change = ((price - prev_close) / prev_close) * 100 if prev_close else 0
            data[name] = {"price": price, "change": change}

        except Exception as e:
            logger.error(f"Error fetching {name}: {e}")

    return data


def get_crypto_data():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        return {
            "Bitcoin (BTC)": {
                "price": data["bitcoin"]["usd"],
                "change": data["bitcoin"]["usd_24h_change"],
            },
            "Ethereum (ETH)": {
                "price": data["ethereum"]["usd"],
                "change": data["ethereum"]["usd_24h_change"],
            },
        }

    except Exception as e:
        logger.error(f"Error fetching crypto: {e}")
        return {}


def get_economic_events():
    try:
        today = datetime.now(ITALY_TZ).strftime("%Y-%m-%d")
        url = "https://financialmodelingprep.com/stable/economic-calendar"

        params = {
            "from": today,
            "to": today,
            "apikey": FMP_API_KEY,
        }

        resp = requests.get(url, params=params, timeout=10)

        logger.info(f"FMP status code: {resp.status_code}")
        logger.info(f"FMP response text: {resp.text[:300]}")

        if resp.status_code == 402:
            logger.warning("FMP economic calendar is restricted on current subscription.")
            return []

        if resp.status_code != 200:
            logger.error(f"FMP HTTP error: {resp.status_code}")
            return []

        try:
            data = resp.json()
        except Exception as json_error:
            logger.error(f"FMP JSON decode error: {json_error}")
            logger.error(f"FMP raw response: {resp.text[:500]}")
            return []

        if not isinstance(data, list):
            logger.error(f"FMP returned non-list response: {data}")
            return []

        return [
            e for e in data
            if isinstance(e, dict) and e.get("impact") in ["High", "Medium"]
        ]

    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []


def format_prices(market_data, crypto_data):
    lines = []

    metalli = {k: v for k, v in market_data.items() if k in ["Oro (XAU)", "Argento (XAG)"]}
    forex = {k: v for k, v in market_data.items() if k in ["EUR/USD", "GBP/USD"]}
    indici = {k: v for k, v in market_data.items() if k in ["S&P 500", "Nasdaq"]}
    azionario = {k: v for k, v in market_data.items() if k in ["Apple", "Meta"]}

    if metalli:
        lines.append("🥇 <b>METALLI</b>")
        for name, d in metalli.items():
            arrow = "🟢" if d["change"] >= 0 else "🔴"
            sign = "+" if d["change"] >= 0 else ""
            lines.append(f"{arrow} {name}: <b>{d['price']:.2f}</b> ({sign}{d['change']:.2f}%)")

    if forex:
        lines.append("\n💱 <b>FOREX</b>")
        for name, d in forex.items():
            arrow = "🟢" if d["change"] >= 0 else "🔴"
            sign = "+" if d["change"] >= 0 else ""
            lines.append(f"{arrow} {name}: <b>{d['price']:.4f}</b> ({sign}{d['change']:.2f}%)")

    if indici:
        lines.append("\n📊 <b>INDICI</b>")
        for name, d in indici.items():
            arrow = "🟢" if d["change"] >= 0 else "🔴"
            sign = "+" if d["change"] >= 0 else ""
            lines.append(f"{arrow} {name}: <b>{d['price']:,.2f}</b> ({sign}{d['change']:.2f}%)")

    if azionario:
        lines.append("\n🏢 <b>AZIONARIO</b>")
        for name, d in azionario.items():
            arrow = "🟢" if d["change"] >= 0 else "🔴"
            sign = "+" if d["change"] >= 0 else ""
            lines.append(f"{arrow} {name}: <b>${d['price']:.2f}</b> ({sign}{d['change']:.2f}%)")

    if crypto_data:
        lines.append("\n🪙 <b>CRYPTO</b>")
        for name, d in crypto_data.items():
            arrow = "🟢" if d["change"] >= 0 else "🔴"
            sign = "+" if d["change"] >= 0 else ""
            lines.append(f"{arrow} {name}: <b>${d['price']:,.2f}</b> ({sign}{d['change']:.2f}%)")

    return "\n".join(lines)


def get_ai_analysis(market_data, crypto_data, session_type, events):
    prices_text = format_prices(market_data, crypto_data)

    events_text = ""
    if events:
        events_text = "\n\nEventi economici disponibili:\n"
        for e in events[:5]:
            impact = "🔴" if e.get("impact") == "High" else "🟡"
            events_text += (
                f"{impact} {e.get('event')} ({e.get('country')}) — "
                f"Atteso: {e.get('estimate', 'N/D')} — "
                f"Precedente: {e.get('previous', 'N/D')}\n"
            )

    weekend_note = (
        "\nNota: è weekend. Forex e indici sono chiusi; non fare analisi operative su mercati chiusi."
        if is_weekend()
        else ""
    )

    session_labels = {
        "morning": "apertura dei mercati europei",
        "wallstreet": "apertura di Wall Street",
        "recap": "recap pomeridiano",
        "close": "chiusura dei mercati",
    }

    session_label = session_labels.get(session_type, "aggiornamento mercati")

    prompt = f"""Sei un analista finanziario professionale, prudente e molto preciso. Devi scrivere un'analisi per trader per {session_label}.

DATI DISPONIBILI:
{prices_text}
{events_text}{weekend_note}

REGOLE RIGIDE:
- Usa SOLO i dati presenti sopra.
- NON inventare supporti, resistenze, target, trendline, volumi, pattern grafici, notizie o dati macro non presenti.
- NON dire che un asset è bullish o bearish se hai solo prezzo e variazione percentuale.
- NON fare previsioni aggressive.
- NON dare segnali buy/sell.
- NON parlare di rotture tecniche se non hai livelli tecnici reali.
- Se i dati sono insufficienti, dichiaralo chiaramente.
- Puoi commentare solo: prezzo attuale, variazione percentuale, forza/debolezza relativa tra asset.
- Usa italiano professionale, diretto e prudente.
- Usa SOLO HTML Telegram: <b>grassetto</b>, <i>corsivo</i>.
- Massimo 170 parole.

STRUTTURA OBBLIGATORIA:

<b>📊 Lettura dei Dati</b>
Riassumi cosa mostrano i numeri reali, citando prezzi e variazioni.

<b>🔍 Asset da Monitorare</b>
Indica 2-3 asset con i movimenti più rilevanti. Spiega solo cosa si può dedurre dai dati disponibili.

<b>⚠️ Limiti dell'Analisi</b>
Scrivi cosa NON si può sapere da questi dati, ad esempio livelli tecnici, volumi reali o direzione certa.

<b>💡 Nota Operativa</b>
Una frase prudente e utile per il trader, senza segnali inventati."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return "<i>Analisi temporaneamente non disponibile.</i>"


def get_ai_event_analysis(event):
    nome = event.get("event", "")
    paese = event.get("country", "")
    ora = event.get("date", "")
    actual = event.get("actual", "N/D")
    stima = event.get("estimate", "N/D")
    precedente = event.get("previous", "N/D")
    impatto = event.get("impact", "")

    prompt = f"""Sei un analista finanziario professionale e prudente. Analizza questo evento economico reale.

Evento: {nome}
Paese: {paese}
Orario: {ora}
Dato uscito: {actual}
Consensus atteso: {stima}
Precedente: {precedente}
Impatto: {impatto}

REGOLE:
- Usa SOLO i dati forniti.
- Non inventare livelli tecnici.
- Non dare segnali buy/sell.
- Se il dato è N/D, specifica che non è ancora disponibile.
- Usa SOLO tag HTML Telegram: <b>grassetto</b>, <i>corsivo</i>.
- Massimo 160 parole.

STRUTTURA:

<b>📌 Cos'è e Perché Conta</b>
Spiegazione concisa dell'indicatore.

<b>📊 Dato vs Attese</b>
Commento numerico preciso.

<b>⚡ Possibile Impatto</b>
Scenario prudente, senza direzioni certe.

<b>⚠️ Nota</b>
Limiti dell'analisi."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=450,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    except Exception as e:
        logger.error(f"AI event error: {e}")
        return "<i>Analisi evento non disponibile.</i>"


async def send_market_update(session_type, header_emoji, header_text):
    if is_weekend() and session_type in ["wallstreet", "recap"]:
        logger.info(f"Skipping {session_type} — weekend")
        return

    try:
        market_data = get_market_data()
        crypto_data = get_crypto_data()
        events = get_economic_events()
        now = datetime.now(ITALY_TZ).strftime("%d/%m/%Y %H:%M")
        prices = format_prices(market_data, crypto_data)

        msg1 = f"""{header_emoji} <b>{header_text}</b>
📅 {now}
{SEPARATOR}

{prices}

{SEPARATOR}
<i>AI MarketsAnalysis 📊</i>"""

        await bot.send_message(chat_id=CHAT_ID, text=msg1, parse_mode="HTML")
        logger.info(f"Sent prices for {session_type}")

        await asyncio.sleep(6)

        analysis = get_ai_analysis(market_data, crypto_data, session_type, events)

        msg2 = f"""🤖 <b>ANALISI MERCATI</b>
{SEPARATOR}

{analysis}

{DISCLAIMER}
<i>AI MarketsAnalysis 📊</i>"""

        await bot.send_message(chat_id=CHAT_ID, text=msg2, parse_mode="HTML")
        logger.info(f"Sent analysis for {session_type}")

    except Exception as e:
        logger.error(f"Error sending {session_type} update: {e}")


async def check_and_send_events():
    if is_weekend():
        return

    try:
        events = get_economic_events()
        now = datetime.now(ITALY_TZ)

        for event in events:
            try:
                event_time_str = event.get("date", "")
                if not event_time_str:
                    continue

                event_time = datetime.fromisoformat(
                    event_time_str.replace("Z", "+00:00")
                ).astimezone(ITALY_TZ)

                diff = (event_time - now).total_seconds()

                if event.get("actual") and abs(diff) < 1800:
                    analysis = get_ai_event_analysis(event)
                    impact_emoji = "🔴" if event.get("impact") == "High" else "🟡"

                    msg = f"""{impact_emoji} <b>EVENTO ECONOMICO</b>
{SEPARATOR}
📌 <b>{event.get('event', '').upper()}</b>
🌍 {event.get('country', '')} | 🕐 {event_time.strftime('%H:%M')}

📊 Atteso: <b>{event.get('estimate', 'N/D')}</b> | Uscito: <b>{event.get('actual', 'N/D')}</b> | Precedente: <b>{event.get('previous', 'N/D')}</b>

{analysis}

{SEPARATOR}
{DISCLAIMER}
<i>AI MarketsAnalysis 📊</i>"""

                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
                    logger.info(f"Sent event: {event.get('event')}")

            except Exception as e:
                logger.error(f"Error processing event: {e}")

    except Exception as e:
        logger.error(f"Error in check_and_send_events: {e}")


async def morning_update():
    await send_market_update("morning", "🌅", "APERTURA MERCATI EUROPEI")


async def wallstreet_update():
    await send_market_update("wallstreet", "🇺🇸", "APERTURA WALL STREET")


async def recap_update():
    await send_market_update("recap", "📋", "RECAP POMERIDIANO")


async def close_update():
    await send_market_update("close", "🌙", "CHIUSURA MERCATI & OUTLOOK")


async def main():
    logger.info("Starting AI MarketsAnalysis Bot...")

    scheduler = AsyncIOScheduler(timezone=ITALY_TZ)

    scheduler.add_job(morning_update, "cron", hour=8, minute=0)
    scheduler.add_job(wallstreet_update, "cron", hour=14, minute=30)
    scheduler.add_job(recap_update, "cron", hour=18, minute=0)
    scheduler.add_job(close_update, "cron", hour=22, minute=0)
    scheduler.add_job(check_and_send_events, "cron", minute="*/30")

    scheduler.start()

    logger.info("Scheduler started. Sending test message...")
    await send_market_update("morning", "🚀", "BOT AVVIATO - TEST MERCATI")

    logger.info("Bot running.")

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
