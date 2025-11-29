LOCATIONS = [
    {
        "name": "The Overlord's Spire",
        "description": "A monument to absolute control, the Spire's chrome-and-onyx facade hides a fortress where The Singularity's core logic processes the master blockchain ledger.",
        "effect": {"type": "modifier", "category": "technology", "value": 15, "narrative": "The Spire's dense data-streams empower your tech."},
        "interaction": {
            "name": "Overload Spire Core", "category": "technology",
            "success_effect": {"type": "damage", "value": 25}, "failure_effect": {"type": "damage_party", "value": 5},
            "success_narrative": "You successfully interface with the Spire's core, unleashing a crippling feedback loop!",
            "failure_narrative": "The Spire's countermeasures reject your intrusion, blasting the party with raw data!"
        }
    },
    {
        "name": "The Rust-Grave Piers",
        "description": "A maze of corroded shipping containers and automated cranes where toxic canals meet the city's edge.",
        "effect": {"type": "environmental_hazard", "category": "failure", "damage": 2, "narrative": "A misstep on the slick, corroded metal sends you splashing into the toxic sludge."},
        "interaction": {
            "name": "Topple Crane", "category": "strength",
            "success_effect": {"type": "damage", "value": 30}, "failure_effect": {"type": "damage_party", "value": 5},
            "success_narrative": "With a tremendous effort, you bring a cargo crane crashing down onto the enemy!",
            "failure_narrative": "The crane's rusty moorings snap unexpectedly, showering the area with dangerous debris!"
        }
    },
    {
        "name": "The Tangle",
        "description": "A knot of forgotten infrastructure—ancient subway lines, data conduits, and sewers that form the backbone of the Underground.",
        "effect": {"type": "modifier", "category": "stealth", "value": 15, "narrative": "The labyrinthine tunnels and sensor-dead zones of The Tangle conceal your movements."},
        "interaction": {
            "name": "Trigger Tunnel Collapse", "category": "strength",
            "success_effect": {"type": "damage", "value": 25}, "failure_effect": {"type": "damage_party", "value": 6},
            "success_narrative": "You detonate a support structure, burying the boss under tons of rubble!",
            "failure_narrative": "The demolition is unstable, and the resulting cave-in injures the whole party!"
        }
    },
    {
        "name": "The Ticker's Bazaar",
        "description": "Housed in an abandoned stock exchange, the Ticker's Bazaar is the rebellion's black-market heart.",
        "effect": {"type": "modifier", "category": "communication", "value": 15, "narrative": "In the chaotic bazaar, your words carry the weight of a thousand back-alley deals."},
        "interaction": {
            "name": "Incite Market Panic", "category": "communication",
            "success_effect": {"type": "damage", "value": 20}, "failure_effect": {"type": "damage_party", "value": 4},
            "success_narrative": "You spread a rumor that crashes the boss's financial backing, causing their systems to fail!",
            "failure_narrative": "Your attempt to manipulate the crowd backfires, turning them hostile toward your party!"
        }
    },
    {
        "name": "The Data Morgue",
        "description": "A cold, silent necropolis of obsolete server farms, a perfect hiding place for the untraceable.",
        "effect": {"type": "faction_modifier", "faction": ["Nodewalkers", "Glitchborn"], "category": ["technology", "stealth"], "value": 20, "narrative": "The ghost-data of the morgue responds to your unique signature."},
        "interaction": {
            "name": "Purge Server Racks", "category": "technology",
            "success_effect": {"type": "damage", "value": 25}, "failure_effect": {"type": "damage_party", "value": 5},
            "success_narrative": "You trigger a mass data-wipe, and the cascading energy tears through the boss's code!",
            "failure_narrative": "The purge is uncontrolled, and the resulting power surge arcs through the party!"
        }
    },
    {
        "name": "The Corrective",
        "description": "This sterile, white tower is where Neuralifes are sent for 'recalibration' by The Singularity's AI.",
        "effect": {"type": "modifier", "category": "stealth", "value": -10, "narrative": "The Corrective's omnipresent surveillance makes subterfuge nearly impossible."},
        "interaction": {
            "name": "Broadcast Subversive Signal", "category": "technology",
            "success_effect": {"type": "damage", "value": 20}, "failure_effect": {"type": "damage_party", "value": 5},
            "success_narrative": "You hijack the tower's broadcast system, hitting the boss with a debilitating psychic frequency!",
            "failure_narrative": "The signal is mistuned, causing painful neural feedback for the entire party!"
        }
    },
    {
        "name": "The Foundry of Defiance",
        "description": "In a geothermal-powered complex deep underground, the Foundry is where Chainbreakers are made and tech is weaponized.",
        "effect": {"type": "faction_modifier", "faction": "Chainbreakers", "category": "strength", "value": 20, "narrative": "On your home turf, your augments hum with raw power."},
        "interaction": {
            "name": "Vent Geothermal Steam", "category": "strength",
            "success_effect": {"type": "damage", "value": 30}, "failure_effect": {"type": "damage_party", "value": 6},
            "success_narrative": "You force open a pressure valve, scalding the boss with superheated geothermal steam!",
            "failure_narrative": "The valve ruptures violently, and the whole party is caught in the blast!"
        }
    },
    {
        "name": "The Oracle's Relay",
        "description": "A forgotten radio telescope complex repurposed by Nodewalkers to pierce the Overlords' data smog.",
        "effect": {"type": "faction_modifier", "faction": "Nodewalkers", "category": "technology", "value": 20, "narrative": "The Relay amplifies your connection to the blockchain's deepest truths."},
        "interaction": {
            "name": "Focus Relay Dish", "category": "technology",
            "success_effect": {"type": "damage", "value": 28}, "failure_effect": {"type": "damage_party", "value": 5},
            "success_narrative": "You focus the entire relay on the boss, bombarding it with a focused beam of cosmic data!",
            "failure_narrative": "The dish misaligns, and the energy beam sweeps across the party's position!"
        }
    },
    {
        "name": "BlissHaven",
        "description": "A self-contained habitat where privileged Neuralifes live in a monitored paradise, with every emotion logged on the blockchain.",
        "effect": {"type": "modifier", "category": "communication", "value": -10, "narrative": "In the panopticon of BlissHaven, every word is weighed and measured, making genuine connection difficult."},
        "interaction": {
            "name": "Crash Social Credit System", "category": "communication",
            "success_effect": {"type": "damage", "value": 22}, "failure_effect": {"type": "damage_party", "value": 4},
            "success_narrative": "You find an exploit in the social credit system, bankrupting the boss and causing their implants to fail!",
            "failure_narrative": "Your exploit is detected, and the system penalizes your entire party with a painful shock!"
        }
    }
]
