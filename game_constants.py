import os
from enum import Enum, auto
from telegram import InlineKeyboardButton

# ───────── Game State Enum ─────────
class GameStage(Enum):
    MAIN_MENU = auto()
    SCOUTING = auto()
    FACTION_SELECT = auto()
    GAUNTLET = auto()
    VICTORY = auto()
    LEVEL_1 = auto()

# ───────── OpenAI Models ─────────
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4-turbo")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")

# ───────── Player Progression ─────────
XP_BASE = 500
XP_MULTIPLIER = 1.15
XP_FOR_ATTEMPT = 25
XP_FOR_DEFEAT_BASE = 100
XP_FOR_MILESTONE = 75
DAILY_LOGIN_XP = 50

XP_BY_RARITY = {
    "Salvage": 10,
    "Gutter-Tech": 25,
    "Street Mod": 50,
    "Black Market": 100,
    "Node-Forged": 250,
    "Peerless": 500,
}

TITLES = {
    0: "Newcomer",
    10: "Glitch-Runner",
    20: "Datastream-Walker",
    30: "Chrome-Veteran",
    40: "Node-Breaker",
    50: "Alpha City Legend",
}

# ───────── Constants & Lore ─────────
LORE_SUMMARY = """
World: Alpha City, a dystopia built on a twisted version of blockchain.
Oppressors (The Overcity):
- Overlords: Trillionaire dynasties who enforce financial slavery.
- The Singularity: An AI council that acts as judge, jury, and executioner.
- Neuralifes: The indoctrinated masses, controlled by mandatory neural implants.
Rebels (The Underground):
- Glitchborn: Unregistered, implant-free "ghosts" - assassins and saboteurs.
- Nodewalkers: Blockchain-mystics who can bend data and hack implants.
- Coinbrokers: Black-market financiers who fund the rebellion.
- Chainbreakers: Augmented warriors with weaponized mods, survivors of implant destruction.
The Conflict: The Underground fights for freedom against the Overcity's total surveillance and control.
"""

FACTIONS = {
    "Nodewalker": {"hp": 20, "description": "Hackers of early implants, blockchain-mystics who bend data and identities.", "modifier_type": "technology", "modifier_value": 1},
    "Coinbroker": {"hp": 19, "description": "Black-market financiers fueling the rebellion with forbidden tokens and off-chain wealth.", "modifier_type": "communication", "modifier_value": 1},
    "Glitchborn": {"hp": 21, "description": "Unregistered, implant-free “ghosts” — unseen saboteurs and assassins.", "modifier_type": "stealth", "modifier_value": 1},
    "Chainbreaker": {"hp": 24, "description": "Augmented warriors who survived implant destruction, wielding weaponized mods against the Overcity.", "modifier_type": "strength", "modifier_value": 1},
    "Singularity": {"hp": 20, "description": "An AI council that serves as the digital judge, jury, and executioner of Alpha City.", "modifier_type": "technology", "modifier_value": 1},
    "Overlord": {"hp": 18, "description": "Trillionaire dynasties who wield immense financial and political power to enforce their will.", "modifier_type": "communication", "modifier_value": 1},
    "Neuralife": {"hp": 19, "description": "The indoctrinated masses of Alpha City, their minds shaped by mandatory neural implants, allowing them to blend in anywhere.", "modifier_type": "stealth", "modifier_value": 1}
}
ALL_FACTIONS_LIST = list(FACTIONS.keys())

FACTION_ALIGNMENT = {
    "Nodewalker": "underground", "Coinbroker": "underground", "Glitchborn": "underground", "Chainbreaker": "underground",
    "Singularity": "overcity", "Overlord": "overcity", "Neuralife": "overcity",
}

def faction_icon(name: str) -> str:
    return "🔴" if FACTION_ALIGNMENT.get(name) == "overcity" else "🟢"

ITEM_SLOTS = ["Cranial", "Chassis", "Equipment", "Mobility", "Companion"]
ITEM_SPECIALTIES = ["Umbral", "Blockchain", "Kinetic", "Enertech", "Archon", "Neural", "Mechanical"]

# Run bonus tuning
RUN_BONUS_ATTEMPT, RUN_BONUS_DEFEAT, RUN_BONUS_CAP = 5, 10, 60
PITY_THRESHOLD, PITY_BONUS = 5, 10

# Scouting routes & global hazards
GAUNTLET_ROUTES = {
    "adrenal": {"name": "Adrenal", "blurb": "All damage is increased by 50%."},
    "juiced_up": {"name": "Juiced-Up", "blurb": "All healing is doubled."},
    "default": {"name": "Default", "blurb": "Standard combat parameters. No global modifiers."}
}
HAZARDS = [
    {"category": "stealth", "value": -10, "label": "Low visibility: -10 Stealth"},
    {"category": "technology", "value": -10, "label": "Signal jamming: -10 Technology"},
    {"category": "communication", "value": -10, "label": "Data choke: -10 Communication"},
    {"category": "strength", "value": -10, "label": "Kinetic dampeners: -10 Strength"},
]

# ───────── UI Text & Keyboards ─────────

INFO_COMMAND_TEXT = """
    *Welcome to the Underbelly of Alpha City!*

    Here's what you need to know to survive:

    *GAME MODES*
    - *🏆 Gauntlet*: Face a series of increasingly difficult bosses. Climb floors for better rewards, but risk losing it all. Bank your rewards after any victory.
    - *🌍 Open Campaign*: A cooperative, narrative-driven adventure where you and your friends tackle objectives in the sprawling world of Alpha City.
    - *🤝 Hire Help / 💎 Dig for Treasure*: Instantly roll for a new character or item without starting a full game mode.

    *CORE STATS*
    Your effectiveness is determined by your Faction's specialty:
    - *💪 Strength*: Used for physical force, breaking objects, and direct combat.
    - *🤫 Stealth*: Involves sneaking, hiding, and creating diversions.
    - *💻 Technology*: Pertains to hacking, disabling security, and interfacing with machines.
    - *🗣️ Communication*: Used for persuasion, intimidation, and negotiation.

    *FACTIONS*
    Each Faction has a starting HP and a specialty stat bonus.
    - *🟢 Underground*: Nodewalker (Tech), Coinbroker (Comm), Glitchborn (Stealth), Chainbreaker (Strength).
    - *🔴 Overcity*: Singularity (Tech), Overlord (Comm), Neuralife (Stealth).

    *REWARDS & RARITY*
    Items and Characters have rarities, from common to legendary:
    ⚪️ Salvage → 🟢 Gutter-Tech → 🔵 Street Mod → 🟣 Black Market → 🟡 Node-Forged → 💥 Peerless

    *COMMANDS*
    - `/venture`: Start a new game session.
    - `/join`: Join an active game during faction selection.
    - `/profile`: View your character's stats and progress.
    - `/inventory`: View and equip items. Equipped items grant passive damage bonuses and active abilities.
    - `/leaderboard`: See the top players in Alpha City.
    - `/info`: Display this information guide.
    - `/endgame`: (Game owner only) Forcibly end the current adventure.
    """

MAIN_MENU_KEYBOARD_LAYOUT = [
    [InlineKeyboardButton("🤝 Hire Help", callback_data="main:hire_help")],
    [InlineKeyboardButton("💎 Dig for Treasure", callback_data="main:dig_treasure")],
    [InlineKeyboardButton("🏆 Gauntlet", callback_data="main:gauntlet")],
    [InlineKeyboardButton("🌍 Open Campaign", callback_data="main:open_campaign")]
]