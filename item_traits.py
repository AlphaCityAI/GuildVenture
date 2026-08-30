"""
Item Traits System for Alpha City RPG
Defines item abilities, damage bonuses, and rarity scaling
"""

from typing import Dict, List, Any, Optional
import copy

ITEM_SLOTS = ["Cranial", "Chassis", "Equipment", "Mobility", "Companion"]
ITEM_SPECIALTIES = ["Umbral", "Blockchain", "Kinetic", "Enertech", "Archon", "Neural", "Mechanical"]

SPECIALTY_TO_DAMAGE_TYPE = {
    "Umbral": "Umbral",
    "Blockchain": "Mercantile",
    "Mercantile": "Mercantile",
    "Kinetic": "Kinetic",
    "Enertech": "Enertech",
    "Archon": "Archon",
    "Neural": "Neural",
    "Mechanical": "Mechanical"
}

RARITY_ORDER = ["Salvage", "Gutter-Tech", "Street Mod", "Black Market", "Node-Forged", "Peerless"]

RARITY_DAMAGE_BONUS = {
    "Salvage": 0.05,
    "Gutter-Tech": 0.10,
    "Street Mod": 0.15,
    "Black Market": 0.25,
    "Node-Forged": 0.40,
    "Peerless": 0.60
}

RARITY_ABILITY_SCALING = {
    "Salvage": {"damage_mult": 0.6, "heal_mult": 0.6, "charges": 1},
    "Gutter-Tech": {"damage_mult": 0.8, "heal_mult": 0.8, "charges": 1},
    "Street Mod": {"damage_mult": 1.0, "heal_mult": 1.0, "charges": 2},
    "Black Market": {"damage_mult": 1.2, "heal_mult": 1.2, "charges": 2},
    "Node-Forged": {"damage_mult": 1.5, "heal_mult": 1.5, "charges": 3},
    "Peerless": {"damage_mult": 2.0, "heal_mult": 2.0, "charges": 4}
}

RARITY_ICONS = {
    "Salvage": "⚪️",
    "Gutter-Tech": "🟢",
    "Street Mod": "🔵",
    "Black Market": "🟣",
    "Node-Forged": "🟡",
    "Peerless": "💥"
}

