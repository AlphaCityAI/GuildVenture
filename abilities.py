ABILITIES = {
    "Nodewalkers": [
        {
            "name": "Data Spike",
            "description": "Unleash a burst of corrupted data, dealing 5 damage directly to an enemy.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 5},
            "target": "enemy"
        },
        {
            "name": "System Restore",
            "description": "Purge corrupted code from a friendly system, healing yourself for 8 HP.",
            "charges": 1,
            "effect": {"type": "heal_self", "value": 8},
            "target": "self"
        }
    ],
    "Coinbrokers": [
        {
            "name": "Insider Trading",
            "description": "Use black market intel to predict enemy movements, granting your party a +15 bonus to their next roll.",
            "charges": 2,
            "effect": {"type": "roll_bonus", "value": 15},
            "target": "party"
        },
        {
            "name": "Bailout",
            "description": "Inject a surge of capital, healing all party members for 4 HP.",
            "charges": 1,
            "effect": {"type": "heal_party", "value": 4},
            "target": "party"
        }
    ],
    "Glitchborn": [
        {
            "name": "Ambush",
            "description": "Exploit a system vulnerability to strike from the shadows, dealing 7 damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 7},
            "target": "enemy"
        },
        {
            "name": "Phase Shift",
            "description": "Become momentarily intangible, guaranteeing a successful escape or dodge on your next action.",
            "charges": 1,
            "effect": {"type": "guaranteed_success", "category": "stealth"},
            "target": "self"
        }
    ],
    "Chainbreakers": [
        {
            "name": "Overcharge",
            "description": "Divert power to your augments for a devastating blow, dealing 8 damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 8},
            "target": "enemy"
        },
        {
            "name": "Adrenaline Surge",
            "description": "Trigger your combat stimulants, healing yourself for 10 HP.",
            "charges": 1,
            "effect": {"type": "heal_self", "value": 10},
            "target": "self"
        }
    ]
}