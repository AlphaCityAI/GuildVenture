BOSS_TRAITS = {
    "Mech-Warden": {
        "description": "A chrome executioner deployed by the Singularity. Walking fortress built to end uprisings.",
        "abilities": [
            {
                "name": "Siege Cannon",
                "description": "Charged plasma beam annihilates a priority target.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 12}
                ]
            },
            {
                "name": "Repulsor Shield",
                "description": "Kinetic surge shoves the squad off-balance while the Warden resets.",
                "effects": [
                    {"type": "roll_bonus", "target": "players", "value": -5},
                    {"type": "heal", "target": "self", "value": 8}
                ]
            },
            {
                "name": "Suppressing Fire",
                "description": "Disciplined volleys rake the battlefield.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 5}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Kinetic", "value": 0.75, "narrative": "Its heavy plating excels at deflecting physical force."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Enertech", "value": 1.25, "narrative": "Concentrated energy attacks can overload its sophisticated systems."}
        ]
    },

    "Data-Wraith": {
        "description": "A ghost-process born of crashed minds and orphaned code. It hunts intent, not flesh.",
        "abilities": [
            {
                "name": "Recursive Curse",
                "description": "Paradox logic unthreads a victim’s defenses.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 9},
                    {"type": "roll_bonus", "target": "players", "value": -6}
                ]
            },
            {
                "name": "Bit Drain",
                "description": "Siphons charge from everyone, surging stronger.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 3},
                    {"type": "heal", "target": "self", "value": 6}
                ]
            },
            {
                "name": "Ghost Protocol",
                "description": "Phases thin, computations align.",
                "effects": [
                    {"type": "roll_bonus", "target": "self", "value": 10}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Umbral", "value": 0.75, "narrative": "As a being of shadow data, it is naturally resistant to Umbral energies."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Neural", "value": 1.25, "narrative": "Focused psionic attacks can disrupt its non-physical form."}
        ]
    },

    "Bio-Titan": {
        "description": "Illegal wetlab horror—muscle braided with actuator cable, always flexing toward the kill.",
        "abilities": [
            {
                "name": "Caustic Spew",
                "description": "Corrosive bile scalds a target, then the beast pounces.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 10}
                ]
            },
            {
                "name": "Frenzied Regeneration",
                "description": "Plates knit; wounds close mid-charge.",
                "effects": [
                    {"type": "heal", "target": "self", "value": 10}
                ]
            },
            {
                "name": "Ground Slam",
                "description": "Shockwave buckles footing; the squad staggers.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 4},
                    {"type": "roll_bonus", "target": "players", "value": -6}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Neural", "value": 0.75, "narrative": "Its primal rage and simple mind are difficult to affect with psionics."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Kinetic", "value": 1.25, "narrative": "For all its strength, its organic body is vulnerable to overwhelming brute force."}
        ]
    },

    "Dynastic Scion": {
        "description": "Heir to obscene wealth in a sovereign-grade exosuit; battlefield is just another market.",
        "abilities": [
            {
                "name": "Hostile Takeover",
                "description": "Locks options and dictates the next exchange.",
                "effects": [
                    {"type": "roll_bonus", "target": "players", "value": -10}
                ]
            },
            {
                "name": "Market Crash",
                "description": "Volatility floods the local net; hesitation is punished.",
                "effects": [
                    {"type": "roll_bonus", "target": "players", "value": -8},
                    {"type": "direct_damage", "target": "all", "value": 3}
                ]
            },
            {
                "name": "Golden Handshake",
                "description": "Calls in favors and processes a fast refurbishment.",
                "effects": [
                    {"type": "heal", "target": "self", "value": 6},
                    {"type": "roll_bonus", "target": "self", "value": 10}
                ]
            }
        ],
        "strengths": [
             {"type": "damage_type_resistance", "damage_type": "Archon", "value": 0.75, "narrative": "An Overlord's authority is inherently resistant to the influence of its own kind."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Mercantile", "value": 1.25, "narrative": "Its reliance on a stable market makes it vulnerable to a Coinbroker's economic warfare."}
        ]
    },

    "Psionic Prophet": {
        "description": "A Neuralife who tore out their leash and learned to weaponize the echo of belief.",
        "abilities": [
            {
                "name": "Mind Shatter",
                "description": "Splinters a single mind with a focused scream.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 9},
                    {"type": "roll_bonus", "target": "players", "value": -10}
                ]
            },
            {
                "name": "Collective Agony",
                "description": "Harvests team pain and returns it with interest.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 4},
                    {"type": "heal", "target": "self", "value": 4}
                ]
            },
            {
                "name": "Prophetic Vision",
                "description": "Steps aside just before the strike.",
                "effects": [
                    {"type": "roll_bonus", "target": "self", "value": 10}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Mercantile", "value": 0.75, "narrative": "Having transcended material wealth, economic attacks have little effect."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Umbral", "value": 1.25, "narrative": "Its hyper-focused mind is open to attacks from the unseen, shadow angles a Glitchborn uses."}
        ]
    },

    "Glitch Abomination": {
        "description": "A heap of data-rot congealed into teeth and noise; reality is just a suggestion nearby.",
        "abilities": [
            {
                "name": "Data Cascade",
                "description": "Bursts of chaotic packets hammer the team.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 4},
                    {"type": "roll_bonus", "target": "players", "value": -8}
                ]
            },
            {
                "name": "Unstable Matrix",
                "description": "Reconfigures mid-lunge; grows hungrier.",
                "effects": [
                    {"type": "heal", "target": "self", "value": 8},
                    {"type": "roll_bonus", "target": "self", "value": 8}
                ]
            },
            {
                "name": "Reality Tear",
                "description": "Rips a straight line through space and anything in it.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 12}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Enertech", "value": 0.75, "narrative": "Its chaotic code corrupts and disperses clean, ordered energy attacks."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Mechanical", "value": 1.25, "narrative": "The pure, unyielding logic of a Singularity's attack can stabilize and delete its glitched form."}
        ]
    },

    "Syndicate Enforcer": {
        "description": "Coinbroker warlord with bought loyalties and illegal hardware, entrenched behind favors.",
        "abilities": [
            {
                "name": "Call Enforcers",
                "description": "Signals muscle; suppressive fire pins you while the boss advances.",
                "effects": [
                    {"type": "direct_damage", "target": "all", "value": 6}
                ]
            },
            {
                "name": "Flashbang Drone",
                "description": "Blinds sensors and rattles nerves.",
                "effects": [
                    {"type": "roll_bonus", "target": "players", "value": -10}
                ]
            },
            {
                "name": "Asset Liquidation",
                "description": "Sacrifices a pawn to hammer a priority target.",
                "effects": [
                    {"type": "direct_damage", "target": "single", "value": 11}
                ]
            }
        ],
        "strengths": [
            {"type": "damage_type_resistance", "damage_type": "Mechanical", "value": 0.75, "narrative": "Its black-market tech is specifically designed to counter Singularity systems."}
        ],
        "weaknesses": [
            {"type": "damage_type_vulnerability", "damage_type": "Archon", "value": 1.25, "narrative": "The absolute authority of an Overlord's attack bypasses any bought loyalty."}
        ]
    }
}