SLOT_ABILITIES = {
    "Cranial": {
        "Umbral": {
            "name": "Shadow Whisper",
            "description": "Project dark thoughts into the target's mind, dealing psychic damage.",
            "base_effect": {"type": "direct_damage", "value": 8, "damage_type": "Umbral"}
        },
        "Blockchain": {
            "name": "Market Insight",
            "description": "Analyze enemy weaknesses through financial data patterns, granting a roll bonus.",
            "base_effect": {"type": "roll_bonus", "value": 15}
        },
        "Kinetic": {
            "name": "Neural Overcharge",
            "description": "Amplify your combat reflexes for a devastating strike.",
            "base_effect": {"type": "direct_damage", "value": 10, "damage_type": "Kinetic"}
        },
        "Enertech": {
            "name": "Synaptic Surge",
            "description": "Channel raw energy through your neural implant to shock the target.",
            "base_effect": {"type": "direct_damage", "value": 9, "damage_type": "Enertech"}
        },
        "Archon": {
            "name": "Command Protocol",
            "description": "Assert dominance with an authoritative mental command.",
            "base_effect": {"type": "direct_damage", "value": 7, "damage_type": "Archon"}
        },
        "Neural": {
            "name": "Mind Mend",
            "description": "Reorganize neural pathways to heal yourself.",
            "base_effect": {"type": "heal", "value": 10}
        },
        "Mechanical": {
            "name": "Logic Spike",
            "description": "Fire a concentrated data packet that disrupts target systems.",
            "base_effect": {"type": "direct_damage", "value": 8, "damage_type": "Mechanical"}
        }
    },
    "Chassis": {
        "Umbral": {
            "name": "Shadow Shroud",
            "description": "Envelop yourself in darkness, regenerating health.",
            "base_effect": {"type": "heal", "value": 12}
        },
        "Blockchain": {
            "name": "Economic Shield",
            "description": "Redistribute damage through market algorithms, healing the party.",
            "base_effect": {"type": "heal", "value": 6, "target": "party"}
        },
        "Kinetic": {
            "name": "Impact Absorb",
            "description": "Convert incoming kinetic energy into healing power.",
            "base_effect": {"type": "heal", "value": 14}
        },
        "Enertech": {
            "name": "Energy Barrier",
            "description": "Project a protective field that restores HP.",
            "base_effect": {"type": "heal", "value": 11}
        },
        "Archon": {
            "name": "Authority Aura",
            "description": "Your commanding presence bolsters your defenses.",
            "base_effect": {"type": "heal", "value": 10}
        },
        "Neural": {
            "name": "Psionic Shield",
            "description": "Create a mental barrier that repairs physical damage.",
            "base_effect": {"type": "heal", "value": 13}
        },
        "Mechanical": {
            "name": "Nanobot Repair",
            "description": "Deploy repair nanobots to restore structural integrity.",
            "base_effect": {"type": "heal", "value": 15}
        }
    },
    "Equipment": {
        "Umbral": {
            "name": "Void Grenade",
            "description": "Throw a grenade that explodes into consuming darkness.",
            "base_effect": {"type": "direct_damage", "value": 12, "damage_type": "Umbral"}
        },
        "Blockchain": {
            "name": "Crypto Bomb",
            "description": "Deploy a device that crashes enemy financial systems.",
            "base_effect": {"type": "direct_damage", "value": 11, "damage_type": "Mercantile"}
        },
        "Kinetic": {
            "name": "Concussion Charge",
            "description": "Detonate a powerful kinetic blast.",
            "base_effect": {"type": "direct_damage", "value": 14, "damage_type": "Kinetic"}
        },
        "Enertech": {
            "name": "Plasma Launcher",
            "description": "Fire a concentrated plasma bolt.",
            "base_effect": {"type": "direct_damage", "value": 13, "damage_type": "Enertech"}
        },
        "Archon": {
            "name": "Sanction Device",
            "description": "Activate an Overlord-sanctioned punishment protocol.",
            "base_effect": {"type": "direct_damage", "value": 10, "damage_type": "Archon"}
        },
        "Neural": {
            "name": "Psi Amplifier",
            "description": "Boost your next action with psionic energy.",
            "base_effect": {"type": "roll_bonus", "value": 20}
        },
        "Mechanical": {
            "name": "EMP Burst",
            "description": "Release an electromagnetic pulse that damages mechanical targets.",
            "base_effect": {"type": "direct_damage", "value": 12, "damage_type": "Mechanical"}
        }
    },
    "Mobility": {
        "Umbral": {
            "name": "Shadow Step",
            "description": "Phase through shadows to strike from an unexpected angle.",
            "base_effect": {"type": "direct_damage", "value": 9, "damage_type": "Umbral"}
        },
        "Blockchain": {
            "name": "Market Momentum",
            "description": "Ride economic data waves to boost your next action.",
            "base_effect": {"type": "roll_bonus", "value": 18}
        },
        "Kinetic": {
            "name": "Velocity Strike",
            "description": "Build up speed for a devastating impact.",
            "base_effect": {"type": "direct_damage", "value": 11, "damage_type": "Kinetic"}
        },
        "Enertech": {
            "name": "Energy Dash",
            "description": "Surge forward in a burst of energy, striking the target.",
            "base_effect": {"type": "direct_damage", "value": 10, "damage_type": "Enertech"}
        },
        "Archon": {
            "name": "Executive Retreat",
            "description": "Tactical repositioning restores your composure and HP.",
            "base_effect": {"type": "heal", "value": 8}
        },
        "Neural": {
            "name": "Psionic Leap",
            "description": "Teleport a short distance using mental focus.",
            "base_effect": {"type": "roll_bonus", "value": 15}
        },
        "Mechanical": {
            "name": "Thruster Boost",
            "description": "Activate thrusters for a high-speed ramming attack.",
            "base_effect": {"type": "direct_damage", "value": 10, "damage_type": "Mechanical"}
        }
    },
    "Companion": {
        "Umbral": {
            "name": "Shadow Bite",
            "description": "Your shadow-beast companion lunges at the target.",
            "base_effect": {"type": "direct_damage", "value": 11, "damage_type": "Umbral"}
        },
        "Blockchain": {
            "name": "Broker Bot Attack",
            "description": "Your financial drone executes a hostile takeover attack.",
            "base_effect": {"type": "direct_damage", "value": 10, "damage_type": "Mercantile"}
        },
        "Kinetic": {
            "name": "Combat Drone Strike",
            "description": "Your combat drone delivers a punishing blow.",
            "base_effect": {"type": "direct_damage", "value": 13, "damage_type": "Kinetic"}
        },
        "Enertech": {
            "name": "Energy Familiar",
            "description": "Your energy construct blasts the target.",
            "base_effect": {"type": "direct_damage", "value": 12, "damage_type": "Enertech"}
        },
        "Archon": {
            "name": "Enforcer Summon",
            "description": "Your personal enforcer delivers punishment.",
            "base_effect": {"type": "direct_damage", "value": 9, "damage_type": "Archon"}
        },
        "Neural": {
            "name": "Psionic Familiar",
            "description": "Your mental construct heals you.",
            "base_effect": {"type": "heal", "value": 9}
        },
        "Mechanical": {
            "name": "Mech Companion Strike",
            "description": "Your mechanical companion unleashes its weapons.",
            "base_effect": {"type": "direct_damage", "value": 12, "damage_type": "Mechanical"}
        }
    }
}


