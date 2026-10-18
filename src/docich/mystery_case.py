"""Deterministic murder-mystery case engine for the participatory corner.

The engine is the single authority on the case truth. The corner loop, the
broadcast view and the LLM narration may display or dramatize what this
module returns, but can never change who the culprit is, which clue sits in
which room, or which alibi contradicts the evidence. Game state is never an
LLM output (the same trust boundary the #380 selfmade engine established).

Everything derives from one integer seed via the stdlib ``random.Random``, so
a reveal that publishes ``(engine VERSION, seed, commitment)`` lets anyone
recompute the whole case with the public code and verify that the final
answer was fixed before any poll or channel-point prediction started. The
commitment is ``sha256(COMMITMENT_PREFIX + ":" + seed)`` and is shown on the
broadcast board from the moment the case is generated.

All strings are bounded (view payload / poll titles / spoken text) and the
module is pure: no I/O, no clock, no environment.
"""
from __future__ import annotations

import hashlib
import random
import secrets

VERSION = 1
COMMITMENT_PREFIX = "docich-mystery-v1"

#: Poll choice limits (Twitch Helix: 2..5 choices, <=25 chars, title <=60).
MAX_SUSPECTS = 5
MIN_SUSPECTS = 4
MAX_ROOMS = 4
MIN_ROOMS = 3

# --- Fixed content pools. Short bounded Japanese strings only. -------------

VICTIMS = ("城主・ゴロー", "冒険家・ノック", "評論家・ペン太", "歌姫・ウタ子", "商人・ゼニ郎")
PLACES = ("霧がかった洋館", "灯台のうらの別荘", "古城ホテル", "山中のペンション", "湖上の遊覧船")
TIMES = ("夜半", "黄昏どき", "明け方", "深夜2時", "夕暮れ")

# (name, role) — names stay well under the 25-char choice limit.
SUSPECT_POOL = (
    ("クロやま", "執事"),
    ("ミケーネ", "料理人"),
    ("ヤマネコ", "使用人"),
    ("シャーロ", "客"),
    ("アリサ", "医者"),
    ("ゴン太", "運転手"),
    ("ツル子", "家政婦"),
    ("カゲロー", "庭師"),
    ("ワトすけ", "弁護士"),
    ("ニャンコ", "秘書"),
)

# (id, name) — searchable rooms; ids are stable engine vocabulary.
ROOM_POOL = (
    ("study", "書斎"),
    ("kitchen", "台所"),
    ("cellar", "地下酒蔵"),
    ("garden", "裏庭"),
    ("attic", "屋根裏"),
    ("hall", "応接間"),
    ("library", "書庫"),
)

METHODS = (
    "毒入りの杯",
    "凍りついた賞状の筒",
    "仕掛けのある燭台",
    "投げ込まれた古時計の錘",
    "扉に仕掛けた細工の梁",
)
MOTIVES = (
    "消された古い借金の証文",
    "封を切られた手紙の差出人",
    "替えられていた遺言書",
    "盗まれた設計図の件",
    "焼かれた日記の内容",
)

# Clue templates. {room} / {suspect} get substituted; every case shows
# exactly one room clue that contradicts the culprit's alibi, and the rest
# are bounded flavour or pointers that survive the reveal.
ROOM_CLUES = (
    "「{room}の床に、{suspect}の上履きと同じサイズの泥の跡」",
    "「{room}の灰皿に、{suspect}だけが吸う細めのタバコの吸殻」",
    "「{room}の棚の奥、{suspect}の手袋の片方」",
    "「{room}の窓枠に、{suspect}の着ている服と同じ繊維」",
)
FLAVOUR_CLUES = (
    "「{room}の隅に、飲みかけの紅茶のカップ」",
    "「{room}に、誰かが落とした懐中時計（10分前で止まっている）」",
    "「{room}の戸棚に、開かれたままの料理の本」",
    "「{room}のカーペットに、小さな虫の死骸と雨粒」",
)

INTRO_LINES = (
    "事件の発見者は使用人で、現場に第一発見者の足跡以外は無かったと証言している。",
    "同じ夜、屋敷のどこかで停電があり、家里は数分だけ暗かったという。",
    "被害者は当日、誰かと会う約束をしていたことが日記から分かっている。",
    "現場の扉は内側から施錠されておらず、誰でも出入りできた。",
)

# --- Dataclasses ------------------------------------------------------------


class CaseError(ValueError):
    """A fixed, non-sensitive case engine rejection."""


def _short(value, limit, what):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise CaseError(f"invalid {what}")
    return value


class Suspect:
    __slots__ = ("name", "role")

    def __init__(self, name, role):
        self.name = _short(name, 25, "suspect name")
        self.role = _short(role, 25, "suspect role")

    def as_dict(self):
        return {"name": self.name, "role": self.role}


