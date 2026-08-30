"""Owner-bound personal ally, talent, and workshop flows used by BotService."""

from __future__ import annotations

import datetime as dt
import uuid

import gameplay_content as content
import presentation as ui
import profiles
from profiles import InvalidAction


class PlayerFeatures:
    def personal_view(self, update, section, page=0):
        key = (update.effective_user.id, section)
        view = {
            "nonce": uuid.uuid4().hex[:8],
            "page": page,
            "chat": update.effective_chat.id,
            "thread": update.effective_message.message_thread_id,
        }
        self.personal_views[key] = view
        self.personal_views.move_to_end(key)
        while len(self.personal_views) > 2000:
            self.personal_views.popitem(last=False)
        return view

    async def show_allies(self, update, context, profile, page=0):
        allies = list(reversed(profile["collectibles"]))
        pages = max(1, (len(allies) + 3) // 4)
        page = min(max(0, page), pages - 1)
        view = self.personal_view(update, "allies", page)

        def control(label, action, argument=""):
            return ui.button(label, f"p:{update.effective_user.id}:{view['nonce']}:allies:{action}:{argument}")

        lines = [
            f"Ally roster — {len(allies)} recruits · page {page + 1}/{pages}",
            "One ally deploys at the next floor/chapter. Each support action consumes your turn. "
            "Bond grows on completed encounters; ranks improve at 3 and 6 completions.",
        ]
        rows = []
        for ally in allies[page * 4 : (page + 1) * 4]:
            selected = profile["active_ally_id"] == ally["id"]
            lines.append(("Selected for deployment\n" if selected else "") + ui.ally_text(ally))
            rows.append([control(("Selected: " if selected else "Deploy: ") + ally["name"][:35], "deploy", ally["id"])])
        if not allies:
            lines.append("Recruit an ally through Hire Help or a banked reward.")
        rows += [
            [control("No active ally", "deploy", "none")],
            [control("Previous", "page", str(page - 1)), control("Next", "page", str(page + 1))],
        ]
        return await self.say(update, context, "\n\n".join(lines), rows)

    async def show_progression(self, update, context, profile):
        milestone = profiles.eligible_talent(profile)
        if milestone is not None and str(milestone) not in profile["talent_offers"]:
            design = await self.ai.talents(profile, milestone)
            design = content.useful_talents(profile, design) if design else None
            source = "AI" if design else "fallback"
            design = design or content.fallback_talents(profile)
            saved = await self.repo.mutate_profile(
                update.effective_user.id,
                lambda p: profiles.save_talent_offers(p, milestone, design.model_dump()["offers"], source),
                f"talent-offers:{milestone}",
            )
            profile = saved.profile
            if profiles.eligible_talent(profile) != milestone:
                return await self.show_progression(update, context, profile)
        view = self.personal_view(update, "talents")
        bonuses = profiles.talent_bonuses(profile)
        lines = [
            f"Career progression — Level {profile['level']} · {profile['title']}",
            f"Active bonuses at next encounter: +{bonuses['hp']} max HP, +{bonuses['damage']} damage, "
            f"+{bonuses['heal']} healing, +{bonuses['roll']} roll points.",
            "Choose one permanent talent at levels 2, 5, and 10. Combined caps: HP +6, damage +2, healing +3, roll points +10.",
        ]
        lines += [f"Level {t['milestone']}: {t['name']} ({t['kind']})" for t in profile["talents"]]
        rows = []
        if milestone is not None:
            offer = profile["talent_offers"][str(milestone)]
            lines.append(f"Level {milestone} offers · {offer['source']} design. These choices are saved.")
            for index, talent in enumerate(offer["offers"]):
                after = profiles.talent_bonuses({"talents": profile["talents"] + [talent]})
                change = ", ".join(
                    f"{key} +{after[key] - bonuses[key]}" for key in bonuses if after[key] > bonuses[key]
                )
                lines.append(f"{talent['name']} — {change}\n{talent['description']}")
                rows.append(
                    [
                        ui.button(
                            "Choose " + talent["name"],
                            f"p:{update.effective_user.id}:{view['nonce']}:talents:choose:{milestone}.{index}",
                        )
                    ]
                )
        else:
            next_level = next((n for n in profiles.TALENT_LEVELS if n > profile["level"]), None)
            lines.append(f"Next talent unlock: level {next_level}." if next_level else "All career talents selected.")
        return await self.say(update, context, "\n\n".join(lines), rows)

    async def personal_callback(self, update, context, data):
        try:
            _, owner, nonce, section, action, argument = data.split(":", 5)
        except ValueError as exc:
            raise InvalidAction("Invalid personal control. Open /allies or /progression.") from exc
        user = update.effective_user
        view = self.personal_views.get((user.id, section))
        if (
            owner != str(user.id)
            or not view
            or view["nonce"] != nonce
            or view["chat"] != update.effective_chat.id
            or view["thread"] != update.effective_message.message_thread_id
        ):
            raise InvalidAction("These personal controls expired or belong to another player. Open your own menu.")
        if section == "allies":
            if action == "deploy":
                saved = await self.repo.mutate_profile(
                    user.id, lambda p: profiles.deploy_ally(p, argument), f"deploy:{nonce}:{argument}"
                )
                profile = saved.profile
            elif action == "page":
                profile = await self.profile(user)
                try:
                    view["page"] = int(argument)
                except ValueError as exc:
                    raise InvalidAction("Invalid roster page.") from exc
            else:
                raise InvalidAction("Unknown roster action.")
            return await self.show_allies(update, context, profile, view["page"])
        if section == "talents" and action == "choose":
            try:
                milestone, index = map(int, argument.split("."))
            except ValueError as exc:
                raise InvalidAction("Invalid talent choice.") from exc
            saved = await self.repo.mutate_profile(
                user.id, lambda p: profiles.choose_talent(p, milestone, index), f"talent:{milestone}"
            )
            await self.say(
                update,
                context,
                f"Talent saved: {saved.result['name']}. Applies at the next encounter; ready again if your loadout changed.",
            )
            return await self.show_progression(update, context, saved.profile)
        raise InvalidAction("Unknown personal action.")

    async def request_blueprint(self, update, context, item_id):
        profile = await self.profile(update.effective_user)
        source = next((i for i in profile["inventory"] if i["id"] == item_id), None)
        if not source:
            raise InvalidAction("Upgrade a backpack item from /inventory.")
        prior = profile["craft_quote"]
        if prior and prior["fingerprint"] == profiles.fingerprint(source):
            return await self.show_craft(update, context, profile)
        timestamp = dt.datetime.now(dt.timezone.utc).timestamp()
        if timestamp - profile.get("last_blueprint_at", 0) < 30:
            raise InvalidAction("Wait 30 seconds between new blueprints. Use /craft for your saved preview.")
        rarity, _ = profiles.forge_terms(source)
        design = await self.ai.forge(source, rarity)
        output = profiles.forged_item(source, design.model_dump() if design else None)
        saved = await self.repo.mutate_profile(
            update.effective_user.id,
            lambda p: profiles.save_craft_quote(
                p, source, output, uuid.uuid4().hex[:16], timestamp, "AI" if design else "fallback"
            ),
        )
        return await self.show_craft(update, context, saved.profile)

    async def show_craft(self, update, context, profile):
        lines = [
            f"Workshop — {profile['materials']} materials",
            "Salvage backpack items in /inventory, or request an upgrade blueprint from an item's details.",
        ]
        rows = []
        quote = profile["craft_quote"]
        if quote:
            lines += [
                f"Saved {quote['design_source']} blueprint: consume {quote['source_name']} + {quote['cost']} materials.",
                "Output:\n" + ui.item_text(quote["output"]),
                quote["output"]["background"],
                "Confirm only if you want this exact upgrade. The source must still be in your backpack.",
            ]
            rows = [
                [
                    ui.button(
                        f"Craft — spend {quote['cost']} materials",
                        f"c:{update.effective_user.id}:{quote['id']}:confirm",
                    )
                ]
            ]
            rows.append(
                [
                    ui.button(
                        "Cancel blueprint (keep item/materials)", f"c:{update.effective_user.id}:{quote['id']}:cancel"
                    )
                ]
            )
        elif profile.get("last_craft_receipt"):
            receipt = profile["last_craft_receipt"]
            lines += [
                f"Last saved crafting receipt: {receipt['spent']} materials spent once.",
                ui.item_text(receipt["item"]),
            ]
        return await self.say(update, context, "\n\n".join(lines), rows)

    async def craft_callback(self, update, context, data):
        try:
            _, owner, quote_id, action = data.split(":")
        except ValueError as exc:
            raise InvalidAction("Invalid blueprint control. Open /craft.") from exc
        if owner != str(update.effective_user.id) or len(quote_id) != 16 or action not in {"confirm", "cancel"}:
            raise InvalidAction("This blueprint belongs to another player or has expired. Open /craft.")
        if action == "cancel":
            saved = await self.repo.mutate_profile(
                update.effective_user.id, lambda p: profiles.cancel_craft(p, quote_id), f"craft-cancel:{quote_id}"
            )
            await self.say(update, context, "Blueprint cancelled. Your item and materials are unchanged.")
            return await self.show_craft(update, context, saved.profile)
        saved = await self.repo.mutate_profile(
            update.effective_user.id, lambda p: profiles.complete_craft(p, quote_id), f"craft:{quote_id}"
        )
        await self.say(
            update,
            context,
            f"{'Crafted' if saved.applied else 'Saved crafting receipt'}: {saved.result['item']['name']}. "
            f"{saved.result['spent']} materials spent once. Saved in /inventory; equip it before the next encounter.",
        )
        return await self.show_craft(update, context, saved.profile)
