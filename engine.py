"""موتور بازی (Game Engine) — کاملاً مستقل از تلگرام.

جریان: Race (وضعیت مسابقه) ← هندلرهای تلگرام ← رندر UI
هیچ import از تلگرام اینجا نیست؛ به همین خاطر بالانس بازی را می‌شود بدون دست زدن به ربات تست و تغییر داد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import config as C

log = logging.getLogger(__name__)


class Phase(str, Enum):
    LOBBY = "lobby"
    COUNTDOWN = "countdown"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class ShiftResult(str, Enum):
    PERFECT = "perfect"
    LATE = "late"
    EARLY = "early"
    TOP_GEAR = "top_gear"
    INACTIVE = "inactive"


class EventKind(str, Enum):
    GO = "go"
    PERFECT = "perfect"
    MISSED = "missed"
    OVERTAKE = "overtake"
    FINISH = "finish"


@dataclass
class Event:
    kind: EventKind
    time: float
    player_id: int = 0
    other_id: int = 0
    value: int = 0


# ─────────────────────────── ماشین ───────────────────────────
@dataclass(frozen=True)
class CarStats:
    """ضریب‌های نهایی ماشین که از سطح آپگریدها محاسبه می‌شوند."""

    power_low: float = 1.0        # ضریب سرعت در دنده‌های سبک
    power_high: float = 1.0       # ضریب سرعت در دنده‌های سنگین
    green_factor: float = 1.0     # ضریب اندازهٔ محدودهٔ سبز
    boost: float = C.PERFECT_BOOST
    boost_seconds: float = C.PERFECT_BOOST_SECONDS
    penalty: float = C.MISS_PENALTY

    @classmethod
    def from_levels(cls, levels: dict[str, int]) -> "CarStats":
        lv = {k: levels.get(k, C.START_LEVEL) - 1 for k in C.UPGRADE_KEYS}
        engine = 1.0 + (C.engine_multiplier(lv["engine"] + 1) - 1.0) * C.ENGINE_RACE_WEIGHT
        trans_speed = 1.0 + C.TRANS_SPEED_BONUS_PER_LEVEL * lv["transmission"]
        tires = 1.0 + C.TIRES_BONUS_PER_LEVEL * lv["tires"]
        aero = 1.0 + C.AERO_BONUS_PER_LEVEL * lv["aero"]
        return cls(
            power_low=engine * trans_speed * tires,
            power_high=engine * trans_speed * aero,
            green_factor=1.0 + C.TRANS_WINDOW_BONUS_PER_LEVEL * lv["transmission"],
            boost=C.PERFECT_BOOST * (1.0 + C.EXHAUST_BOOST_BONUS_PER_LEVEL * lv["exhaust"]),
            boost_seconds=C.PERFECT_BOOST_SECONDS * (1.0 + C.ECU_BOOST_TIME_BONUS_PER_LEVEL * lv["ecu"]),
            penalty=C.MISS_PENALTY * max(0.0, 1.0 - C.ECU_PENALTY_CUT_PER_LEVEL * lv["ecu"]),
        )


@dataclass
class Player:
    user_id: int
    name: str
    slot: int
    car: CarStats = field(default_factory=CarStats)

    gear: int = 1
    rpm: float = float(C.IDLE_RPM)
    distance: float = 0.0
    perfect_shifts: int = 0
    missed_shifts: int = 0
    boost_until: float = 0.0
    boost_value: float = 0.0
    green_end_time: float | None = None   # لحظه‌ای که RPM از محدودهٔ سبز خارج شد
    position: int = 0
    finish_position: int | None = None
    finish_time: float | None = None

    # ── محدودهٔ سبز دندهٔ فعلی: (شروع، پایان) یا None در دندهٔ آخر
    def window(self) -> tuple[float, float] | None:
        if self.gear >= C.GEAR_COUNT:
            return None
        start = C.GREEN_START_RPM[self.gear - 1]
        width = C.GEAR_RPM_RATE[self.gear - 1] * C.GREEN_SECONDS * self.car.green_factor
        return float(start), min(start + width, float(C.GREEN_MAX_END_RPM))

    def speed(self, now: float) -> float:
        base = self.rpm / C.REDLINE_RPM * C.GEAR_TOP_SPEED[self.gear - 1]
        power = self.car.power_low if self.gear <= C.LOW_GEAR_MAX else self.car.power_high
        boost = 1.0 + self.boost_value if now < self.boost_until else 1.0
        return base * power * boost

    def shift_state(self, now: float) -> str:
        """top | wait | green | late"""
        win = self.window()
        if win is None:
            return "top"
        start, end = win
        if self.rpm < start:
            return "wait"
        if self.rpm <= end:
            return "green"
        if self.green_end_time is not None and now - self.green_end_time <= C.PERFECT_GRACE:
            return "green"
        return "late"


# ─────────────────────────── مسابقه ───────────────────────────
class Race:
    def __init__(self, race_id: int, chat_id: int, host_id: int, max_players: int,
                 distance: int = C.RACE_DISTANCE) -> None:
        self.race_id = race_id
        self.chat_id = chat_id
        self.host_id = host_id
        self.max_players = max_players
        self.distance = distance
        self.phase = Phase.LOBBY
        self.confirming = False          # Host در حال تأیید شروع است
        self.message_id: int | None = None
        self.players: dict[int, Player] = {}
        self.finish_order: list[int] = []
        self.events: list[Event] = []
        self.start_time = 0.0
        self.sim_time = 0.0
        self._prev_pos: dict[int, int] = {}

    # ── لابی ──
    @property
    def is_full(self) -> bool:
        return len(self.players) >= self.max_players

    def add_player(self, user_id: int, name: str, levels: dict[str, int] | None = None) -> bool:
        if self.phase != Phase.LOBBY or user_id in self.players or self.is_full:
            return False
        car = CarStats.from_levels(levels or {})
        self.players[user_id] = Player(user_id, name, slot=len(self.players), car=car)
        return True

    def remove_player(self, user_id: int) -> bool:
        if self.phase != Phase.LOBBY or user_id not in self.players or user_id == self.host_id:
            return False
        del self.players[user_id]
        for i, p in enumerate(self.players.values()):
            p.slot = i
        return True

    def set_capacity(self, n: int) -> str | None:
        """None = موفق؛ در غیر این صورت کد خطا."""
        if self.phase != Phase.LOBBY:
            return "locked"
        if n < len(self.players):
            return "below_current"
        if not C.MIN_PLAYERS <= n <= C.MAX_PLAYERS:
            return "out_of_range"
        self.max_players = n
        return None

    # ── شروع ──
    def start(self, now: float) -> None:
        self.phase = Phase.RUNNING
        self.start_time = self.sim_time = now
        self.events.append(Event(EventKind.GO, now))
        self.update_positions(now)
        log.info("race %s started with %d players", self.race_id, len(self.players))

    def cancel(self) -> None:
        self.phase = Phase.CANCELLED

    # ── شبیه‌سازی ──
    def advance(self, now: float) -> None:
        """شبیه‌سازی را تا لحظهٔ `now` جلو می‌برد."""
        while self.phase == Phase.RUNNING and now - self.sim_time >= C.SIM_STEP - 1e-9:
            self.sim_time += C.SIM_STEP
            self._step(self.sim_time, C.SIM_STEP)
            self._check_end(self.sim_time)
        rest = now - self.sim_time
        if self.phase == Phase.RUNNING and rest > 0.01:
            self.sim_time = now
            self._step(now, rest)
            self._check_end(now)

    def _step(self, t: float, dt: float) -> None:
        crossed: list[tuple[float, int, Player]] = []
        for p in self.players.values():
            if p.finish_position is not None:
                continue
            self._tick_rpm(p, t, dt)
            step_m = p.speed(t) / 3.6 * C.DISTANCE_SCALE * dt
            if p.distance + step_m >= self.distance:
                frac = (self.distance - p.distance) / step_m if step_m > 0 else 1.0
                crossed.append((t - dt + frac * dt, p.slot, p))
                p.distance = float(self.distance)
            else:
                p.distance += step_m
        for cross_time, _, p in sorted(crossed, key=lambda c: (c[0], c[1])):
            self._finish(p, cross_time)

    def _tick_rpm(self, p: Player, t: float, dt: float) -> None:
        p.rpm = min(float(C.REDLINE_RPM), p.rpm + C.GEAR_RPM_RATE[p.gear - 1] * dt)
        win = p.window()
        if win is None:
            return
        if p.green_end_time is None and p.rpm > win[1]:
            p.green_end_time = t
        if p.green_end_time is not None and t - p.green_end_time >= C.MISS_TIMEOUT:
            self._shift(p, t, "missed")

    def _shift(self, p: Player, t: float, kind: str) -> None:
        ratio = C.GEAR_TOP_SPEED[p.gear - 1] / C.GEAR_TOP_SPEED[p.gear]
        rpm = p.rpm * ratio                      # سرعت پیوسته می‌ماند، RPM در دندهٔ جدید پایین می‌آید
        if kind == "perfect":
            rpm *= 1.0 + C.PERFECT_SPEED_BONUS
            p.boost_value, p.boost_until = p.car.boost, t + p.car.boost_seconds
            p.perfect_shifts += 1
            self.events.append(Event(EventKind.PERFECT, t, p.user_id))
        elif kind == "missed":
            rpm *= 1.0 - p.car.penalty
            p.missed_shifts += 1
            self.events.append(Event(EventKind.MISSED, t, p.user_id))
        p.rpm = max(float(C.IDLE_RPM), min(float(C.REDLINE_RPM), rpm))
        p.gear += 1
        p.green_end_time = None

    def press_shift(self, user_id: int, now: float) -> ShiftResult:
        """فشار دادن دکمهٔ تعویض دنده توسط بازیکن."""
        p = self.players.get(user_id)
        if self.phase != Phase.RUNNING or p is None or p.finish_position is not None:
            return ShiftResult.INACTIVE
        self.advance(now)
        if self.phase != Phase.RUNNING or p.finish_position is not None:
            return ShiftResult.INACTIVE
        if p.gear >= C.GEAR_COUNT:
            return ShiftResult.TOP_GEAR
        state = p.shift_state(now)
        if state == "wait":
            return ShiftResult.EARLY
        if state == "green":
            self._shift(p, now, "perfect")
            return ShiftResult.PERFECT
        self._shift(p, now, "late")
        return ShiftResult.LATE

    # ── پایان ──
    def _finish(self, p: Player, cross_time: float) -> None:
        p.finish_position = len(self.finish_order) + 1
        p.finish_time = cross_time - self.start_time
        self.finish_order.append(p.user_id)
        self.events.append(Event(EventKind.FINISH, cross_time, p.user_id, value=p.finish_position))

    def _check_end(self, t: float) -> None:
        left = [p for p in self.players.values() if p.finish_position is None]
        if not left:
            self.phase = Phase.FINISHED
            return
        timed_out = t - self.start_time >= C.RACE_TIMEOUT
        # وقتی فقط یک نفر مانده، رتبهٔ آخر را می‌گیرد و مسابقه فوراً تمام می‌شود
        if len(left) == 1 or timed_out:
            for p in sorted(left, key=lambda q: (-q.distance, q.slot)):
                p.finish_position = len(self.finish_order) + 1
                self.finish_order.append(p.user_id)
            self.phase = Phase.FINISHED
            log.info("race %s finished", self.race_id)

    # ── رتبه‌بندی و رویدادها ──
    def ranking(self) -> list[Player]:
        return sorted(
            self.players.values(),
            key=lambda p: (0, p.finish_position, 0) if p.finish_position is not None else (1, -p.distance, p.slot),
        )

    def update_positions(self, now: float) -> None:
        """رتبهٔ لحظه‌ای را به‌روز می‌کند و رویداد سبقت می‌سازد."""
        order = self.ranking()
        new_pos = {p.user_id: i + 1 for i, p in enumerate(order)}
        for p in order:
            old = self._prev_pos.get(p.user_id)
            new = new_pos[p.user_id]
            if old is not None and new < old and p.finish_position is None:
                passed = [q for q in order if self._prev_pos.get(q.user_id, 0) < old and new_pos[q.user_id] > new]
                if passed:
                    victim = max(passed, key=lambda q: self._prev_pos[q.user_id])
                    self.events.append(Event(EventKind.OVERTAKE, now, p.user_id, victim.user_id))
            p.position = new
        self._prev_pos = new_pos
        if len(self.events) > 60:
            self.events = self.events[-30:]

    def recent_events(self, now: float) -> list[Event]:
        fresh = [e for e in self.events if now - e.time <= C.EVENT_TTL]
        return fresh[-C.EVENT_LIMIT:]