def get_item_ability(slot: str, specialty: str, rarity: str) -> Optional[Dict[str, Any]]:
    """
    Generate an ability for an item based on its slot, specialty, and rarity.
    Returns a complete ability dictionary with scaled values.
    """
    if slot not in SLOT_ABILITIES or specialty not in SLOT_ABILITIES.get(slot, {}):
        return None
    
    base_ability = SLOT_ABILITIES[slot][specialty]
    scaling = RARITY_ABILITY_SCALING.get(rarity, RARITY_ABILITY_SCALING["Salvage"])
    
    ability = {
        "name": base_ability["name"],
        "description": base_ability["description"],
        "charges": scaling["charges"],
        "max_charges": scaling["charges"],
        "from_item": True,
        "effect": copy.deepcopy(base_ability["base_effect"])
    }
    
    effect = ability["effect"]
    if effect["type"] == "direct_damage":
        effect["value"] = int(effect["value"] * scaling["damage_mult"])
    elif effect["type"] == "heal":
        effect["value"] = int(effect["value"] * scaling["heal_mult"])
    elif effect["type"] == "roll_bonus":
        effect["value"] = int(effect["value"] * scaling["damage_mult"])
    
    return ability


def get_damage_bonus_for_specialty(specialty: str, rarity: str) -> float:
    """
    Get the damage bonus multiplier for an item's specialty.
    This bonus applies to all abilities that match the specialty's damage type.
    """
    return RARITY_DAMAGE_BONUS.get(rarity, 0.0)


def get_damage_type_for_specialty(specialty: str) -> str:
    """
    Get the damage type that a specialty boosts.
    """
    return SPECIALTY_TO_DAMAGE_TYPE.get(specialty, specialty)


def calculate_equipped_damage_bonus(equipped_items: Dict[str, Dict], damage_type: str) -> float:
    """
    Calculate the total damage bonus from all equipped items for a given damage type.
    Returns a multiplier (e.g., 1.15 for +15% damage).
    """
    total_bonus = 0.0
    
    for slot, item in equipped_items.items():
        if item is None:
            continue
        item_specialty = item.get("specialty")
        item_rarity = item.get("rarity")
        if item_specialty and item_rarity:
            item_damage_type = get_damage_type_for_specialty(item_specialty)
            if item_damage_type == damage_type:
                total_bonus += get_damage_bonus_for_specialty(item_specialty, item_rarity)
    
    return 1.0 + total_bonus