class Room:
    __slots__ = ("id", "name")

    def __init__(self, id_, name):
        self.id = _short(id_, 32, "room id")
        self.name = _short(name, 25, "room name")

    def as_dict(self):
        return {"id": self.id, "name": self.name}


class Clue:
    __slots__ = ("room_id", "text", "key")

    def __init__(self, room_id, text, *, key):
        self.room_id = _short(room_id, 32, "clue room")
        self.text = _short(text, 120, "clue text")
        self.key = bool(key)

    def as_dict(self):
        return {"room_id": self.room_id, "text": self.text, "key": self.key}


class Case:
    """The full (truth-bearing) case. Never serialized to the broadcast."""

    __slots__ = (
        "version", "seed", "commitment", "victim", "place", "time_label",
        "suspects", "rooms", "culprit_index", "method", "motive", "clues",
        "alibis", "intro",
    )

    def __init__(self, *, version, seed, victim, place, time_label, suspects,
                 rooms, culprit_index, method, motive, clues, alibis, intro):
        self.version = version
        self.seed = seed
        self.commitment = commitment(version, seed)
        self.victim = victim
        self.place = place
        self.time_label = time_label
        self.suspects = suspects
        self.rooms = rooms
        self.culprit_index = culprit_index
        self.method = method
        self.motive = motive
        self.clues = clues
        self.alibis = alibis
        self.intro = intro

    @property
    def culprit(self) -> Suspect:
        return self.suspects[self.culprit_index]

    def headline(self) -> str:
        return f"{self.place}で{self.victim}が殺された"

    def alibi_of(self, index) -> str:
        """The bounded spoken alibi for one suspect, or a fixed unknown."""
        value = self.alibis[index]
        if value is None:
            return "アリバイは誰も証明できなかった"
        room, partner = value
        if partner is None:
            return f"{_time_phrase(self.time_label)}、{room.name}に一人だったと証言"
        return (f"{_time_phrase(self.time_label)}、{room.name}で"
                f"{partner.name}と一緒だったと証言")


def _time_phrase(label):
    return label


# --- Deterministic generation ----------------------------------------------


def commitment(version, seed) -> str:
    """The public pre-commitment over (engine version, seed)."""
    if type(version) is not int or version < 1:
        raise CaseError("invalid engine version")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise CaseError("invalid seed")
    return hashlib.sha256(f"{COMMITMENT_PREFIX}:{version}:{seed}".encode()).hexdigest()


def new_seed() -> int:
    """A fresh cryptographically random case seed."""
    return secrets.randbits(64)


def generate(seed: int) -> Case:
    """Derive the whole case from ``seed``. Same seed, same case, always."""
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise CaseError("invalid seed")
    rng = random.Random(seed)

    victim = VICTIMS[rng.randrange(len(VICTIMS))]
    place = PLACES[rng.randrange(len(PLACES))]
    time_label = TIMES[rng.randrange(len(TIMES))]

    suspect_rows = rng.sample(SUSPECT_POOL, rng.randint(MIN_SUSPECTS, MAX_SUSPECTS))
    suspects = [Suspect(name, role) for name, role in suspect_rows]
    culprit_index = rng.randrange(len(suspects))

    room_rows = rng.sample(ROOM_POOL, rng.randint(MIN_ROOMS, MAX_ROOMS))
    rooms = [Room(id_, name) for id_, name in room_rows]
    key_room = rooms[rng.randrange(len(rooms))]

    # The culprit claims an alibi in one (possibly key) room; the key room's
    # clue contradicts exactly that claim. Innocent suspects get alibis that
    # never contradict anything.
    culprit_room = rooms[rng.randrange(len(rooms))]
    partner = _alibi_partner(rng, suspects, culprit_index)
    method = METHODS[rng.randrange(len(METHODS))]
    motive = MOTIVES[rng.randrange(len(MOTIVES))]

    clues = _build_clues(rng, rooms, key_room, suspects, culprit_index)
    alibis = {
        index: _alibi_for(rng, rooms, suspects, index, culprit_index, culprit_room, partner)
        for index in range(len(suspects))
    }
    intro = rng.sample(INTRO_LINES, 2)
    return Case(
        version=VERSION, seed=seed, victim=victim, place=place, time_label=time_label,
        suspects=suspects, rooms=rooms, culprit_index=culprit_index,
        method=method, motive=motive, clues=clues, alibis=alibis, intro=intro,
    )


def _alibi_partner(rng, suspects, culprit_index):
    others = [s for i, s in enumerate(suspects) if i != culprit_index]
    return rng.choice(others) if others else None


def _alibi_for(rng, rooms, suspects, index, culprit_index, culprit_room, partner):
    if index == culprit_index:
        return (culprit_room, partner)
    room = rooms[rng.randrange(len(rooms))]
    if rng.random() < 0.4:
        return (room, None)
    others = [s for i, s in enumerate(suspects) if i != index and i != culprit_index]
    if not others:
        return (room, None)
    return (room, rng.choice(others))


