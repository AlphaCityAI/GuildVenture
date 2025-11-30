# Victory Cinematics System - Specification

## Overview
Enhance the victory experience by showing player contributions, faction-specific language, boss-specific death narratives, and winning streaks. Transform victory from a simple end state into a memorable cinematic moment.

## 1. Data Tracking During Combat

### Track per Player
- `damage_dealt` - total damage dealt to boss
- `damage_healed` - total healing provided to party
- `abilities_used` - list of ability names used (for combo detection)
- `critical_turns` - turns where ability outcome was "Tremendous Success"
- `turns_taken` - number of actions this player took

### Track per Boss Fight
- `total_combat_turns` - how many rounds the fight lasted
- `player_contributions` - dict mapping player_id to stats above
- `winning_streak` - current consecutive floor count (reset on loss, increment on win)
- `total_damage_by_damage_type` - breakdown: {"Darkness": 150, "Energy": 200, ...}

### Storage
Add to `state` dict during combat:
```json
"combat_stats": {
  "player_123": {
    "damage_dealt": 450,
    "damage_healed": 200,
    "abilities_used": ["Shadow Strike", "Neural Strike", "Pack Tactics"],
    "critical_turns": 2,
    "turns_taken": 5
  },
  "player_456": {...}
}
```

## 2. UI/Narrative Flow

### Step 1: Boss Death Scene (Archetype-Specific)
*Currently:* Generic "you've won" message
*New:* Boss archetype determines death narrative style

**Examples by Archetype:**
- **Overdrive** → "Systems cascade failure... the AI spasms and goes dark"
- **Sentry** → "Defensive protocols collapse, the mechanical guardian falls silent"
- **Executioner** → "The hunter becomes the hunted. You've turned the tables"
- **Cipher** → "The code shatters, fragmented data dissolves into the network"

Implementation: Add `death_scene_prompt` to each archetype in `bosstraits.py`

---

### Step 2: Player Contributions Highlight
*New component* - Show brief recap of what each player did

**Format:**
```
🏆 VICTORY! Generating battle report...

⚔️ *Battle Contributions*
• @Player1: 450 damage (Most Lethal)
• @Player2: 200 healing (Field Medic) 
• @Player3: Had 2 critical hits (MVP Moment)
```

**Categories (choose top 2-3):**
- Most Lethal (highest damage)
- Field Medic (highest healing)
- Relentless (most turns taken)
- Clutch Player (most critical successes)
- Combo Master (used 5+ unique abilities)

---

### Step 3: Faction-Specific Victory Language
*Current:* Generic victory epilogue
*New:* Tailor epilogue to faction composition

**If majority Nodewalkers:**
"The node network crackles with triumph. You've successfully infiltrated deep. Their secrets are yours now."

**If majority Coinbrokers:**
"The score is massive. Their credit flows into your accounts. Alpha City trembles at your financial prowess."

**If majority Glitchborn:**
"Reality glitches in your favor. The system stutters. You've rewritten the rules of engagement."

**If mixed faction group:**
"The unlikely alliance has proven unstoppable. Underground, Overcity—none can stand against you."

---

### Step 4: Winning Streak Display
*New component* - Show momentum

**Format:**
```
🔥 *STREAK: 7 FLOORS UNDEFEATED*
Your run bonus: +14 to next roll

[Continue to Floor 8] [Bank & Reward]
```

If streak = 1 (first floor), don't show this.

---

### Step 5: Reward Cinematics
*Current:* "You chose reward type. Rolling..."
*New:* Flavor text → then reveal

**For items (example):**
```
You sift through the wreckage. A crystalline module glints in the neon light.

🎲 Rolling the dice...
Roll: 67 → 🟣 *Black Market* item acquired!

*Psionic Lattice*
🔩 Slot: Cranial
✨ Specialty: Neural
🛠️ Durability: 3/3
📈 Passive: +25% Neural damage
⚡ Ability: Cognitive Overload (2 charges)
```

**For allies (example):**
```
A figure emerges from the smoke. They lower their weapon.

🎲 Rolling the dice...
Roll: 82 → 🟡 *Node-Forged* ally recruited!

*Cipher* (Nodewalker, Level 18)
Encoded data streams, mercurial presence...
```

---

## 3. Architecture Changes

### File: main.py - `run_epilogue()`
**Current flow:**
```
Boss defeated → Victory message → Prompt for epilogue → Show reward choice
```

**New flow:**
```
Boss defeated 
  → Boss death scene (AI generated) 
  → Player contributions recap
  → Faction victory language (AI generated)
  → Winning streak display
  → Reward cinematics
  → Reward choice menu
```

