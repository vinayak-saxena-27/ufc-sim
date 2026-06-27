from __future__ import annotations

import random
from dataclasses import dataclass

# ─── Academy entity ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Academy:
    name: str
    template: str  # must match a key in TEMPLATES
    nudges: dict   # {attr_name: float} — subset of ATTR_NAMES

    def get_nudge(self, attr: str) -> float:
        return self.nudges.get(attr, 0.0)


# ─── Academy definitions ──────────────────────────────────────────────────────
# 3 academies per region × 5 regions = 15 academies.
# Nudge constants are intentionally small (±3–6 pts relative to template means
# that themselves span ±22 pts) — each academy is a flavour variant within its
# region, not a wholesale change of profile. Missing attrs default to 0.

# ── Dagestan / Sambo ──────────────────────────────────────────────────────────
# Template anchors: wrestling +22, clinch +20, cardio +22, chin +16, boxing –15
_MAKHACHKALA_SAMBO = Academy(
    name="Makhachkala Combat Sambo Centre",
    template="dagestan_sambo",
    nudges={
        "wrestling": +4.0,  # pure takedown shop — highest TD completion
        "clinch":    +3.0,
    },
)

_ANZHI_MMA = Academy(
    name="Anzhi MMA Factory",
    template="dagestan_sambo",
    nudges={
        "cardio": +4.0,  # grinding-pace specialists, known for 25-min conditioning
        "chin":   +3.0,
    },
)

_EAGLE_ATHLETIC = Academy(
    name="Eagle Athletic Club",
    template="dagestan_sambo",
    nudges={
        "fight_iq": +5.0,  # cerebral positional control, reads pace changes well
        "bjj":      +3.0,  # better ground retention than typical Dagestan base
    },
)

# ── American Wrestling ─────────────────────────────────────────────────────────
# Template anchors: wrestling +22, athleticism +20, cardio +20, boxing +8, bjj –12
_ALLIANCE_MMA = Academy(
    name="Alliance MMA San Diego",
    template="american_wrestling",
    nudges={
        "boxing":   +5.0,  # best striking integration of the three camps
        "fight_iq": +3.0,
    },
)

_AMERICAN_TOP_TEAM = Academy(
    name="American Top Team Florida",
    template="american_wrestling",
    nudges={
        "power":      +5.0,  # known for finishing power and explosive style
        "athleticism": +3.0,
    },
)

_ELEVATION_FIGHT_TEAM = Academy(
    name="Elevation Fight Team Colorado",
    template="american_wrestling",
    nudges={
        "cardio":    +5.0,  # high-altitude conditioning programme
        "wrestling": +3.0,
    },
)

# ── Brazilian ─────────────────────────────────────────────────────────────────
# Template anchors: bjj +25, fight_iq +22, kickboxing +8, wrestling 0
_NOVA_UNIAO = Academy(
    name="Nova Uniao Rio",
    template="brazilian",
    nudges={
        "bjj":   +4.0,  # BJJ / boxing hybrid — produces well-rounded ground attackers
        "boxing": +3.0,
    },
)

_CHUTE_BOXE = Academy(
    name="Chute Boxe Academy",
    template="brazilian",
    nudges={
        "kickboxing": +5.0,  # aggressive Muay Thai-influenced brawling style
        "power":      +4.0,
    },
)

_GRACIE_BARRA_SP = Academy(
    name="Gracie Barra Sao Paulo",
    template="brazilian",
    nudges={
        "bjj":      +6.0,  # pure submission hunting; deepest ground repertoire
        "fight_iq": +3.0,
    },
)

# ── Muay Thai / Thailand ──────────────────────────────────────────────────────
# Template anchors: kickboxing +25, clinch +22, chin +18, boxing +10, wrestling –18
_FAIRTEX_CENTER = Academy(
    name="Fairtex Training Center",
    template="muay_thai",
    nudges={
        "kickboxing": +4.0,  # technical precision, angle work
        "clinch":     +3.0,
    },
)

_TIGER_MUAY_THAI = Academy(
    name="Tiger Muay Thai Phuket",
    template="muay_thai",
    nudges={
        "power":  +4.0,  # power striking emphasis; international boxing coaching
        "boxing": +3.0,
    },
)

_LANNA_MUAY_THAI = Academy(
    name="Lanna Muay Thai Chiang Mai",
    template="muay_thai",
    nudges={
        "cardio": +5.0,  # traditional conditioning regimen, long camp cycles
        "chin":   +3.0,
    },
)

