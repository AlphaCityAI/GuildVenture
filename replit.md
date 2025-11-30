# Alpha City RPG - Cyberpunk Telegram Bot Game

## Overview

Alpha City RPG is a text-based cyberpunk role-playing game delivered through Telegram. Players navigate a dystopian blockchain-controlled city, choose from three rebel factions (Nodewalkers, Coinbrokers, Glitchborn), and battle AI-controlled bosses in a gauntlet-style progression system. The game features turn-based combat, character progression, item collection, and AI-generated narrative content and images.

**Core Purpose:** Provide an immersive, AI-driven RPG experience within Telegram, combining strategic combat, character customization, and procedurally generated content.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Application Framework
- **Platform:** Python-based Telegram bot using `python-telegram-bot` library
- **Execution Environment:** Replit (async event loop already running)
- **AI Integration:** OpenAI API for content generation and DM narration
  - Chat completions for narrative, boss generation, and combat resolution
  - Image generation for visual content (currently encountering 400 errors)
- **State Management:** Replit DB (key-value store) for persistent game state per chat/group

**Design Pattern:** Event-driven architecture with callback handlers for player interactions. Game state is loaded/saved per message, enabling concurrent multi-player support across different Telegram chats.

### Combat System
- **Turn-based mechanics:** Players select abilities, AI resolves outcomes using pre-selected boss abilities
- **Damage types:** Multiple damage specialties (Enertech, Mercantile, Umbral, Kinetic, etc.) with boss-specific resistances/vulnerabilities
- **Ability system:** Class-based abilities with charge limits, direct damage, healing, and party-wide effects
- **Boss AI:** Template-based boss archetypes with procedurally generated names/descriptions and predefined ability sets

**Rationale:** Pre-defined ability templates ensure balanced combat while AI narration provides dynamic storytelling. This hybrid approach prevents AI from generating game-breaking mechanics.

### Progression System
- **XP-based leveling:** Exponential curve (base 500, multiplier 1.15)
- **Multiple XP sources:** Combat attempts, boss defeats, milestones, daily login, item collection
- **Rarity tiers:** 6-tier system (Salvage → Peerless) affecting item power and ability scaling
- **Titles:** Level-based titles (Newcomer → Alpha City Legend)

**Design Choice:** Generous XP rewards for participation encourage engagement even on failed attempts. Rarity-based scaling provides clear progression path without complex stat systems.

### Item & Character System
- **5 Equipment Slots:** Cranial, Chassis, Equipment, Mobility, Companion
- **7 Specialties:** Umbral, Blockchain, Kinetic, Enertech, Archon, Neural, Mechanical
- **Procedural Generation:** AI generates unique item/character names and backstories based on rarity, slot, and specialty constraints
- **Scaling Mechanics:** Rarity determines damage multipliers, healing multipliers, and ability charges

**Itemization System (item_traits.py):**
- **Passive Damage Bonuses:** Items grant % damage bonuses based on specialty (e.g., Umbral items boost Darkness damage). Bonuses scale with rarity: Salvage +5% → Peerless +30%
- **Active Abilities:** Each slot grants a unique ability type:
  - Cranial: Neural Strike (direct damage)
  - Chassis: Emergency Shield (self-heal)
  - Equipment: Tactical Advantage (roll bonus)
  - Mobility: Swift Strike (direct damage)
  - Companion: Pack Tactics (party heal)
- **Ability Charges:** Based on item durability (1-3 charges), reset when starting new gauntlet floors
- **Combat Integration:** Item damage bonuses applied multiplicatively after base damage calculation
- **Player Storage:** Profiles store `inventory` (list of items) and `equipped_items` (dict by slot)
- **Commands:** `/inventory` for viewing and equipping items

**Architecture Decision:** Item effects are deterministic (defined by rarity/specialty), while flavor text is AI-generated. This balances variety with game balance.

### Location System
- **Environmental effects:** Passive modifiers (e.g., +15 to technology rolls in tech-heavy locations)
- **Interactive elements:** Location-specific skill checks with success/failure outcomes
- **Faction bonuses:** Certain locations provide bonuses to specific factions

**Implementation:** Location data is static (defined in `locations.py`) but AI integrates them narratively during boss encounters.

### Game State Management
- **Per-chat isolation:** Each Telegram chat maintains separate game state in Replit DB
- **JSON serialization:** Game state stored as JSON with player stats, inventory, gauntlet progress, logs
- **Key structure:** `game_state_{chat_id}.json` format

**Challenges:** No traditional database schema; all relationships maintained in nested JSON structures. This limits complex queries but simplifies deployment on Replit.

### AI Prompting Strategy
- **System prompts:** Lore-aware DM persona with strict JSON response formats
- **Constraint enforcement:** Character limits on narratives (250-400 chars) to prevent Telegram message overflow
- **Validation rules:** Explicit rules for damage calculation, target selection, and response structure

**Problem Addressed:** LLMs can generate verbose or invalid responses. Strict JSON schemas with validation ensure parseable, game-compatible outputs.

## External Dependencies

### Third-Party APIs
- **OpenAI API:** 
  - Chat completions (GPT-4 Turbo) for DM narration, boss generation, item/character creation
  - Image generation (currently using `gpt-image-1` model, experiencing 400 errors)
  - **Error Handling:** Retry logic for rate limits, fallback to text-only on image failure

### Telegram Bot API
- **python-telegram-bot library:** Handles webhook/polling, inline keyboards, message formatting
- **Interaction Model:** Callback queries for button interactions, message handlers for text commands
- **Rate Limiting:** RetryAfter exception handling for Telegram API limits

### Replit Infrastructure
- **Replit DB:** Key-value database for persistent storage
  - No schema migrations required
  - Automatic scaling per Replit plan
  - **Limitation:** No complex queries or indexing; all filtering done in-memory
  
- **Environment Variables:**
  - `OPENAI_API_KEY`: Required for AI features
  - `TELEGRAM_TOKEN`: Bot authentication
  - `OPENAI_CHAT_MODEL`: Configurable chat model (default: gpt-4-turbo)
  - `OPENAI_IMAGE_MODEL`: Configurable image model (default: gpt-image-1)

### Known Issues
1. **Event Loop Conflict:** `asyncio.run()` called when loop already running (Replit environment issue)
2. **Image Generation Failures:** OpenAI image API returning 400 errors (model name or prompt issue)
3. **Concurrency:** No locking mechanism for simultaneous updates to same game state

### Future Extensibility Considerations
- Database migration path if moving to relational DB (player stats, inventory as tables)
- Webhook mode for Telegram (currently polling-based)
- Caching layer for frequently accessed game constants
- Rate limiting for AI API calls to control costs