def _build_clues(rng, rooms, key_room, suspects, culprit_index):
    """One key clue in the key room contradicts the culprit; the rest flavour."""
    culprit = suspects[culprit_index]
    key_text = _short(
        rng.choice(ROOM_CLUES).format(room=key_room.name, suspect=culprit.name),
        120, "clue text")
    clues = [Clue(key_room.id, key_text, key=True)]
    for room in rooms:
        if room.id == key_room.id:
            continue
        text = _short(
            rng.choice(FLAVOUR_CLUES).format(room=room.name), 120, "clue text")
        clues.append(Clue(room.id, text, key=False))
    return clues


# --- Public projection ------------------------------------------------------


def clue_for_room(case: Case, room_id: str) -> Clue | None:
    if not isinstance(room_id, str):
        raise CaseError("invalid room id")
    for clue in case.clues:
        if clue.room_id == room_id:
            return clue
    return None


def room_by_id(case: Case, room_id: str) -> Room | None:
    for room in case.rooms:
        if room.id == room_id:
            return room
    return None


def poll_actions(case: Case, phase: str) -> tuple[tuple[str, str], ...]:
    """Bounded poll choices for one phase as ``(value, label)`` pairs.

    ``investigate`` offers the searchable rooms; ``interrogate`` offers the
    suspects. Both respect the Helix 2..5 choice limit by construction
    (MIN/MAX_SUSPECTS and MIN/MAX_ROOMS).
    """
    if phase == "investigate":
        return tuple((room.id, room.name) for room in case.rooms)
    if phase == "interrogate":
        return tuple(
            (str(index), f"{s.name}（{s.role}）")
            for index, s in enumerate(case.suspects)
        )
    raise CaseError("unknown poll phase")


def poll_question(case: Case, phase: str) -> str:
    if phase == "investigate":
        return "次に調べる場所は？"
    if phase == "interrogate":
        return "次に尋問するのは？"
    raise CaseError("unknown poll phase")


def ai_detective_choice(case: Case, phase: str, rng: random.Random) -> str:
    """The solo-mode action: a seeded pick the AI detective "investigates".

    Part of the published engine so solo runs are verifiable from the seed
    too. It never looks at the solution directly beyond what a viewer could
    derive, and it is only used when no live poll input exists.
    """
    actions = poll_actions(case, phase)
    if not actions:
        raise CaseError("no actions for phase")
    return actions[rng.randrange(len(actions))][0]


def reveal(case: Case) -> dict:
    """The end-of-case publication: solution plus the verification facts."""
    return {
        "version": case.version,
        "seed": case.seed,
        "commitment": case.commitment,
        "culprit": case.culprit.as_dict(),
        "method": case.method,
        "motive": case.motive,
    }


def verify_reveal(revealed: dict, case: Case) -> bool:
    """Fail-closed check that a published reveal matches this case."""
    if not isinstance(revealed, dict):
        return False
    expected = reveal(case)
    return (
        revealed.get("version") == expected["version"]
        and revealed.get("seed") == expected["seed"]
        and revealed.get("commitment") == expected["commitment"]
        and revealed.get("culprit") == expected["culprit"]
        and revealed.get("method") == expected["method"]
        and revealed.get("motive") == expected["motive"]
    )


def public_board(case: Case, *, revealed_rooms: tuple[str, ...] = (),
                 revealed_suspects: tuple[int, ...] = (), revealed: dict | None = None) -> dict:
    """The bounded broadcast payload: everything except the hidden truth.

    The commitment is always included; the culprit, method and motive appear
    only through ``revealed`` (the end-of-case publication). Never include
    the seed or any per-suspect knowledge that pinpoints the culprit.
    """
    rooms_state = []
    for room in case.rooms:
        entry = {"id": room.id, "name": room.name, "searched": room.id in revealed_rooms}
        if room.id in revealed_rooms:
            clue = clue_for_room(case, room.id)
            entry["clue"] = clue.text if clue is not None else None
        rooms_state.append(entry)

    suspects_state = []
    for index, suspect in enumerate(case.suspects):
        entry = dict(suspect.as_dict())
        entry["index"] = index
        if index in revealed_suspects:
            entry["alibi"] = case.alibi_of(index)
        suspects_state.append(entry)

    board = {
        "schema": "mystery-case",
        "version": case.version,
        "commitment": case.commitment,
        "headline": case.headline(),
        "victim": case.victim,
        "place": case.place,
        "time": case.time_label,
        "intro": list(case.intro),
        "rooms": rooms_state,
        "suspects": suspects_state,
    }
    if revealed is not None:
        if not verify_reveal(revealed, case):
            raise CaseError("reveal does not match the case")
        board["reveal"] = dict(revealed)
    return board
