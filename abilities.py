ABILITIES = {
    "Nodewalker": [
        {
            "name": "Ping Attack",
            "description": "A weak, annoying data ping that deals 4 damage.",
            "effect": {"type": "direct_damage", "value": 4, "damage_type": "Enertech"}
        },
        {
            "name": "Data Spike",
            "description": "Inject hostile code to overload enemy nodes, dealing 14 damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 14, "damage_type": "Enertech"}
        },
        {
            "name": "System Restore",
            "description": "Recompile your own stack and purge junk data, healing yourself for 8 HP.",
            "charges": 1,
            "effect": {"type": "heal", "value": 8}
        }
    ],

    "Coinbroker": [
        {
            "name": "Market Fluctuation",
            "description": "Exploit a minor market opening to cause a distraction, dealing 4 damage.",
            "effect": {"type": "direct_damage", "value": 4, "damage_type": "Mercantile"}
        },
        {
            "name": "Hostile Liquidation",
            "description": "Force an asset sell-off, dealing 13 economic shock damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 13, "damage_type": "Mercantile"}
        },
        {
            "name": "Bailout",
            "description": "Liquidate emergency funds to stabilize the squad, healing all party members for 5 HP.",
            "charges": 2,
            "effect": {"type": "heal", "value": 5, "target": "party"}
        }
    ],

    "Glitchborn": [
        {
            "name": "Flicker Strike",
            "description": "A quick, disorienting jab from the shadows, dealing 5 damage.",
            "effect": {"type": "direct_damage", "value": 5, "damage_type": "Umbral"}
        },
        {
            "name": "Ambush",
            "description": "Step from blind data-zones and strike, dealing 10 damage.",
            "charges": 3,
            "effect": {"type": "direct_damage", "value": 10, "damage_type": "Umbral"}
        },
        {
            "name": "Unmake",
            "description": "Briefly de-rez a target's core code, dealing 9 umbral damage.",
            "charges": 1,
            "effect": {"type": "direct_damage", "value": 9, "damage_type": "Umbral"}
        }
    ],

    "Chainbreaker": [
        {
            "name": "Scrap Punch",
            "description": "A heavy blow with augmented knuckles, dealing 6 damage.",
            "effect": {"type": "direct_damage", "value": 6, "damage_type": "Kinetic"}
        },
        {
            "name": "Overcharge",
            "description": "Route raw current through combat mods for a brutal hit, dealing 15 damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 15, "damage_type": "Kinetic"}
        },
        {
            "name": "Adrenaline Surge",
            "description": "Trigger combat stims and reinforce plating, healing yourself for 6 HP.",
            "charges": 1,
            "effect": {"type": "heal", "value": 6}
        }
    ],

    "Singularity": [
        {
            "name": "Process Query",
            "description": "Run a low-level diagnostic attack, dealing 5 damage.",
            "effect": {"type": "direct_damage", "value": 5, "damage_type": "Mechanical"}
        },
        {
            "name": "Logic Bomb",
            "description": "Recursive payload detonates inside target heuristics, dealing 12 damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 12, "damage_type": "Mechanical"}
        },
        {
            "name": "Recursive Deletion",
            "description": "Flag a target for total data erasure, dealing 6 technology damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 6, "damage_type": "Mechanical"}
        }
    ],

    "Overlord": [
        {
            "name": "Asset Forfeiture",
            "description": "A minor, legally-backed seizure of processing power, dealing 3 damage.",
            "effect": {"type": "direct_damage", "value": 3, "damage_type": "Archon"}
        },
        {
            "name": "Sanction Strike",
            "description": "Call in a targeted orbital strike, dealing 8 kinetic damage.",
            "charges": 2,
            "effect": {"type": "direct_damage", "value": 8, "damage_type": "Archon"}
        },
        {
            "name": "Private Security",
            "description": "Activate a personal shield and med-kit, healing for 12 HP.",
            "charges": 2,
            "effect": {"type": "heal", "value": 12}
        }
    ],

    "Neuralife": [
        {
            "name": "Frantic Headbutt",
            "description": "An uncontrolled, desperate physical strike, dealing 3 damage.",
            "effect": {"type": "direct_damage", "value": 3, "damage_type": "Neural"}
        },
        {
            "name": "Ghost in the Machine",
            "description": "Hijack implant drift and strike from noise, dealing 8 damage.",
            "charges": 3,
            "effect": {"type": "direct_damage", "value": 8, "damage_type": "Neural"}
        },
        {
            "name": "Synaptic Overload",
            "description": "Flood the target's neural link with raw data, dealing 7 neural damage.",
            "charges": 3,
            "effect": {"type": "direct_damage", "value": 7, "damage_type": "Neural"}
        }
    ]
}