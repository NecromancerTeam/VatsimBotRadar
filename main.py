import os
import re
import glob
import json
import asyncio
import logging
import aiohttp

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logging.error("Токен бота не найден Убедитесь, что он есть в файле .env")
    exit()
    
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

vatsimData= "https://data.vatsim.net/v3/vatsim-data.json"
metarApi = "https://metar.vatsim.net/"

DICT_FILE = "metar_dict.json"
DEFAULT_DICT = {
  "ru": {
    "CAVOK": "Ясно (видимость более 10км, нет облаков)",
    "NOSIG": "Без существенных изменений",
    "BR": "Дымка",
    "FG": "Туман",
    "RA": "Дождь",
    "SN": "Снег",
    "SH": "Ливень",
    "TS": "Гроза",
    "-": "Слабый ",
    "+": "Сильный ",
    "BKN": "Значительная облачность",
    "OVC": "Сплошная облачность",
    "SCT": "Рассеянная облачность",
    "FEW": "Незначительная облачность"
  },
  "uk": {
    "CAVOK": "Ясно (видимість більше 10км, немає хмар)",
    "NOSIG": "Без суттєвих змін",
    "BR": "Серпанок",
    "FG": "Туман",
    "RA": "Дощ",
    "SN": "Сніг",
    "SH": "Злива",
    "TS": "Гроза",
    "-": "Слабкий ",
    "+": "Сильний ",
    "BKN": "Значна хмарність",
    "OVC": "Суцільна хмарність",
    "SCT": "Розсіяна хмарність",
    "FEW": "Незначна хмарність"
  }
}

if not os.path.exists(DICT_FILE):
    with open(DICT_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_DICT, f, ensure_ascii=False, indent=4)
    logging.info(f"Файл словаря {DICT_FILE} создан!")
with open(DICT_FILE, "r", encoding="utf-8") as f:
    METAR_DICT = json.load(f)
def translate_metar(metar_text: str, lang: str = "ru") -> str:
    if "Нет данных" in metar_text:
        return metar_text
    if lang not in METAR_DICT:
        lang = "ru" 
    dictionary = METAR_DICT[lang]
    parts = metar_text.split()
    translated_parts = []
    for part in parts:
        if part in dictionary:
            translated_parts.append(dictionary[part])
            continue
        translated_part = part
        for key, value in dictionary.items():
            if key in translated_part and key not in ["-", "+"]: 
                translated_part = translated_part.replace(key, f"{value} ")
        if translated_part.startswith("-"):
            translated_part = translated_part.replace("-", dictionary.get("-", "Слабый "), 1)
        elif translated_part.startswith("+"):
            translated_part = translated_part.replace("+", dictionary.get("+", "Сильный "), 1)
        wind_match = re.match(r'^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$', part)
        if wind_match:
            dir_wind = wind_match.group(1)
            spd_wind = wind_match.group(2)
            gusts = f", порывы {wind_match.group(3)}" if wind_match.group(3) else ""
            unit = "узлов" if wind_match.group(4) == "KT" else "м/с"
            
            dir_str = "Переменный" if dir_wind == "VRB" else f"{dir_wind}°"
            translated_part = f"Ветер {dir_str}, {int(spd_wind)}{gusts} {unit}"

        translated_parts.append(translated_part.strip())
    return " | ".join(translated_parts)
def get_start_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Vatsim Radar",
        url="https://vatsim-radar.com"
    )
    return builder.as_markup()
async def fetch_vatsim(session: aiohttp.ClientSession) -> dict:
    try:
        async with session.get(VATSIM_DATA, timeout=15) as r:
            if r.status == 200:
                return await r.json()
    except aiohttp.ClientError as e:
        logging.error(f"Ошибка получения данных vatsim: {e}")
    return {}
async def fetch_metar(session: aiohttp.ClientSession, icao: str) -> str:
    try:
        async with session.get(f"{metarApi}{icao}", timeout=15) as r:
            if r.status == 200:
                text = await r.text()
                if text.strip():
                    return text.strip()
    except aiohttp.ClientError as e:
        logging.error(f"Ошибка получения METAR: {e}")  
    return "Нет данных ⚠️"
def get_runway(atis_list: list, icao: str) -> str:
    patterns = [
        r"ARR RWY (\d+[LRC]?)",
        r"DEP RWY (\d+[LRC]?)",
        r"RUNWAY IN USE (\d+[LRC]?)",
        r"LANDING RUNWAY (\d+[LRC]?)",
        r"TAKEOFF RUNWAY (\d+[LRC]?)",
        r"RUNWAY (\d+[LRC]?)",
        r"RWY (\d+[LRC]?)",
    ]
    for atis in atis_list:
        if not atis.get("callsign", "").upper().startswith(f"{icao}_"):
            continue
        text = " ".join(atis.get("text_atis", [])).upper()
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
    return "Неизвестно"