# ── Southeast Asia Mixed ──────────────────────────────────────────────────────
# Template anchors: kickboxing +18, athleticism +18, bjj +15, wrestling –12, power –10
_EVOLVE_MMA = Academy(
    name="Evolve MMA Singapore",
    template="sea_mixed",
    nudges={
        "fight_iq": +5.0,  # champion coaching roster; highest tactical diversity in region
        "bjj":      +3.0,
    },
)

_TEAM_LAKAY = Academy(
    name="Team Lakay Philippines",
    template="sea_mixed",
    nudges={
        "athleticism": +5.0,  # acrobatic aggressive style, explosive fighters
        "kickboxing":  +3.0,
    },
)

_ELORDE_COMBAT = Academy(
    name="Elorde Combat Sports Manila",
    template="sea_mixed",
    nudges={
        "clinch": +4.0,  # Muay Thai–influenced clinch integration
        "cardio": +3.0,
    },
)


ACADEMIES: dict[str, list[Academy]] = {
    "dagestan_sambo":     [_MAKHACHKALA_SAMBO, _ANZHI_MMA, _EAGLE_ATHLETIC],
    "american_wrestling": [_ALLIANCE_MMA, _AMERICAN_TOP_TEAM, _ELEVATION_FIGHT_TEAM],
    "brazilian":          [_NOVA_UNIAO, _CHUTE_BOXE, _GRACIE_BARRA_SP],
    "muay_thai":          [_FAIRTEX_CENTER, _TIGER_MUAY_THAI, _LANNA_MUAY_THAI],
    "sea_mixed":          [_EVOLVE_MMA, _TEAM_LAKAY, _ELORDE_COMBAT],
}


def pick_academy(template_name: str) -> Academy:
    """Uniform random selection among a region's academies.
    Session 5a: no quality/reputation weighting — that's 5c.
    """
    return random.choice(ACADEMIES[template_name])


# ─── Per-region name pools ─────────────────────────────────────────────────────
# Shared at REGION level, not per-academy — multiple academies within a region
# draw from the same cultural pool, which is realistic.
#
# Pool sizes: ~32 first × ~22–26 last ≈ 700–830 combos per region.
# Uniqueness is enforced per-region via _used_names: regional_name() retries on
# collision and raises if the pool is somehow exhausted. Call reset_name_registry()
# at the start of each fresh simulation (generate_all_tiers / generate_population
# do this automatically).

