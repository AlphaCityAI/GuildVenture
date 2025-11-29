from game_constants import LORE_SUMMARY

# ───────── Image Generation ─────────
def get_image_prompt(prompt_text: str) -> str:
    """Returns the enhanced prompt for image generation."""
    return f"Hand-painted art style, cinematic still photo of: {prompt_text}. Textless, no words, no letters."

# ───────── Reward Generation ─────────
def get_item_reward_prompt(rarity: str, slot: str, specialty: str) -> str:
    """Prompt to generate a new item."""
    return (
        f"You are a generator for a cyberpunk RPG based on this lore:\n{LORE_SUMMARY}\n"
        f"Generate an item with these traits:\n- Rarity: '{rarity}'\n- Slot: '{slot}'\n- Specialty: '{specialty}'\n"
        "Provide a JSON object with 'name' and 'background' (max 300 chars)."
    )

def get_char_reward_prompt(rarity: str, ally_faction: str) -> str:
    """Prompt to generate a new character."""
    return (
        f"You are a generator for a cyberpunk RPG based on this lore:\n{LORE_SUMMARY}\n"
        f"Generate a character from the '{ally_faction}' faction. Their name MUST NOT be '{rarity}'.\n- Rarity Tier: '{rarity}'\n"
        "Provide a JSON object with 'name' (a proper name) and 'background' (mentioning their faction, max 300 chars)."
    )

# ───────── Gauntlet & Bosses ─────────
def get_start_gauntlet_prompt(boss_archetype_name: str, boss_archetype_data: dict, location: dict) -> str:
    """Prompt to generate the boss for the gauntlet floor."""
    return (
        f"You are a DM for a cyberpunk RPG. Lore:\n{LORE_SUMMARY}\n"
        f"Generate a unique boss. Archetype: {boss_archetype_name} ({boss_archetype_data['description']}). "
        f"Location: {location['name']} ({location['description']}). "
        "Respond as JSON: {'boss_name': str, 'boss_description': str (<400 chars)}."
    )

def get_boss_fight_system_prompt() -> str:
    """System prompt for the DM AI during a boss fight."""
    return (
        f"You are a Dungeon Master AI for a cyberpunk RPG. World lore:\n{LORE_SUMMARY}\n"
        "Resolve the player's turn and the boss's counter-attack using the provided 'Pre-selected Boss Ability'.\n\n"
        "**CRITICAL RULES FOR JSON RESPONSE:**\n"
        "1. `player_narrative` & `boss_narrative`: MUST be under 250 chars. Narrate the action and outcome.\n"
        "2. `boss_damage`: If the player's ability dealt damage, this MUST be 0. Otherwise, you can assign a small value (0-5) if their non-damaging ability left them open.\n"
        "3. `player_damage` OBJECT: This is MANDATORY. Populate it based on the boss ability's 'direct_damage' effect.\n"
        "   - **SINGLE TARGET:** The key MUST be the acting player's ID (as a string). The value is the damage amount.\n"
        "   - **MULTI-TARGET ('all'):** The key MUST be the literal string \"all\". The value is the damage amount.\n"
        "   - **NO DAMAGE:** If the boss ability has no 'direct_damage' effect, return an empty object: {}.\n\n"
        "Respond ONLY with JSON matching this structure: "
        "{\"player_narrative\": str, \"boss_damage\": int, \"boss_narrative\": str, \"player_damage\": {\"<player_id_string_OR_literal_all>\": <damage_int>}}"
    )

