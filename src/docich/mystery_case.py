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

Deducibility (#2757) is a structural guarantee, not a hope:

* every room holds one clue and every suspect gives one alibi, and the
  corner publishes all of them before the prediction opens;
* every innocent suspect spent the murder hour with at least one other
  suspect, and the room clue for that group says the same thing — so each
  innocent alibi is corroborated by two independent statements;
* the culprit claims to have been alone in one room, while a clue in a
  *different* room places the culprit there at the murder hour, together
  with the method's evidence. That is the single contradiction in the case.
* :func:`contradicted_suspects` / :func:`solve` expose exactly that
  derivation from published material only, so tests can sweep seeds and
  assert the answer is unique.

All strings are bounded (view payload / poll titles / spoken text) and the
module is pure: no I/O, no clock, no environment.
"""
from __future__ import annotations

import hashlib
import random
import secrets

#: Bumped to 2 for the #2757 deducible-case redesign: the generated case
#: (texts, alibi shape, clue roles) changed, so a seed now means a different
#: case than under v1. A corner session records the version it started with
#: and fails closed instead of silently re-reading the seed under new rules.
VERSION = 2
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

#: The personal trace each suspect leaves behind. It is the identity marker
#: the audience matches against the alibi: role → bounded phrase. Every
#: suspect in the pool has one, so a clue can always name its owner without
#: inventing a new vocabulary at generation time.
TRACE_BY_ROLE = {
    "執事": "白手袋の繊維",
    "料理人": "小麦粉のついた足跡",
    "使用人": "上履きと同じ大きさの泥の跡",
    "客": "外靴の乾いた泥",
    "医者": "消毒液の匂いのする指紋",
    "運転手": "革手袋の擦り跡",
    "家政婦": "洗剤の匂いのする布切れ",
    "庭師": "土のついた軍手の繊維",
    "弁護士": "インクの染みた袖口の繊維",
    "秘書": "細いヒールの跡",
}

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

# (name, how the culprit did it, what the scene keeps as proof)
METHODS = (
    ("毒入りの杯", "杯に毒を盛って{victim}に差し出した", "飲みかけの杯の底に残った白い粉"),
    ("凍りついた賞状の筒", "冷凍庫で固めた賞状の筒を振り下ろした", "筒から滴った水滴と冷凍庫の扉の手形"),
    ("仕掛けのある燭台", "燭台の台座に細工をして倒れるように仕掛けた", "台座に巻かれた細い針金と倒れた燭台"),
    ("投げ込まれた古時計の錘", "古時計の錘を窓から投げ入れた", "窓枠の擦り跡と散らばったガラス"),
    ("扉に仕掛けた細工の梁", "扉の上に梁を仕掛けて倒れるようにした", "扉の上框に残った梁の圧痕"),
)

# (name, why the culprit did it, what survives as proof of the motive)
MOTIVES = (
    ("消された古い借金の証文", "{victim}に借金の証文を焼かれ、返済の当てを断たれた",
     "焼け残った借用書の端"),
    ("封を切られた手紙の差出人", "{victim}が手紙の差出人を公にし、隠していた素性が暴かれそうになった",
     "封を切られた手紙の切れ端"),
    ("替えられていた遺言書", "{victim}が遺言書を書き替え、{culprit}への相続分を消した",
     "書き替える前の遺言書の下書き"),
    ("盗まれた設計図の件", "{culprit}の設計図を{victim}が無断で売り、手柄を横取りした",
     "設計図の写しと売買の覚書"),
    ("焼かれた日記の内容", "{victim}が日記に{culprit}の弱みを書き、それを盾に脅していた",
     "焼け残った日記のページ"),
)

# --- Clue templates ---------------------------------------------------------
#
# Three clue roles, each a fixed sentence shape:
#   contradiction : places the culprit in a room at murder time (breaks the
#                   culprit's "I was alone elsewhere" claim)
#   corroboration : places a group of innocents together in the room they all
#                   claim (confirms their alibis, no contradiction)
#   motive        : the surviving motive evidence (a time-less fact, so it
#                   never counts as a placement)
#   flavour       : bounded scene dressing for a room no group used
#
# {room}/{time}/{suspect}/{trace}/{names}/{evidence} are substituted.

CONTRADICTION_CLUES = (
    "「{room}の床に、{evidence}。そばに{suspect}の{trace}が{time}のぶんだけ新しいまま残っていた」",
    "「{room}の棚の影に、{evidence}と{suspect}の{trace}。どちらも{time}のものだ」",
    "「{room}の窓辺に、{evidence}。そして{suspect}の{trace}が{time}の時刻で止まっていた」",
)
CORROBORATION_CLUES = (
    "「{room}のテーブルに、{names}の名が並んだ得点表。{time}の書き込みで止まっている」",
    "「{room}の戸棚に、{names}の湯呑みが{time}の茶殻ごと並んでいた」",
    "「{room}のカーペットに、{names}の靴跡が{time}のぶんだけ並んでいた」",
)
#: A lone innocent needs a non-testimonial alibi: material proof that they
#: were in the room they claim, at the murder hour. It keeps "was alone" from
#: being a giveaway — the audience must compare placement with the claim.
SOLO_CORROBORATION_CLUES = (
    "「{room}の卓上に、{name}あての{time}の配達控えと{trace}」",
    "「{room}の電話の記録に、{time}の{name}からの発信が残っていた」",
    "「{room}の窓に、{name}の{trace}と、内側から{time}に閉められた鍵」",
)
MOTIVE_CLUES = (
    "「{room}の暖炉の灰から、{evidence}が見つかった。{suspect}の名が記されている」",
    "「{room}の机の引き出しの奥に、{evidence}。{suspect}の署名入りだった」",
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

    @property
    def trace(self) -> str:
        """The identity marker this suspect leaves at a scene."""
        return TRACE_BY_ROLE.get(self.role, "小さな足跡")

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
    """One room clue. ``kind``/``suspects`` are engine truth, never broadcast."""

    __slots__ = ("room_id", "text", "key", "kind", "suspects")

    def __init__(self, room_id, text, *, key, kind="flavour", suspects=()):
        self.room_id = _short(room_id, 32, "clue room")
        self.text = _short(text, 120, "clue text")
        self.key = bool(key)
        self.kind = _short(kind, 32, "clue kind")
        self.suspects = tuple(int(index) for index in suspects)

    def as_dict(self):
        return {"room_id": self.room_id, "text": self.text, "key": self.key}


class Case:
    """The full (truth-bearing) case. Never serialized to the broadcast."""

    __slots__ = (
        "version", "seed", "commitment", "victim", "place", "time_label",
        "suspects", "rooms", "culprit_index", "method", "motive", "clues",
        "alibis", "intro", "method_how", "method_evidence", "motive_reason",
        "motive_evidence", "culprit_alibi_room_id", "culprit_evidence_room_id",
        "motive_room_id",
    )

    def __init__(self, *, version, seed, victim, place, time_label, suspects,
                 rooms, culprit_index, method, motive, clues, alibis, intro,
                 method_how, method_evidence, motive_reason, motive_evidence,
                 culprit_alibi_room_id, culprit_evidence_room_id, motive_room_id):
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
        self.method_how = method_how
        self.method_evidence = method_evidence
        self.motive_reason = motive_reason
        self.motive_evidence = motive_evidence
        self.culprit_alibi_room_id = culprit_alibi_room_id
        self.culprit_evidence_room_id = culprit_evidence_room_id
        self.motive_room_id = motive_room_id

    @property
    def culprit(self) -> Suspect:
        return self.suspects[self.culprit_index]

    def headline(self) -> str:
        return f"{self.place}で{self.victim}が殺された"

    @property
    def motive_room(self) -> str:
        return room_by_id(self, self.motive_room_id).name

    def alibi_room_id(self, index) -> str:
        return self.alibis[index][0].id

    def alibi_of(self, index) -> str:
        """The bounded spoken alibi for one suspect, or a fixed unknown."""
        value = self.alibis[index]
        if value is None:
            return "アリバイは誰も証明できなかった"
        room, partners = value
        names = [partner.name for partner in partners[:2]]
        extra = len(partners) - len(names)
        if not names:
            return (f"{_time_phrase(self.time_label)}、{room.name}に"
                    f"一人だったと証言")
        joined = "、".join(names)
        if extra > 0:
            joined = f"{joined}ほか{extra}人"
        return (f"{_time_phrase(self.time_label)}、{room.name}で"
                f"{joined}と一緒だったと証言")


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
    culprit = suspects[culprit_index]

    room_rows = rng.sample(ROOM_POOL, rng.randint(MIN_ROOMS, MAX_ROOMS))
    rooms = [Room(id_, name) for id_, name in room_rows]

    # The culprit's claimed alibi room and the room that contradicts it are
    # always distinct: that pair *is* the deduction (#2757).
    alibi_index, evidence_index = rng.sample(range(len(rooms)), 2)
    alibi_room, evidence_room = rooms[alibi_index], rooms[evidence_index]

    method_name, method_how_raw, method_evidence = METHODS[rng.randrange(len(METHODS))]
    motive_name, motive_reason_raw, motive_evidence = MOTIVES[rng.randrange(len(MOTIVES))]
    method_how = _short(method_how_raw.format(victim=victim), 120, "method how")
    motive_reason = _short(
        motive_reason_raw.format(victim=victim, culprit=culprit.name), 120,
        "motive reason")

    free_rooms = [room for room in rooms if room.id not in
                  (alibi_room.id, evidence_room.id)]
    groups = _innocent_groups(rng, suspects, culprit_index, free_rooms)
    alibis = _build_alibis(suspects, culprit_index, alibi_room, groups)
    clues = _build_clues(rng, rooms, suspects, culprit_index, evidence_room,
                         alibi_room, groups, method_evidence, motive_evidence,
                         time_label)
    intro = rng.sample(INTRO_LINES, 2)

    case = Case(
        version=VERSION, seed=seed, victim=victim, place=place,
        time_label=time_label, suspects=suspects, rooms=rooms,
        culprit_index=culprit_index, method=method_name, motive=motive_name,
        clues=clues, alibis=alibis, intro=intro, method_how=method_how,
        method_evidence=_short(method_evidence, 60, "method evidence"),
        motive_reason=motive_reason,
        motive_evidence=_short(motive_evidence, 60, "motive evidence"),
        culprit_alibi_room_id=alibi_room.id,
        culprit_evidence_room_id=evidence_room.id,
        motive_room_id=alibi_room.id,
    )
    # Fail closed: generation must never emit an unsolvable or ambiguous case.
    derived = contradicted_suspects(case)
    if derived != (culprit_index,):
        raise CaseError("generated case is not uniquely deducible")
    return case


def _innocent_groups(rng, suspects, culprit_index, free_rooms):
    """Split the innocents into corroborated alibi groups.

    A group of 2+ shares one room for the murder hour and names each other, so
    every member's alibi is confirmed by another suspect. A group of one gets
    material corroboration instead (see ``SOLO_CORROBORATION_CLUES``). Groups
    never land in the culprit's claimed room or in the contradiction room.
    """
    members = [index for index in range(len(suspects)) if index != culprit_index]
    rng.shuffle(members)
    if not members:
        return []
    if len(free_rooms) >= 2 and len(members) >= 3:
        if len(members) == 4 and rng.random() < 0.5:
            return [(free_rooms[0], members[:2]), (free_rooms[1], members[2:])]
        return [(free_rooms[0], members[:1]), (free_rooms[1], members[1:])]
    return [(free_rooms[0], members)]


def _build_alibis(suspects, culprit_index, alibi_room, groups):
    """Culprit: alone in the claimed room. Innocents: with their group."""
    alibis = {culprit_index: (alibi_room, ())}
    for room, members in groups:
        for index in members:
            partners = tuple(suspects[other] for other in members if other != index)
            alibis[index] = (room, partners)
    return alibis


def _build_clues(rng, rooms, suspects, culprit_index, evidence_room, alibi_room,
                 groups, method_evidence, motive_evidence, time_label):
    """One clue per room: the contradiction, the corroborations, the motive."""
    culprit = suspects[culprit_index]
    by_room = {}
    by_room[evidence_room.id] = Clue(
        evidence_room.id,
        _short(rng.choice(CONTRADICTION_CLUES).format(
            room=evidence_room.name, evidence=method_evidence,
            suspect=culprit.name, trace=culprit.trace, time=time_label),
            120, "clue text"),
        key=True, kind="contradiction", suspects=(culprit_index,))
    by_room[alibi_room.id] = Clue(
        alibi_room.id,
        _short(rng.choice(MOTIVE_CLUES).format(
            room=alibi_room.name, evidence=motive_evidence, suspect=culprit.name),
            120, "clue text"),
        key=False, kind="motive", suspects=(culprit_index,))
    for room, members in groups:
        if len(members) == 1:
            index = members[0]
            by_room[room.id] = Clue(
                room.id,
                _short(rng.choice(SOLO_CORROBORATION_CLUES).format(
                    room=room.name, name=suspects[index].name,
                    trace=suspects[index].trace, time=time_label),
                    120, "clue text"),
                key=False, kind="corroboration", suspects=(index,))
            continue
        names = "、".join(suspects[index].name for index in members)
        by_room[room.id] = Clue(
            room.id,
            _short(rng.choice(CORROBORATION_CLUES).format(
                room=room.name, names=names, time=time_label),
                120, "clue text"),
            key=False, kind="corroboration", suspects=tuple(members))
    clues = []
    for room in rooms:
        clue = by_room.get(room.id)
        if clue is None:
            clue = Clue(
                room.id,
                _short(rng.choice(FLAVOUR_CLUES).format(room=room.name),
                      120, "clue text"),
                key=False, kind="flavour")
        clues.append(clue)
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


def placements(case: Case) -> dict:
    """``suspect index -> room ids`` where a time-anchored clue places them.

    Only the contradiction and corroboration clues anchor a suspect to a room
    at the murder hour; the motive clue is a time-less fact and is therefore
    excluded (a suspect may have been in that room earlier).
    """
    found = {index: [] for index in range(len(case.suspects))}
    for clue in case.clues:
        if clue.kind not in ("contradiction", "corroboration"):
            continue
        for index in clue.suspects:
            if index in found and clue.room_id not in found[index]:
                found[index].append(clue.room_id)
    return {index: tuple(room_ids) for index, room_ids in found.items()}


def contradicted_suspects(case: Case) -> tuple:
    """Suspects whose published clue contradicts their own alibi.

    This is the whole deduction, computed from published material only: no
    engine truth beyond "which clue names whom" is used. A well-formed case
    yields exactly one entry, the culprit (:func:`solve`).
    """
    placed = placements(case)
    found = []
    for index in range(len(case.suspects)):
        alibi_room = case.alibi_room_id(index)
        if any(room_id != alibi_room for room_id in placed.get(index, ())):
            found.append(index)
    return tuple(found)


def solve(case: Case) -> int | None:
    """The culprit index derivable from the published case, or ``None``."""
    found = contradicted_suspects(case)
    return found[0] if len(found) == 1 else None


def poll_actions(case: Case, phase: str, exclude=()) -> tuple[tuple[str, str], ...]:
    """Bounded poll choices for one phase as ``(value, label)`` pairs.

    ``investigate`` offers the searchable rooms; ``interrogate`` offers the
    suspects. ``exclude`` drops values already covered by an earlier round.
    Both respect the Helix 2..5 choice limit by construction (MIN/MAX_SUSPECTS
    and MIN/MAX_ROOMS) whenever ``exclude`` is empty.
    """
    skip = {str(value) for value in exclude}
    if phase == "investigate":
        return tuple((room.id, room.name) for room in case.rooms
                     if room.id not in skip)
    if phase == "interrogate":
        return tuple(
            (str(index), f"{s.name}（{s.role}）")
            for index, s in enumerate(case.suspects)
            if str(index) not in skip
        )
    raise CaseError("unknown poll phase")


def poll_question(case: Case, phase: str) -> str:
    if phase == "investigate":
        return "次に調べる場所は？"
    if phase == "interrogate":
        return "次に尋問するのは？"
    raise CaseError("unknown poll phase")


def ai_detective_choice(case: Case, phase: str, rng: random.Random,
                        exclude=()) -> str:
    """The solo-mode action: a seeded pick the AI detective "investigates".

    Part of the published engine so solo runs are verifiable from the seed
    too. It never looks at the solution directly beyond what a viewer could
    derive, and it is only used when no live poll input exists.
    """
    actions = poll_actions(case, phase, exclude)
    if not actions:
        raise CaseError("no actions for phase")
    return actions[rng.randrange(len(actions))][0]


def timeline(case: Case) -> list:
    """The murder narrative, in order, from engine-owned facts only."""
    evidence_room = room_by_id(case, case.culprit_evidence_room_id)
    alibi_room = room_by_id(case, case.culprit_alibi_room_id)
    culprit, victim = case.culprit, case.victim
    return [
        f"{case.time_label}の少し前、{culprit.name}は{victim}を"
        f"{evidence_room.name}へ呼び出した。",
        f"{case.time_label}、{evidence_room.name}で{culprit.name}は"
        f"{case.method_how}。",
        f"その後{culprit.name}は{alibi_room.name}へ移り、「{case.time_label}は"
        f"{alibi_room.name}に一人だった」と証言した。",
        f"やがて{victim}が{evidence_room.name}で見つかり、そばには"
        f"{case.method_evidence}と{culprit.name}の{culprit.trace}が残っていた。",
    ]


def contradiction_text(case: Case) -> str:
    """Why the culprit's alibi fails, stated with the published clue."""
    evidence_room = room_by_id(case, case.culprit_evidence_room_id)
    alibi_room = room_by_id(case, case.culprit_alibi_room_id)
    culprit = case.culprit
    return (f"{culprit.name}は「{case.time_label}は{alibi_room.name}に一人」と"
            f"証言したが、{evidence_room.name}の手がかりは{case.time_label}の"
            f"もので、{culprit.name}の{culprit.trace}が新しいまま残っていた。"
            f"この二つは両立しない。")


def exclusion_texts(case: Case) -> list:
    """Why every innocent suspect is cleared (one sentence per suspect)."""
    out = []
    for index, suspect in enumerate(case.suspects):
        if index == case.culprit_index:
            continue
        room, partners = case.alibis[index]
        if partners:
            names = "、".join(partner.name for partner in partners)
            support = (f"{room.name}で{names}と一緒だったと互いの証言が一致し、"
                       f"{room.name}の手がかりとも食い違わない。")
        else:
            support = (f"{room.name}にいたことは{room.name}の手がかりが"
                       f"{case.time_label}の時刻で裏づけており、"
                       f"事件の現場とは重ならない。")
        out.append(f"{suspect.name}は{case.time_label}、{support}")
    return out


def reveal(case: Case) -> dict:
    """The end-of-case publication: solution plus the full explanation."""
    return {
        "version": case.version,
        "seed": case.seed,
        "commitment": case.commitment,
        "culprit": case.culprit.as_dict(),
        "method": case.method,
        "motive": case.motive,
        "timeline": timeline(case),
        "method_how": case.method_how,
        "method_evidence": case.method_evidence,
        "motive_reason": case.motive_reason,
        "motive_evidence": case.motive_evidence,
        "motive_room": room_by_id(case, case.motive_room_id).name,
        "contradiction": contradiction_text(case),
        "exclusions": exclusion_texts(case),
    }


#: Every key a published reveal must carry; ``verify_reveal`` compares them all.
REVEAL_KEYS = (
    "version", "seed", "commitment", "culprit", "method", "motive", "timeline",
    "method_how", "method_evidence", "motive_reason", "motive_evidence",
    "motive_room", "contradiction", "exclusions",
)


def verify_reveal(revealed: dict, case: Case) -> bool:
    """Fail-closed check that a published reveal matches this case."""
    if not isinstance(revealed, dict):
        return False
    expected = reveal(case)
    return all(revealed.get(key) == expected[key] for key in REVEAL_KEYS)


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
            entry["alibi_room"] = case.alibis[index][0].name
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
