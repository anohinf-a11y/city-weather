"""
CityWeather: текущая погода по списку городов (API wttr.in) и статистика по странам.

Только стандартная библиотека Python 3.8+ (json, urllib), внешних пакетов нет.

Запуск:
    python city_weather.py                      # Cities.txt рядом/в текущей папке, иначе список с gistpad
    python city_weather.py path/to/Cities.txt   # свой файл
    python city_weather.py https://...          # список по ссылке
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Tuple
from decimal import Decimal, ROUND_HALF_UP


DEFAULT_URL = "https://gistpad.com/raw/vk-task-14"
DEFAULT_FILE = "Cities.txt"

# В условии указано "format=jl"; документированный JSON-формат wttr.in называется j1 (единица)
API_URL = "https://wttr.in/{city}?format=j1"

TIMEOUT_SEC = 30
MAX_ATTEMPTS = 3
MAX_WORKERS = 4


@dataclass
class WeatherData:
    """Погода в одном городе: город, текущая температура в °C, страна."""
    city: str
    temperature_c: float
    country: str


#  загрузка списка

def http_get(url: str) -> str:
    """GET с несколькими попытками: wttr.in иногда отвечает 5xx/429 или отваливается по таймауту."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CityWeather/1.0"})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                return response.read().decode("utf-8-sig")  # utf-8-sig убирает BOM, если он есть
        except urllib.error.HTTPError as e:
            transient = e.code >= 500 or e.code == 429
            if not transient or attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"HTTP {e.code} {e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"сетевая ошибка: {e}") from e
        time.sleep(attempt * 2)
    raise RuntimeError("не удалось получить ответ")  # недостижимо, для линтеров


def parse_cities(text: str) -> List[str]:
    """Уникальные города (без учёта регистра) в порядке появления; пустые строки и пробелы отбрасываются."""
    unique = {}
    for line in text.splitlines():
        city = line.strip().lstrip("\ufeff").strip()
        if city:
            unique.setdefault(city.casefold(), city)
    return list(unique.values())


def load_cities(source: Optional[str]) -> Tuple[List[str], str]:
    """
    Источник (по приоритету): аргумент (путь или ссылка) -> Cities.txt в текущей папке
    или рядом со скриптом -> ссылка по умолчанию.
    """
    if source and source.lower().startswith(("http://", "https://")):
        return parse_cities(http_get(source)), source

    if source:
        if not os.path.isfile(source):
            raise FileNotFoundError(f"Файл со списком городов не найден: {source}")
        return parse_cities(read_file(source)), source

    for folder in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        path = os.path.join(folder, DEFAULT_FILE)
        if os.path.isfile(path):
            return parse_cities(read_file(path)), path

    return parse_cities(http_get(DEFAULT_URL)), DEFAULT_URL


def read_file(path: str) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


#  погода

def parse_weather(city: str, text: str) -> WeatherData:
    """
    Нужные поля ответа wttr.in:
      current_condition[0].temp_C       - текущая температура (строка, °C)
      nearest_area[0].country[0].value  - страна
    В поле city остаётся название из списка (сервис может вернуть другое написание).
    """
    try:
        data = json.loads(text)
        temperature = float(data["current_condition"][0]["temp_C"])
        country = data["nearest_area"][0]["country"][0]["value"].strip()
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        raise ValueError("не удалось разобрать ответ сервиса (не тот формат или город не найден)") from e

    if not country:
        raise ValueError("в ответе нет страны")
    return WeatherData(city, temperature, country)


def fetch_weather(city: str) -> Tuple[str, Optional[WeatherData], Optional[str]]:
    """Возвращает (город, данные, текст ошибки); исключения наружу не выпускает."""
    try:
        url = API_URL.format(city=urllib.parse.quote(city, safe=""))
        return city, parse_weather(city, http_get(url)), None
    except Exception as e:  # одна проблемная строка не должна ронять весь список
        return city, None, str(e)


#  вывод

def fmt_temp(value: float) -> str:
    """+18, -3, 0, +19.3 : знак виден всегда, дробная часть только если она есть."""
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)  # 4.25 -> 4.3, а не 4.2
    if rounded == 0:
        return "0"
    if rounded == rounded.to_integral_value():
        return f"{int(rounded):+d}"
    return f"{rounded:+.1f}"


def main(argv: List[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # 1. Загружаем города
    cities, source = load_cities(argv[1] if len(argv) > 1 else None)

    print("=" * 65)
    print("          CITY WEATHER — ПОГОДА ПО ГОРОДАМ")
    print("=" * 65)
    print(f"Источник списка: {source}")
    print(f"Количество уникальных городов: {len(cities)}")
    print("-" * 65)

    if not cities:
        print("Список городов пуст.")
        return 1

    # 2. Получаем погоду
    print("\n[1/3] ЗАПРОС ПОГОДЫ\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(fetch_weather, cities))

    weather = []

    for number, (city, data, error) in enumerate(results, start=1):
        if data:
            weather.append(data)
            print(
                f"{number:>3}. {data.city}, {data.country}: "
                f"{fmt_temp(data.temperature_c)} °C"
            )
        else:
            print(f"{number:>3}. {city}: ОШИБКА — {error}")

    if not weather:
        print("\nНе удалось получить данные ни по одному городу.")
        return 1

    # 3. Статистика по странам
    groups = {}

    for item in weather:
        groups.setdefault(item.country.casefold(), []).append(item)

    print("\n" + "=" * 65)
    print("[2/3] СТАТИСТИКА ПО СТРАНАМ")
    print("=" * 65)

    for key in sorted(groups):
        items = groups[key]
        temps = [item.temperature_c for item in items]
        count = len(items)
        city_word = "город" if count == 1 else "городов"

        print(f"\nСтрана: {items[0].country}")
        print(f"  Количество {city_word}: {count}")
        print(f"  Средняя температура: {fmt_temp(sum(temps) / count)} °C")
        print(f"  Минимальная температура: {fmt_temp(min(temps))} °C")
        print(f"  Максимальная температура: {fmt_temp(max(temps))} °C")
        print(f"  Города: {', '.join(item.city for item in items)}")

    # 4. Итог выполнения
    failed = len(results) - len(weather)

    print("\n" + "=" * 65)
    print("[3/3] ИТОГ")
    print("=" * 65)
    print(f"Всего уникальных городов: {len(cities)}")
    print(f"Успешно обработано: {len(weather)}")
    print(f"Ошибок получения данных: {failed}")
    print(f"Стран в статистике: {len(groups)}")
    print("=" * 65)

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