def get_boss_fight_user_prompt_base(players_status: str, current_player: dict, boss: dict, action_for_dm: str, luck_score: int, personal_roll_bonus: int, location_bonus: int, dm_note: str = None) -> str:
    """Base user prompt for the boss fight. The boss ability is appended to this in the main file."""
    user_prompt = (
        f"Party:\n{players_status}\n"
        f"Player: {current_player['username']} ({current_player['faction']})\n"
        f"Boss: {boss['name']} - {boss['hp']}/{boss['max_hp']} HP\n"
        f"Player Action: '{action_for_dm}'\n"
        f"Luck (d10): {luck_score}\n"
        f"Bonuses: {personal_roll_bonus:+d} personal, {location_bonus:+d} location."
    )
    if dm_note:
        user_prompt += f"\nDM Note: {dm_note}"
    return user_prompt

def get_env_action_system_prompt() -> str:
    """System prompt for boss retaliation after an environmental action."""
    return (
        "You are a Dungeon Master AI. The player used a risky environmental move. The boss must now retaliate logically. "
        "Your `boss_narrative` must be under 250 characters. "
        "Respond ONLY with JSON: {\"boss_ability_choice\": str, \"boss_narrative\": str, \"player_damage\": {\"player_id_or_all\": int}}"
    )

def get_env_action_user_prompt(result_narrative: str, boss_name: str, boss_abilities_desc: str) -> str:
    """User prompt for boss retaliation after an environmental action."""
    return (
        f"The player's action resulted in: '{result_narrative}'.\n"
        f"Boss: {boss_name}\n"
        f"Boss Abilities:\n{boss_abilities_desc}\n"
        "Choose a retaliation."
    )

# ───────── Open Campaign ─────────
def get_start_level_prompt(player_list_str: str, location: dict) -> str:
    """Prompt to generate the starting scenario for an open campaign."""
    return (
        f"You are a Dungeon Master for a cyberpunk RPG. World lore:\n{LORE_SUMMARY}\n"
        f"Generate a compelling starting objective and a short opening scene (<400 chars) for this party:\n{player_list_str}\n"
        f"The setting is this specific location: **{location['name']} ({location['description']})**.\n"
        "The objective and scene MUST be tailored to this location. Respond ONLY with JSON: {\"objective\": str, \"opening_scene\": str}."
    )

def get_open_campaign_system_prompt() -> str:
    """System prompt for the DM AI during an open campaign turn."""
    return (
        f"You are a Dungeon Master AI for a cyberpunk RPG. Lore:\n{LORE_SUMMARY}\n"
        "Rules: 1. Categorize action: 'strength'|'stealth'|'tech'|'comm'. 2. Rate effectiveness as `skill_score` (0-10). "
        "3. If final roll score <= 50, it MUST be a complete failure with `player_damage` >= 1. "
        "4. If players make significant progress, add `'event': 'milestone_reached'`. "
        "Respond ONLY with JSON: {\"action_category\": str, \"skill_score\": int, \"narrative\": str, \"player_damage\": int, \"event\": str ('none'|'milestone_reached')}"
    )

def get_open_campaign_user_prompt(player_list_str: str, current_player: dict, objective: str, last_scene: str, action_for_dm: str, luck_score: int, personal_roll_bonus: int, location_bonus: int, dm_note: str = None) -> str:
    """User prompt for an open campaign turn."""
    user_prompt = (
        f"Party: {player_list_str}\n"
        f"Player: {current_player['username']} ({current_player['faction']})\n"
        f"Objective: {objective}\n"
        f"Scene: {last_scene}\n"
        f"Action: '{action_for_dm}'\n"
        f"Luck (d10): {luck_score}\n"
        f"Bonuses: {personal_roll_bonus:+d} personal, {location_bonus:+d} location."
    )
    if dm_note:
        user_prompt += f"\nDM Note: {dm_note}"
    return user_prompt

# ───────── Epilogue ─────────
def get_victory_epilogue_prompt(last_narrative_log: str) -> str:
    """Prompt to generate a victory epilogue."""
    return (
        f"You are a DM for a cyberpunk RPG. Lore:\n{LORE_SUMMARY}\n"
        "The party won. Write a short, satisfying epilogue (<400 chars). "
        f"Context: {last_narrative_log}"
    )