def get_controller(controllers: list, icao: str, positions: list) -> tuple:
    for controller in controllers:
        callsign = controller.get("callsign", "").upper()
        if not callsign.startswith(f"{icao}_"):
            continue
        for pos in positions:
            if pos in callsign:
                return callsign, controller.get("frequency", "")
    return None, None
def get_ctr(controllers: list, icao: str) -> tuple:
    prefix = icao[:2]
    for controller in controllers:
        callsign = controller.get("callsign", "").upper()
        if callsign.startswith(prefix) and "_CTR" in callsign:
            return callsign, controller.get("frequency", "")
    return None, None


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет!\n\n"
        "Введите ICAO код аэропорта.\n"
        " Все данные берутся из VATSIM.",
        reply_markup=get_start_keyboard()
    )


@dp.message(Command("help"))
async def help_bot(message: types.Message):
    await message.answer(
        "Просто отправьте 4 буквы ICAO аэропорта.\n\n"
        "Например:\n"
        "<code>UUEE</code>\n"
        "<code>ESSA</code>\n"
        "<code>ULLI</code>"
    )


@dp.message(Command("info"))
async def info_bot(message: types.Message):
    await message.answer(
        "👋 Привет!\n"
        "Бот получает данные напрямую из VATSIM API.\n"
        "Сделано на Aiogram 3.\n\n"
        "По вопросам: @linuxangel"
    )



@dp.message(F.text.regexp(r"^[a-zA-Z]{4}$"))
async def handle_airport(message: types.Message):
    icao = message.text.upper()
    async with aiohttp.ClientSession() as session:
        metar_raw, vatsim = await asyncio.gather(
            fetch_metar(session, icao),
            fetch_vatsim(session)
        )

    if not vatsim:
        await message.answer("Не удалось получить данные от VATSIM.")
        return

    metar_translated = translate_metar(metar_raw, lang="ru")

    controllers = vatsim.get("controllers", [])
    atis_list = vatsim.get("atis", [])
    runway = get_runway(atis_list, icao)

    no_ctrl_text = "Нет контроля 👤"

    _, freq = get_controller(controllers, icao, ["_DEL", "_GND"])
    gnd_val = f"{freq} MHz 👥️" if freq else no_ctrl_text

    _, freq = get_controller(controllers, icao, ["_TWR"])
    twr_val = f"{freq} MHz 👥️" if freq else no_ctrl_text


    call, freq = get_controller(controllers, icao, ["_APP", "_DEP", "_DIRECTOR", "_FINAL"])
    if freq:
        app_val = f"{call} — {freq} MHz 👥️"
    else:
        call, freq = get_ctr(controllers, icao)
        app_val = f"{call} — {freq} MHz 👥️" if freq else no_ctrl_text


    atis_val = no_ctrl_text
    for atis in atis_list:
        if atis.get("callsign", "").upper().startswith(f"{icao}_"):
            freq = atis.get("frequency")
            atis_val = f"{freq} MHz 👥️"
            break

    response_text = (
        f"🤩 <b>{icao}</b>\n\n"
        f"⛅️ <b>METAR:</b>\n"
        f"<code>{metar_raw}</code>\n"
        f"<i>{metar_translated}</i>\n\n"
        f"🌟 Рабочая полоса — {runway}\n\n"
        f"📢 <b>Активные диспетчеры:</b>\n"
        f"📚 Руление — {gnd_val}\n"
        f"🔼 Взлет/Посадка — {twr_val}\n"
        f"📊 Округ / РЦ — {app_val}\n"
        f"🌥 Погода и информация (ATIS) — {atis_val}"
    )

    builder = InlineKeyboardBuilder()

    if glob.glob(f"Chart/{icao.lower()}.*"):
        builder.button(
            text="Скачать чарты 📥",
            callback_data=f"chart:{icao}"
        )

    await message.answer(
        response_text,
        reply_markup=builder.as_markup() if builder.buttons else None
    )


@dp.callback_query(F.data.startswith("chart:"))
async def send_chart(callback: types.CallbackQuery):
    icao = callback.data.split(":")[1].lower()
    files = glob.glob(f"Chart/{icao}.*")
    if not files:
        await callback.answer("Файл чарта отсутствует.", show_alert=True)
        return
    await callback.answer("Отправляю чарты...")
    await callback.message.answer_document(FSInputFile(files[0]))
async def main():
    os.makedirs("Chart", exist_ok=True)
    logging.info("Бот запущен!.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.Спасибо")
