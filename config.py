"""تنظیمات مرکزی بازی.

همهٔ عددهای مربوط به اقتصاد، سرعت، آپگریدها و بالانس مسابقه فقط در همین فایل هستند.
برای تغییر بالانس بازی نیازی به دست زدن به بقیهٔ کدها نیست.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _load_dotenv() -> None:
    """خواندن فایل .env کنار پروژه (اگر وجود داشته باشد)."""
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# ─────────────────────────── ربات و زیرساخت ───────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "racing.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─────────────────────────── مسابقه ───────────────────────────
RACE_DISTANCE = 1000          # مسافت پیش‌فرض (متر)
MIN_PLAYERS = 2
MAX_PLAYERS = 10
LOBBY_TIMEOUT = 900           # ثانیه؛ لابی بدون شروع بعد از این مدت لغو می‌شود
RACE_TIMEOUT = 150            # ثانیه؛ سقف ایمنی طول مسابقه
COUNTDOWN_STEP = 1.0          # فاصلهٔ اعداد شمارش معکوس (ثانیه)

TICK_SECONDS = 1.5            # هر چند ثانیه پیام مسابقه ادیت شود (محدودیت تلگرام؛ اگر خطای 429 دیدید بیشترش کنید)
SIM_STEP = 0.1                # گام شبیه‌سازی (ثانیه)
DISTANCE_SCALE = 0.62         # ضریب تبدیل سرعت به پیشروی؛ زمان کل مسابقه را تنظیم می‌کند
EVENT_TTL = 7.0               # مدت نمایش رویدادها (سبقت، تعویض عالی و ...)
EVENT_LIMIT = 4               # حداکثر تعداد رویداد نمایش‌داده‌شده

# ─────────────────────────── دنده و RPM ───────────────────────────
GEAR_COUNT = 6
REDLINE_RPM = 8000
IDLE_RPM = 1500

# سرعت (km/h) در RPM حداکثر برای هر دنده (قبل از اعمال آپگریدها)
GEAR_TOP_SPEED = [55, 90, 130, 170, 210, 250]
# سرعت بالا رفتن RPM (دور در ثانیه) در هر دنده
GEAR_RPM_RATE = [560, 440, 380, 340, 310, 260]
# ابتدای محدودهٔ سبز (Perfect Shift) برای هر دنده
GREEN_START_RPM = [6000, 6200, 6300, 6400, 6500, 6500]
GREEN_SECONDS = 2.6           # مدت زمان محدودهٔ سبز (ثانیه)، قبل از اثر گیربکس
GREEN_MAX_END_RPM = 7700      # انتهای محدودهٔ سبز از این عدد بیشتر نمی‌شود
MISS_TIMEOUT = 2.0            # چند ثانیه بعد از خروج از سبز = Shift Missed
PERFECT_GRACE = 0.5           # ارفاق تأخیر شبکه/پیام (ثانیه) برای Perfect Shift
LOW_GEAR_MAX = 3              # دنده‌های ۱ تا این عدد «دنده سبک» حساب می‌شوند (اثر لاستیک)

PERFECT_SPEED_BONUS = 0.02    # افزایش آنی سرعت بعد از Perfect Shift
PERFECT_BOOST = 0.08          # +8٪ شتاب/پیشروی
PERFECT_BOOST_SECONDS = 2.5   # مدت اثر Perfect Shift
MISS_PENALTY = 0.03           # جریمهٔ سرعت بعد از Missed Shift (۰ تا ۰٫۰۴؛ ۰ = حفظ سرعت)

# ─────────────────────────── جایزه‌ها (Coin) بر اساس تعداد بازیکنان ───────────────────────────
REWARDS: dict[int, list[int]] = {
    2: [300, 150],
    3: [400, 250, 150],
    4: [450, 300, 200, 100],
    5: [500, 350, 250, 150, 100],
    6: [600, 450, 350, 250, 175, 100],
    7: [700, 550, 450, 350, 250, 175, 100],
    8: [800, 600, 500, 400, 300, 225, 150, 100],
    9: [900, 675, 550, 450, 350, 275, 200, 150, 100],
    10: [1000, 750, 600, 500, 400, 300, 250, 200, 150, 100],
}

# ─────────────────────────── آپگریدها ───────────────────────────
START_LEVEL = 1
MAX_LEVEL = 50

# key: (نام فارسی، ایموجی، ضریب هزینه نسبت به موتور، توضیح کوتاه)
UPGRADES: dict[str, dict] = {
    "engine":       {"label": "موتور",   "emoji": "⚙️", "cost_mult": 1.00, "desc": "قدرت و سرعت ماشین"},
    "tires":        {"label": "لاستیک",  "emoji": "🛞", "cost_mult": 0.80, "desc": "سرعت در دنده‌های سبک"},
    "transmission": {"label": "گیربکس",  "emoji": "🔧", "cost_mult": 0.90, "desc": "محدودهٔ سبز بزرگ‌تر"},
    "exhaust":      {"label": "اگزوز",   "emoji": "💨", "cost_mult": 0.70, "desc": "قدرت تعویض عالی"},
    "aero":         {"label": "آیرو",    "emoji": "🌪️", "cost_mult": 0.70, "desc": "سرعت در دنده‌های سنگین"},
    "ecu":          {"label": "ECU",     "emoji": "🧠", "cost_mult": 0.80, "desc": "جریمهٔ کمتر + اثر طولانی‌تر"},
}
UPGRADE_KEYS = list(UPGRADES)

# هزینهٔ ارتقای موتور از سطح ۱ تا ۱۰ (نمونهٔ سند)
_BASE_COSTS = [100, 150, 225, 340, 510, 765, 1150, 1725, 2600]
# بعد از سطح ۱۰ رشد هزینه کم‌کم آرام می‌شود (ولی همچنان شدید است)
COST_GROWTH_START = 1.35
COST_GROWTH_MIN = 1.05
COST_GROWTH_DECAY = 0.85


@lru_cache(maxsize=None)
def _base_cost(level: int) -> float:
    """هزینهٔ خام ارتقا از سطح `level` به `level+1` (برای موتور)."""
    if level <= len(_BASE_COSTS):
        return float(_BASE_COSTS[level - 1])
    prev_level = level - 1
    ratio = COST_GROWTH_MIN + (COST_GROWTH_START - COST_GROWTH_MIN) * COST_GROWTH_DECAY ** (prev_level - len(_BASE_COSTS))
    return _base_cost(prev_level) * ratio


def upgrade_cost(key: str, level: int) -> int:
    """هزینهٔ ارتقا از سطح فعلی `level` به سطح بعد."""
    raw = _base_cost(level) * UPGRADES[key]["cost_mult"]
    return max(5, int(round(raw / 5.0)) * 5)


# ─────────────────────────── اثر آپگریدها ───────────────────────────
def engine_multiplier(level: int) -> float:
    """ضریب خام موتور (طبق نمونهٔ سند: ۱٫۰۴ / ۱٫۰۸ / ۱٫۱۷ / ≈۱٫۴۲ / ≈۲٫۰۰)."""
    x = level - 1
    return 1.0 + 0.04 * x + 0.0006 * x * x


ENGINE_RACE_WEIGHT = 0.15            # سهم اثر موتور در مسابقه (برای اینکه بازی Pay-to-Win/غیرقابل رقابت نشود)
TIRES_BONUS_PER_LEVEL = 0.0015       # سرعت دنده‌های سبک
AERO_BONUS_PER_LEVEL = 0.0020        # سرعت دنده‌های سنگین
TRANS_SPEED_BONUS_PER_LEVEL = 0.0010 # شتاب کلی
TRANS_WINDOW_BONUS_PER_LEVEL = 0.008 # بزرگ‌تر شدن محدودهٔ سبز
EXHAUST_BOOST_BONUS_PER_LEVEL = 0.02 # قوی‌تر شدن Boost بعد از Perfect Shift
ECU_PENALTY_CUT_PER_LEVEL = 0.015    # کم شدن جریمهٔ Missed Shift
ECU_BOOST_TIME_BONUS_PER_LEVEL = 0.01  # طولانی‌تر شدن اثر Perfect Shift


def _validate() -> None:
    for name in ("GEAR_TOP_SPEED", "GEAR_RPM_RATE", "GREEN_START_RPM"):
        assert len(globals()[name]) == GEAR_COUNT, f"{name} باید {GEAR_COUNT} عدد داشته باشد"
    for n in range(MIN_PLAYERS, MAX_PLAYERS + 1):
        assert len(REWARDS.get(n, [])) == n, f"جدول جایزه برای {n} بازیکن ناقص است"
    assert 0.0 <= MISS_PENALTY <= 0.04


_validate()
