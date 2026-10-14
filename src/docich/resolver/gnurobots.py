"""GNU Robots resolver: a parameterized Scheme program (token-free).

GNU Robots is self-playing: the "brain" is a Scheme script that the game
executes.  The improvement loop (docich.resolver.improve) perturbs the
numeric weights, renders this template into a concrete ``resolver.scm``,
evaluates it with real headless matches, and promotes the best set.  The
live wrapper (``/usr/local/bin/gnurobots_docich``) re-reads the script at
every match start, so a promotion hot-swaps the running game.

Weights (all numeric, perturbable):

- ``food_urgency``: energy at/below which the robot prioritizes food over
  prizes.
- ``move_budget``: steps to spend walking toward a spotted item before
  giving up and re-seeking (bounds the per-target commitment).
- ``flee_retries``: random-turn attempts when a baddie blocks the way.
- ``wander_turn_one_in``: drunkard-walk turn probability (1 in N steps).
"""
from __future__ import annotations

import re

TEMPLATE = """\
;;; docich gnurobots resolver (rendered; DO NOT edit by hand)
;;; weights: food_urgency=@food_urgency@ move_budget=@move_budget@
;;;          flee_retries=@flee_retries@ wander_turn_one_in=@wander_turn_one_in@

(define food-threshold @food_urgency@)
(define move-budget @move_budget@)
(define flee-retries @flee_retries@)
(define wander-turn-one-in @wander_turn_one_in@)

(define (low-energy?) (< (robot-get-energy) food-threshold))

;; Turn away when something dangerous is immediately ahead.  Returns #f
;; when every attempt still faces a baddie.
(define (flee)
  (let loop ((n flee-retries))
    (cond ((<= n 0) #f)
          ((robot-feel "baddie")
           (begin
             (robot-turn (+ 1 (random 2)))
             (loop (- n 1))))
          (else #t))))

;; One exploration step: avoid baddies, move when the way is clear,
;; occasionally change direction while wandering, turn when blocked.
;; Returns #t when the robot changed cells (or turned).
(define (safe-step)
  (cond
    ((robot-feel "baddie")
     (begin (flee) #f))
    ((robot-move 1)
     (begin
       (if (= (random wander-turn-one-in) 0)
           (robot-turn (+ 1 (random 2))))
       #t))
    (else
     (begin
       (robot-turn (+ 1 (random 2)))
       #f))))

;; Walk to a seen item and take it.  Prizes are grabbed in front; food is
;; grabbed the same way (grabbing then stepping onto the cell).
(define (collect thing)
  (let loop ((n move-budget))
    (cond
      ((<= n 0) #f)
      ((robot-feel "baddie")
       (begin (flee) #f))
      ((robot-feel thing)
       (begin
         (robot-grab)
         (robot-move 1)
         #t))
      ((robot-move 1) (loop (- n 1)))
      (else
       (begin
         (robot-turn (+ 1 (random 2)))
         #f)))))

(let main-loop ()
  (let* ((primary (if (low-energy?) "food" "prize"))
         (secondary (if (low-energy?) "prize" "food")))
    (cond
      ((and (robot-look primary) (collect primary)))
      ((and (robot-look secondary) (collect secondary)))
      (else (safe-step))))
  (main-loop))
"""

_WEIGHT_KEYS = ("food_urgency", "move_budget", "flee_retries", "wander_turn_one_in")

DEFAULT_STRATEGY: dict[str, float] = {
    "food_urgency": 400.0,
    "move_budget": 12.0,
    "flee_retries": 4.0,
    "wander_turn_one_in": 4.0,
}

_PLACEHOLDER_RE = re.compile(r"@([a-z_]+)@")


def render(weights: dict) -> str:
    """Render the Scheme template with concrete weight values."""
    st = dict(DEFAULT_STRATEGY)
    if weights:
        st.update({k: weights[k] for k in st if k in weights})
    out = TEMPLATE
    for key in _WEIGHT_KEYS:
        out = out.replace(f"@{key}@", str(int(round(float(st[key])))))
    if _PLACEHOLDER_RE.search(out):
        raise ValueError("resolver template に未置換のweightがあります")
    return out


def parse_statistics(text: str) -> dict:
    """Parse the STATISTICS block printed at match end."""
    score = energy = shields = None
    for line in text.splitlines():
        m = re.search(r"^Score:\s*(\d+)", line.strip())
        if m:
            score = int(m.group(1))
        m = re.search(r"^Energy:\s*(\d+)", line.strip())
        if m:
            energy = int(m.group(1))
        m = re.search(r"^Shields:\s*(\d+)", line.strip())
        if m:
            shields = int(m.group(1))
    return {"score": score, "energy": energy, "shields": shields}
