from __future__ import annotations

import random
import re


CHOICES = ("камень", "ножницы", "бумага")
SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
CARD_VALUES = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
    "A": 11,
}


def new_deck(*, rng: random.Random | None = None) -> list[str]:
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    (rng or random).shuffle(deck)
    return deck


def card_value(card: str) -> int:
    return CARD_VALUES.get(card[:-1], 0)


def hand_total(hand: list[str] | tuple[str, ...]) -> int:
    total = sum(card_value(card) for card in hand)
    aces = sum(1 for card in hand if card[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def hand_text(hand: list[str] | tuple[str, ...]) -> str:
    return " ".join(hand)


def rps_result(first: str, second: str) -> int:
    if first == second:
        return 0
    return 1 if (first, second) in {
        ("камень", "ножницы"),
        ("ножницы", "бумага"),
        ("бумага", "камень"),
    } else -1


def normalize_hangman_word(value: str, *, minimum: int = 2) -> str:
    word = str(value or "").strip().lower()
    if not re.fullmatch(rf"[а-яёa-z\-]{{{minimum},}}", word):
        raise ValueError("hangman word must contain only letters and hyphens")
    return word


def mask_hangman_word(word: str, guessed: set[str]) -> str:
    return " ".join(char if char in guessed or char == "-" else r"\_" for char in word)