### New Helper Functions
1. `calculate_player_mvp_categories()` - determine top player stats
2. `get_boss_death_narrative()` - AI request for archetype-specific death
3. `get_faction_victory_narrative()` - AI request for faction flavor
4. `format_winning_streak()` - format streak display

### Combat Tracking Integration
- Initialize `combat_stats` in `start_gauntlet_floor()`
- Track stats in `handle_player_action()` when damage/healing dealt
- Track `total_combat_turns` by incrementing after boss turn
- Save stats to state after each action

---

## 4. AI Prompting

### Boss Death Scene Prompt
```python
{
  "role": "user",
  "content": f"""You are a grimdark cyberpunk narrator.
  
Boss archetype: {archetype_name}
Boss name: {boss_name}
Location: {location_name}

Generate a 2-3 sentence death scene for this boss. Be atmospheric and specific to the archetype.
Keep it under 100 characters. Do NOT say "You defeated" or address the player directly.
Examples: "Systems cascade. The AI spasms and dies." or "The guardian's final protocol: shutdown."

Respond with ONLY the narrative text, no JSON."""
}
```

### Faction Victory Narrative
```python
{
  "role": "user",
  "content": f"""You are a grimdark cyberpunk narrator celebrating a faction's victory.

Faction composition: {faction_breakdown}  # e.g., "2x Nodewalker, 1x Coinbroker"
Floor defeated: {floor_number}
Boss name: {boss_name}

Generate a 2-3 sentence victory narrative specific to their faction identity.
Emphasize their faction's values: Nodewalkers = infiltration/hacking, Coinbrokers = wealth/power, Glitchborn = chaos/freedom.
Keep under 120 characters.

Respond with ONLY the narrative text, no JSON."""
}
```

---

## 5. Example Flow - Player Experience

```
[Boss defeated]

💀 *Systems cascade. The AI spasms and goes dark.*

🏆 *VICTORY!*

⚔️ *Battle Contributions*
• @Alice: 520 damage (Most Lethal) 
• @Bob: 180 healing (Field Medic)
• @Charlie: 3 critical hits (MVP Moment)

🎭 *The unlikely alliance has proven unstoppable. Underground, Overcity—none can stand against you.*

🔥 *STREAK: 5 FLOORS UNDEFEATED*
Your run bonus: +10 to next roll

---

[System generates reward]

💎 You retrieve a data chip from the wreckage. It pulses with residual energy.

🎲 Rolling the dice...
Roll: 58 +10 (streak) = 68 → 🔵 *Street Mod* item

*Encryption Breaker*
Slot: Equipment | Specialty: Blockchain | Durability: 2/3
📈 +15% Blockchain damage
⚡ Tactical Advantage (1 charge): +8 to next roll

---

[Button menu]
🚀 Ascend to Floor 6  |  💰 Bank & Choose Reward
```

---

## 6. Implementation Phases

### Phase 1: Data Tracking (Priority)
- [ ] Add `combat_stats` to state init
- [ ] Track damage/healing in `handle_player_action()`
- [ ] Track winning streak in gauntlet flow

### Phase 2: Boss Death Scenes
- [ ] Add `death_scene_prompt` to each boss archetype
- [ ] Implement `get_boss_death_narrative()` AI call
- [ ] Update `run_epilogue()` to display death scene

### Phase 3: Player Contributions
- [ ] Implement `calculate_player_mvp_categories()`
- [ ] Format MVP display
- [ ] Integrate into `run_epilogue()`

### Phase 4: Faction Victory + Reward Cinematics
- [ ] Implement `get_faction_victory_narrative()`
- [ ] Add reward flavor text before reveal
- [ ] Add winning streak display

---

## 7. Technical Considerations

### Performance
- Each victory now makes 2-3 AI calls (death scene, faction narrative, reward cinematics)
- Cost: ~3-4 cents per victory at current API rates
- Mitigation: Could cache faction narratives per session if needed

### Telegram Message Limits
- Total message length still under 4090 chars (Telegram limit)
- Death scene + contributions + faction narrative = ~300-400 chars, well within limits

### Backward Compatibility
- Existing gauntlet runs won't have `combat_stats` - need graceful handling (default empty dicts)
- Winning streak calculation works for any run (bases on `gauntlet_bonus_defeated`)

---

## 8. Success Metrics
- Victory feels more rewarding and cinematic
- Players understand their contribution to team success
- Streaks create psychological momentum/investment
- Replay value increases (want to see different faction narratives, MVPs)
