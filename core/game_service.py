from __future__ import annotations

import random
import re

from core.economy import add_coins, debit_coins, get_balance, settle_wager


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


def guess_number_reward(maximum: int) -> int:
    if int(maximum) >= 1000:
        return 50
    if int(maximum) >= 200:
        return 30
    if int(maximum) >= 50:
        return 20
    return 10


def guess_temperature(distance: int) -> str:
    clean = abs(int(distance))
    if clean <= 2:
        return "🔥 Горячо!"
    if clean <= 5:
        return "♨️ Тепло"
    return "🧊 Холодно"


def blackjack_outcome(player_total: int, dealer_total: int) -> str:
    player = int(player_total)
    dealer = int(dealer_total)
    if player > 21:
        return "bust"
    if dealer > 21 or player > dealer:
        return "win"
    if player == dealer:
        return "push"
    return "lose"


def blackjack_duel_winner(first_total: int, second_total: int) -> int:
    first = int(first_total) if int(first_total) <= 21 else 0
    second = int(second_total) if int(second_total) <= 21 else 0
    return 1 if first > second else (-1 if second > first else 0)


def can_double_blackjack(user_id: int, current_bet: int) -> bool:
    """No stake is reserved, so the wallet must cover the full doubled loss."""
    return get_balance(int(user_id)) >= int(current_bet) * 2


def settle_solo_blackjack(
    user_id: int,
    bet: int,
    outcome: str,
    *,
    natural: bool = False,
    timeout: bool = False,
) -> dict[str, int | str]:
    clean_bet = max(1, int(bet))
    if outcome == "win":
        profit = int(clean_bet * 1.5) if natural else clean_bet
        balance = add_coins(
            int(user_id),
            profit,
            "game_win",
            {"game": "blackjack", "natural": bool(natural)},
        )
        return {"status": "won", "amount": profit, "balance": balance}
    if outcome == "push":
        return {"status": "push", "amount": 0, "balance": get_balance(int(user_id))}
    debit = debit_coins(
        int(user_id),
        clean_bet,
        "game_lose",
        {"game": "blackjack", "outcome": outcome, "timeout": bool(timeout)},
        allow_partial=True,
    )
    return {
        "status": "lost",
        "amount": int(debit.get("actual", 0)),
        "balance": int(debit.get("balance", 0)),
    }


def settle_blackjack_duel(
    winner_id: int,
    loser_id: int,
    bet: int,
) -> dict[str, int | str]:
    return settle_wager(
        int(winner_id),
        int(loser_id),
        int(bet),
        game="blackjack_duel",
    )
