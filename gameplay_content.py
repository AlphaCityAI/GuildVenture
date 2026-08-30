"""Validated AI-authored gameplay contracts and explicitly labeled offline fallbacks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["technology", "communication", "stealth", "strength"]
Specialty = Literal["Umbral", "Blockchain", "Kinetic", "Enertech", "Archon", "Neural", "Mechanical"]


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SupportSkill(Contract):
    name: str = Field(min_length=1, max_length=45)
    description: str = Field(min_length=1, max_length=160)
    kind: Literal["strike", "heal", "focus"]
    value: int = Field(ge=1, le=6)
    category: Category


class AllyDesign(Contract):
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(min_length=1, max_length=300)
    support: SupportSkill


class Talent(Contract):
    name: str = Field(min_length=1, max_length=45)
    description: str = Field(min_length=1, max_length=160)
    kind: Literal["vitality", "precision", "restoration", "force"]
    value: int = Field(ge=1, le=3)


class TalentOffers(Contract):
    offers: list[Talent] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def distinct_choices(self):
        if len({offer.kind for offer in self.offers}) != len(self.offers):
            raise ValueError("Talent offers must have distinct effects")
        return self


class ForgeDesign(Contract):
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(min_length=1, max_length=300)
    specialty: Specialty
    ability: SupportSkill


class BossMove(Contract):
    name: str = Field(min_length=1, max_length=45)
    telegraph: str = Field(min_length=1, max_length=160)
    kind: Literal["damage", "heal", "debuff"]
    power: int = Field(ge=1, le=8)
    target: Literal["actor", "party", "self"]
    counter_category: Category

    @model_validator(mode="after")
    def target_and_budget(self):
        if (self.kind == "heal") != (self.target == "self"):
            raise ValueError("Only healing targets the boss itself")
        if self.target == "party" and self.power > 4:
            raise ValueError("Party-wide effects have a lower budget")
        return self


class EncounterDesign(Contract):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    moves: list[BossMove] = Field(min_length=2, max_length=3)
    phase_name: str = Field(min_length=1, max_length=45)
    phase_telegraph: str = Field(min_length=1, max_length=160)
    phase_threshold: int = Field(ge=35, le=60)
    phase_power_bonus: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def can_finish_fight(self):
        if not any(move.kind == "damage" for move in self.moves):
            raise ValueError("An encounter needs a damaging move")
        return self


class Approach(Contract):
    label: str = Field(min_length=1, max_length=45)
    detail: str = Field(min_length=1, max_length=160)
    category: Category


class Chapter(Contract):
    title: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=180)
    opening_scene: str = Field(min_length=1, max_length=400)
    approaches: list[Approach] = Field(min_length=2, max_length=2)


class CampaignPlan(Contract):
    title: str = Field(min_length=1, max_length=80)
    premise: str = Field(min_length=1, max_length=300)
    chapters: list[Chapter] = Field(min_length=3, max_length=3)


class VictoryStory(Contract):
    text: str = Field(min_length=1, max_length=600)


def fallback_support(ally):
    category = {
        "Nodewalker": "technology",
        "Singularity": "technology",
        "Coinbroker": "communication",
        "Overlord": "communication",
        "Chainbreaker": "strength",
    }.get(ally.get("faction"), "stealth")
    kind = {"technology": "focus", "communication": "heal"}.get(category, "strike")
    return SupportSkill(
        name={"focus": "Signal Assist", "heal": "Field Aid", "strike": "Covering Strike"}[kind],
        description="A trusted contact lends a hand.",
        kind=kind,
        value=3,
        category=category,
    ).model_dump()


def fallback_talents(profile=None):
    offers = [
        Talent(
            name="Street Endurance", description="A tougher frame for the next encounter.", kind="vitality", value=3
        ),
        Talent(name="Steady Signal", description="Improve eligible rolls.", kind="precision", value=2),
        Talent(name="Precise Force", description="Make damaging attacks count.", kind="force", value=1),
        Talent(name="Field Training", description="Improve effective healing.", kind="restoration", value=2),
    ]
    caps = {"vitality": 6, "precision": 5, "restoration": 3, "force": 2}
    totals = {kind: sum(t["value"] for t in (profile or {}).get("talents", []) if t["kind"] == kind) for kind in caps}
    return TalentOffers(offers=[t for t in offers if totals[t.kind] < caps[t.kind]][:3])


def useful_talents(profile, design):
    caps = {"vitality": 6, "precision": 5, "restoration": 3, "force": 2}
    totals = {kind: sum(t["value"] for t in profile.get("talents", []) if t["kind"] == kind) for kind in caps}
    offers = [t for t in design.offers if totals[t.kind] < caps[t.kind]]
    return TalentOffers(offers=offers) if len(offers) >= 2 else None


def fallback_encounter(boss, rng):
    category = rng.choice(["technology", "communication", "stealth", "strength"])
    return EncounterDesign(
        name=boss["name"],
        description=boss["description"],
        moves=[
            BossMove(
                name="Focused Assault",
                telegraph="A targeting beam settles on the acting player.",
                kind="damage",
                power=rng.randint(3, 6),
                target="actor",
                counter_category=category,
            ),
            BossMove(
                name="Disruption Wave",
                telegraph="A widening pulse threatens the entire party.",
                kind="debuff",
                power=3,
                target="party",
                counter_category="technology",
            ),
            BossMove(
                name="Sweeping Strike",
                telegraph="The weapon pivots across the party's position.",
                kind="damage",
                power=2,
                target="party",
                counter_category="stealth",
            ),
        ],
        phase_name="Overdrive",
        phase_telegraph="Damaged limiters release reserve power.",
        phase_threshold=50,
        phase_power_bonus=1,
    )


def fallback_campaign(location, rng):
    target = rng.choice(["an encrypted witness archive", "a stolen identity core", "a hidden transit key"])
    chapters = []
    for title, objective in [
        ("Find the trail", f"Locate the courier carrying {target}."),
        ("Break the seal", f"Recover {target} from its guarded vault."),
        ("Leave no chains", f"Deliver {target} to the Underground and escape pursuit."),
    ]:
        chapters.append(
            Chapter(
                title=title,
                objective=objective,
                opening_scene=f"In {location['name']}, the next lead waits behind watchful eyes.",
                approaches=[
                    Approach(label="Ghost route", detail="Use concealment and hidden paths.", category="stealth"),
                    Approach(label="Network route", detail="Manipulate the security network.", category="technology"),
                ],
            )
        )
    return CampaignPlan(title="An Alpha City Extraction", premise=f"The Underground needs {target}.", chapters=chapters)
