"""لایهٔ دیتابیس (SQLite). هیچ منطق بازی یا تلگرامی اینجا نیست."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

import config as C

if TYPE_CHECKING:
    from engine import Race

log = logging.getLogger(__name__)

_LEVEL_COLS = ", ".join(f"{k} INTEGER NOT NULL DEFAULT {C.START_LEVEL}" for k in C.UPGRADE_KEYS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS players (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    coins         INTEGER NOT NULL DEFAULT 0,
    {_LEVEL_COLS},
    total_races   INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    podiums       INTEGER NOT NULL DEFAULT 0,
    best_position INTEGER,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS races (
    race_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    host_id     INTEGER NOT NULL,
    max_players INTEGER NOT NULL,
    distance    INTEGER NOT NULL,
    status      TEXT NOT NULL,
    message_id  INTEGER,
    created_at  INTEGER NOT NULL,
    started_at  INTEGER,
    finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS race_players (
    race_id         INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    join_order      INTEGER NOT NULL,
    progress        REAL    NOT NULL DEFAULT 0,
    gear            INTEGER NOT NULL DEFAULT 1,
    speed           REAL    NOT NULL DEFAULT 0,
    rpm             REAL    NOT NULL DEFAULT 0,
    perfect_shifts  INTEGER NOT NULL DEFAULT 0,
    missed_shifts   INTEGER NOT NULL DEFAULT 0,
    position        INTEGER,
    finish_position INTEGER,
    PRIMARY KEY (race_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_races_status ON races(status);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        log.info("database ready: %s", path)

    # ── بازیکن ──
    def upsert_player(self, user_id: int, username: str | None, first_name: str | None) -> bool:
        """بازیکن را ثبت/به‌روز می‌کند. اگر بازیکن جدید باشد True برمی‌گرداند."""
        exists = self.conn.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone()
        if exists:
            self.conn.execute("UPDATE players SET username=?, first_name=? WHERE user_id=?",
                              (username, first_name, user_id))
            return False
        self.conn.execute(
            "INSERT INTO players (user_id, username, first_name, created_at) VALUES (?,?,?,?)",
            (user_id, username, first_name, int(time.time())),
        )
        return True

    def get_player(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_levels(self, user_id: int) -> dict[str, int]:
        p = self.get_player(user_id)
        return {k: (p[k] if p else C.START_LEVEL) for k in C.UPGRADE_KEYS}

    def buy_upgrade(self, user_id: int, key: str) -> tuple[str, int]:
        """('ok'|'poor'|'max'|'none', عدد مرتبط). عدد: هزینه یا سطح جدید."""
        if key not in C.UPGRADES:
            return "none", 0
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(f"SELECT coins, {key} AS lvl FROM players WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                self.conn.execute("ROLLBACK")
                return "none", 0
            level, coins = row["lvl"], row["coins"]
            if level >= C.MAX_LEVEL:
                self.conn.execute("ROLLBACK")
                return "max", level
            cost = C.upgrade_cost(key, level)
            if coins < cost:
                self.conn.execute("ROLLBACK")
                return "poor", cost
            self.conn.execute(f"UPDATE players SET coins=coins-?, {key}={key}+1 WHERE user_id=?", (cost, user_id))
            self.conn.execute("COMMIT")
            return "ok", level + 1
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    # ── مسابقه ──
    def create_race(self, chat_id: int, host_id: int, max_players: int, distance: int, message_id: int | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO races (chat_id, host_id, max_players, distance, status, message_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, host_id, max_players, distance, "lobby", message_id, int(time.time())),
        )
        return int(cur.lastrowid)

    def set_race_status(self, race_id: int, status: str, max_players: int | None = None) -> None:
        now = int(time.time())
        sets, args = ["status=?"], [status]
        if status == "running":
            sets.append("started_at=?"); args.append(now)
        if status in ("finished", "cancelled", "aborted"):
            sets.append("finished_at=?"); args.append(now)
        if max_players is not None:
            sets.append("max_players=?"); args.append(max_players)
        args.append(race_id)
        self.conn.execute(f"UPDATE races SET {', '.join(sets)} WHERE race_id=?", args)

    def sync_players(self, race: "Race") -> None:
        """لیست بازیکنان لابی را در دیتابیس همگام می‌کند."""
        self.conn.execute("BEGIN")
        self.conn.execute("DELETE FROM race_players WHERE race_id=?", (race.race_id,))
        self.conn.executemany(
            "INSERT INTO race_players (race_id, user_id, join_order) VALUES (?,?,?)",
            [(race.race_id, p.user_id, p.slot) for p in race.players.values()],
        )
        self.conn.execute("UPDATE races SET max_players=? WHERE race_id=?", (race.max_players, race.race_id))
        self.conn.execute("COMMIT")

    def save_state(self, race: "Race", now: float) -> None:
        """وضعیت لحظه‌ای مسابقه (پیشروی، دنده، سرعت، RPM، ...) را ذخیره می‌کند."""
        rows = [
            (p.distance, p.gear, p.speed(now), p.rpm, p.perfect_shifts, p.missed_shifts,
             p.position, p.finish_position, race.race_id, p.user_id)
            for p in race.players.values()
        ]
        self.conn.execute("BEGIN")
        self.conn.executemany(
            "UPDATE race_players SET progress=?, gear=?, speed=?, rpm=?, perfect_shifts=?, missed_shifts=?, "
            "position=?, finish_position=? WHERE race_id=? AND user_id=?", rows)
        self.conn.execute("COMMIT")

    def finish_race(self, race: "Race", rewards: dict[int, int], now: float) -> None:
        """ذخیرهٔ نتیجهٔ نهایی + پرداخت Coin + به‌روزرسانی آمار بازیکنان (اتمیک)."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for p in race.players.values():
                pos = p.finish_position or len(race.players)
                self.conn.execute(
                    "UPDATE players SET coins=coins+?, total_races=total_races+1, wins=wins+?, podiums=podiums+?, "
                    "best_position=MIN(COALESCE(best_position, 999), ?) WHERE user_id=?",
                    (rewards.get(p.user_id, 0), 1 if pos == 1 else 0, 1 if pos <= 3 else 0, pos, p.user_id),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.save_state(race, now)
        self.set_race_status(race.race_id, "finished")

    def abort_unfinished(self) -> int:
        """مسابقه‌های نیمه‌کارهٔ قبل از ری‌استارت ربات را بسته می‌کند."""
        cur = self.conn.execute(
            "UPDATE races SET status='aborted', finished_at=? WHERE status IN ('lobby','countdown','running')",
            (int(time.time()),),
        )
        return cur.rowcount