def get_abilities_from_equipped_items(equipped_items: Dict[str, Dict], reset_charges: bool = False) -> List[Dict]:
    """
    Get all abilities granted by equipped items.
    If reset_charges is True, resets all charges to max_charges.
    """
    abilities = []
    
    for slot, item in equipped_items.items():
        if item is None:
            continue
        
        item_ability = item.get("ability")
        if item_ability:
            ability_copy = copy.deepcopy(item_ability)
            if reset_charges and "max_charges" in ability_copy:
                ability_copy["charges"] = ability_copy["max_charges"]
            abilities.append(ability_copy)
    
    return abilities


def create_item_data(name: str, slot: str, specialty: str, rarity: str, background: str, durability: int = 3) -> Dict[str, Any]:
    """
    Create a complete item data dictionary for storage.
    """
    ability = get_item_ability(slot, specialty, rarity)
    
    return {
        "name": name,
        "slot": slot,
        "specialty": specialty,
        "rarity": rarity,
        "background": background,
        "durability": durability,
        "max_durability": 3,
        "ability": ability
    }


def get_empty_equipped_items() -> Dict[str, None]:
    """
    Return an empty equipped items dictionary with all slots set to None.
    """
    return {slot: None for slot in ITEM_SLOTS}


def get_slot_icon(slot: str) -> str:
    """Get an emoji icon for each item slot."""
    icons = {
        "Cranial": "🧠",
        "Chassis": "🛡️",
        "Equipment": "🔧",
        "Mobility": "🦿",
        "Companion": "🐾"
    }
    return icons.get(slot, "📦")


def get_specialty_icon(specialty: str) -> str:
    """Get an emoji icon for each specialty."""
    icons = {
        "Umbral": "🌑",
        "Blockchain": "⛓️",
        "Kinetic": "💥",
        "Enertech": "⚡",
        "Archon": "👑",
        "Neural": "🔮",
        "Mechanical": "⚙️"
    }
    return icons.get(specialty, "✨")


def format_item_display(item: Dict[str, Any]) -> str:
    """Format an item for display in chat."""
    rarity_icon = RARITY_ICONS.get(item.get("rarity", ""), "")
    slot_icon = get_slot_icon(item.get("slot", ""))
    specialty_icon = get_specialty_icon(item.get("specialty", ""))
    
    lines = [
        f"*{item.get('name', 'Unknown Item')}*",
        f"{slot_icon} *Slot*: {item.get('slot', 'Unknown')}",
        f"{specialty_icon} *Specialty*: {item.get('specialty', 'Unknown')}",
        f"{rarity_icon} *Rarity*: {item.get('rarity', 'Unknown')}",
        f"🔩 *Durability*: {item.get('durability', 0)}/{item.get('max_durability', 3)}"
    ]
    
    ability = item.get("ability")
    if ability:
        effect = ability.get("effect", {})
        effect_desc = ""
        if effect.get("type") == "direct_damage":
            effect_desc = f"Deal {effect.get('value', 0)} {effect.get('damage_type', '')} damage"
        elif effect.get("type") == "heal":
            if effect.get("target") == "party":
                effect_desc = f"Heal party for {effect.get('value', 0)} HP"
            else:
                effect_desc = f"Heal for {effect.get('value', 0)} HP"
        elif effect.get("type") == "roll_bonus":
            effect_desc = f"+{effect.get('value', 0)} to next roll"
        
        lines.append(f"⚡ *Ability*: {ability.get('name', 'Unknown')} ({ability.get('max_charges', 1)} charges)")
        lines.append(f"   _{effect_desc}_")
    
    damage_type = get_damage_type_for_specialty(item.get("specialty", ""))
    damage_bonus = get_damage_bonus_for_specialty(item.get("specialty", ""), item.get("rarity", "Salvage"))
    if damage_bonus > 0:
        lines.append(f"📈 *Passive*: +{int(damage_bonus * 100)}% {damage_type} damage")
    
    return "\n".join(lines)