_NAMES: dict[str, dict[str, list[str]]] = {
    "dagestan_sambo": {
        "first": [
            "Khabib", "Islam", "Umar", "Magomed", "Abubakar", "Shamil", "Zaur",
            "Akhmat", "Ruslan", "Zelim", "Zurab", "Hasan", "Aslan", "Eldar",
            "Makhach", "Rashid", "Said", "Musa", "Alibek", "Suliman", "Husein",
            "Abdulmanap", "Khasan", "Rizvan", "Kurban", "Harun", "Bilal",
            "Ramzan", "Bekhan", "Daud", "Artur", "Timur",
        ],
        "last": [
            "Nurmagomedov", "Makhachev", "Ankalaev", "Khasbulaev", "Ulanbekov",
            "Guseinov", "Abdulvakhidov", "Aliev", "Gadzhiev", "Osmanov",
            "Musaev", "Mamedov", "Saidov", "Khalidov", "Yusupov", "Merabdze",
            "Bakiev", "Gaitaev", "Chimaev", "Dzhitiev", "Magomedov",
            "Kurbanov", "Bazaev", "Gasanov",
        ],
    },
    "american_wrestling": {
        "first": [
            "Dustin", "Justin", "Tony", "Colby", "Gilbert", "Sean", "Cory",
            "Brandon", "Marlon", "Derek", "Marcus", "Tyrone", "Jake", "Ryan",
            "Kyle", "Chad", "Brett", "Jordan", "Mike", "Daniel", "Ben", "Clay",
            "Hunter", "Travis", "Zach", "Blake", "Cody", "Austin", "Deron",
            "Logan", "Nate", "Nick",
        ],
        "last": [
            "Poirier", "Holloway", "Thompson", "Davis", "Allen", "Brown",
            "Carter", "Johnson", "Williams", "Walker", "Hughes", "Taylor",
            "Crawford", "Lewis", "Sandhagen", "Strickland", "Evans", "Jones",
            "Henderson", "Cruz", "Barnett", "Hardy", "Cannonier", "Spencer",
        ],
    },
    "brazilian": {
        "first": [
            "Joao", "Carlos", "Anderson", "Rafael", "Felipe", "Lucas", "Mauricio",
            "Gabriel", "Fabricio", "Rodrigo", "Demian", "Gleison", "Wanderlei",
            "Vitor", "Lyoto", "Erick", "Alex", "Thiago", "Robson", "Antonio",
            "Diego", "Paulo", "Leandro", "Marcos", "Ronaldo", "Eduardo",
            "Caio", "Pedro", "Leonardo", "Jonas", "Renato", "Victor",
        ],
        "last": [
            "Silva", "Santos", "Barboza", "Lopes", "Oliveira", "Nogueira",
            "Aldo", "Maia", "Cavalcante", "Moraes", "Machida", "Belfort",
            "Barroso", "Werdum", "Borrachinha", "de Lima", "Teixeira",
            "Costa", "Ribeiro", "Martins", "Ferreira", "Almeida",
            "Figueiredo", "de Souza", "Neves", "Romero",
        ],
    },
    "muay_thai": {
        "first": [
            "Chaiyaphum", "Somrak", "Yodchai", "Lerdsila", "Rodtang", "Nong-O",
            "Samart", "Namsaknoi", "Sitthichai", "Tawanchai", "Petchdam",
            "Yodsanan", "Buakaw", "Sangmanee", "Khamthong", "Rungrat",
            "Singdam", "Pakorn", "Anuwat", "Chalermpol", "Somchai",
            "Wanchai", "Sombat", "Pornsanae", "Yodwicha", "Karuhat",
            "Lamnammoon", "Pinsinchai", "Sagat", "Dieselnoi", "Superlek", "Saenchai",
        ],
        "last": [
            "Jitmuangnon", "Banchamek", "Kaiyanghadaow", "Lookboonmee",
            "Muangthong", "Sitmonchai", "Sawsing", "Yeesan", "Prakaipetch",
            "Petchyindee", "Worapoj", "Kiatphontip", "Dejnapa", "Ratanachai",
            "Suriyanbancherd", "Ruenroeng", "Fairtex", "Sor Singyu",
            "Lukjaomaesaiwaree", "Sitjaroenroj", "Rungsri", "Sitthichai",
        ],
    },
    "sea_mixed": {
        "first": [
            "Eduard", "Kevin", "Mark", "Geje", "Bibiano", "Martin", "Christian",
            "Jeremy", "Danny", "Pacio", "Honorio", "Marat", "Rich", "Joshua",
            "Lester", "Romeo", "Rodolfo", "Rene", "Fariz", "Azlan", "Akbar",
            "Thanh", "Minh", "Duc", "Amir", "Garry", "Shinya", "Yushin",
            "Ahmad", "Brandon", "Kang", "Nguyen",
        ],
        "last": [
            "Folayang", "Striegl", "Sangiao", "Fernandes", "Nguyen", "Tran",
            "Loman", "Cruz", "Antonio", "Yusoff", "Akhbar", "Rahman",
            "Togashi", "Okamoto", "Lee", "Kim", "Moraes", "Simon",
            "Soriano", "Phan", "Do", "Huynh", "Ang", "Masvidal",
        ],
    },
}


# Per-region seen-name sets — populated by regional_name(), cleared by reset_name_registry().
_used_names: dict[str, set[str]] = {t: set() for t in _NAMES}


def reset_name_registry() -> None:
    """Clear all per-region seen-name sets. Call at the start of each new simulation."""
    for s in _used_names.values():
        s.clear()


def regional_name(template_name: str) -> str:
    """Returns a name unique within the region, drawn from its cultural pool.

    Retries on collision. Raises RuntimeError if the pool is exhausted (shouldn't
    happen with ~700+ combos and ~120–150 fighters per region, but catches runaway cases).
    """
    pool = _NAMES[template_name]
    used = _used_names[template_name]
    max_combos = len(pool["first"]) * len(pool["last"])
    if len(used) >= max_combos:
        raise RuntimeError(
            f"Name pool for '{template_name}' fully exhausted ({max_combos} combos used). "
            f"Add more names to _NAMES in academies.py."
        )
    while True:
        name = f"{random.choice(pool['first'])} {random.choice(pool['last'])}"
        if name not in used:
            used.add(name)
            return name
