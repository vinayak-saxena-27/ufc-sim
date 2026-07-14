from __future__ import annotations

import random
from dataclasses import dataclass

# --- Academy entity -----------------------------------------------------------

@dataclass(frozen=True)
class Academy:
    name: str
    template: str           # must match a key in TEMPLATES
    nudges: dict            # {attr_name: float} -- subset of ATTR_NAMES; see get_nudge()
    pipeline_strength: float  # zero-centered; positive = better placement track record / connections

    def get_nudge(self, attr: str) -> float:
        return self.nudges.get(attr, 0.0)


# --- Academy definitions ------------------------------------------------------
# 3 academies per region x 5 regions = 15 academies.
#
# STYLISTIC NUDGES (from 5a): intentionally small (+-3-6 pts) -- each academy is
# a flavour variant within its region, not a wholesale change of profile.
#
# PIPELINE STRENGTH (added 5b): zero-centered; roughly -8 to +9 range.
# Represents connections / placement track record INDEPENDENT of fighter quality.
# Two fighters with equal true skill but different academies will have different
# hype and slightly different promotion likelihoods -- same ability, different
# opportunity path.
#
# Key design choice: within each region, academies VARY on pipeline_strength
# so that the two dimensions (stylistic identity, promotion connections) are
# deliberately decoupled -- the best-connected academy is not always the one
# with the best stylistic nudges, and vice versa.

# -- Dagestan / Sambo ----------------------------------------------------------
# Template anchors: wrestling +22, clinch +20, cardio +22, chin +16, boxing -15
_MAKHACHKALA_SAMBO = Academy(
    name="Makhachkala Combat Sambo Centre",
    template="dagestan_sambo",
    nudges={
        "wrestling": +4.0,  # pure takedown shop -- highest TD completion
        "clinch":    +3.0,
    },
    pipeline_strength= +3.0,  # solid regional network; places fighters in Eastern European orgs
)

_ANZHI_MMA = Academy(
    name="Anzhi MMA Factory",
    template="dagestan_sambo",
    nudges={
        "cardio": +4.0,  # grinding-pace specialists, known for 25-min conditioning
        "chin":   +3.0,
    },
    pipeline_strength= -4.0,  # great coaches, poor at getting fighters noticed internationally
)

_EAGLE_ATHLETIC = Academy(
    name="Eagle Athletic Club",
    template="dagestan_sambo",
    nudges={
        "fight_iq": +5.0,  # cerebral positional control, reads pace changes well
        "bjj":      +3.0,  # better ground retention than typical Dagestan base
    },
    pipeline_strength= +7.0,  # best-connected Dagestan camp; multiple fighters placed in top orgs
)

# -- American Wrestling --------------------------------------------------------
# Template anchors: wrestling +22, athleticism +20, cardio +20, boxing +8, bjj -12
_ALLIANCE_MMA = Academy(
    name="Alliance MMA San Diego",
    template="american_wrestling",
    nudges={
        "boxing":   +5.0,  # best striking integration of the three camps
        "fight_iq": +3.0,
    },
    pipeline_strength= +8.0,  # one of the best-connected gyms in MMA; strong UFC/title-shot ties
)

_AMERICAN_TOP_TEAM = Academy(
    name="American Top Team Florida",
    template="american_wrestling",
    nudges={
        "power":       +5.0,  # known for finishing power and explosive style
        "athleticism": +3.0,
    },
    pipeline_strength= +6.0,  # well-connected; consistent top-org placements across eras
)

_ELEVATION_FIGHT_TEAM = Academy(
    name="Elevation Fight Team Colorado",
    template="american_wrestling",
    nudges={
        "cardio":    +5.0,  # high-altitude conditioning programme
        "wrestling": +3.0,
    },
    pipeline_strength= -2.0,  # solid camp but limited placement network; fighters must earn it
)

# -- Brazilian -----------------------------------------------------------------
# Template anchors: bjj +25, fight_iq +22, kickboxing +8, wrestling 0
_NOVA_UNIAO = Academy(
    name="Nova Uniao Rio",
    template="brazilian",
    nudges={
        "bjj":    +4.0,  # BJJ / boxing hybrid -- produces well-rounded ground attackers
        "boxing": +3.0,
    },
    pipeline_strength= +5.0,  # strong historical ties to UFC and major promotions
)

_CHUTE_BOXE = Academy(
    name="Chute Boxe Academy",
    template="brazilian",
    nudges={
        "kickboxing": +5.0,  # aggressive Muay Thai-influenced brawling style
        "power":      +4.0,
    },
    pipeline_strength= -3.0,  # legendary camp; pipeline weakened after its 2000s peak
)

_GRACIE_BARRA_SP = Academy(
    name="Gracie Barra Sao Paulo",
    template="brazilian",
    nudges={
        "bjj":      +6.0,  # pure submission hunting; deepest ground repertoire
        "fight_iq": +3.0,
    },
    pipeline_strength= +2.0,  # global BJJ brand provides modest organizational connection benefits
)

# -- Muay Thai / Thailand ------------------------------------------------------
# Template anchors: kickboxing +25, clinch +22, chin +18, boxing +10, wrestling -18
_FAIRTEX_CENTER = Academy(
    name="Fairtex Training Center",
    template="muay_thai",
    nudges={
        "kickboxing": +4.0,  # technical precision, angle work
        "clinch":     +3.0,
    },
    pipeline_strength= +4.0,  # international profile; strong ONE Championship connections
)

_TIGER_MUAY_THAI = Academy(
    name="Tiger Muay Thai Phuket",
    template="muay_thai",
    nudges={
        "power":  +4.0,  # power striking emphasis; international boxing coaching
        "boxing": +3.0,
    },
    pipeline_strength= +1.0,  # large international camp; moderate org relationships
)

_LANNA_MUAY_THAI = Academy(
    name="Lanna Muay Thai Chiang Mai",
    template="muay_thai",
    nudges={
        "cardio": +5.0,  # traditional conditioning regimen, long camp cycles
        "chin":   +3.0,
    },
    pipeline_strength= -6.0,  # traditional camp; minimal international MMA pipeline
)

# -- Southeast Asia Mixed ------------------------------------------------------
# Template anchors: kickboxing +18, athleticism +18, bjj +15, wrestling -12, power -10
_EVOLVE_MMA = Academy(
    name="Evolve MMA Singapore",
    template="sea_mixed",
    nudges={
        "fight_iq": +5.0,  # champion coaching roster; highest tactical diversity in region
        "bjj":      +3.0,
    },
    pipeline_strength= +9.0,  # best-connected camp in SE Asia; ONE Championship founding ties
)

_TEAM_LAKAY = Academy(
    name="Team Lakay Philippines",
    template="sea_mixed",
    nudges={
        "athleticism": +5.0,  # acrobatic aggressive style, explosive fighters
        "kickboxing":  +3.0,
    },
    pipeline_strength= +3.0,  # strong ONE Championship track record; growing international profile
)

_ELORDE_COMBAT = Academy(
    name="Elorde Combat Sports Manila",
    template="sea_mixed",
    nudges={
        "clinch": +4.0,  # Muay Thai-influenced clinch integration
        "cardio": +3.0,
    },
    pipeline_strength= -5.0,  # strong local boxing brand; limited international MMA pipeline
)


ACADEMIES: dict[str, list[Academy]] = {
    "dagestan_sambo":     [_MAKHACHKALA_SAMBO, _ANZHI_MMA, _EAGLE_ATHLETIC],
    "american_wrestling": [_ALLIANCE_MMA, _AMERICAN_TOP_TEAM, _ELEVATION_FIGHT_TEAM],
    "brazilian":          [_NOVA_UNIAO, _CHUTE_BOXE, _GRACIE_BARRA_SP],
    "muay_thai":          [_FAIRTEX_CENTER, _TIGER_MUAY_THAI, _LANNA_MUAY_THAI],
    "sea_mixed":          [_EVOLVE_MMA, _TEAM_LAKAY, _ELORDE_COMBAT],
}

# Flat lookup: academy name -> pipeline_strength.
# Used by matchmaking.py for the direct promotion nudge without importing all of ACADEMIES.
ACADEMY_PIPELINE: dict[str, float] = {
    acad.name: acad.pipeline_strength
    for region_list in ACADEMIES.values()
    for acad in region_list
}


def pick_academy(template_name: str) -> Academy:
    """Uniform random selection among a region's academies.
    Session 5a: no quality/reputation weighting -- that's 5c.
    """
    return random.choice(ACADEMIES[template_name])


# --- Per-region name pools ----------------------------------------------------
# Shared at REGION level, not per-academy -- multiple academies within a region
# draw from the same cultural pool, which is realistic.
#
# Pool sizes: ~100-110 first x ~70-80 last ~= 7000-8700 combos per region
# (matchmaking-audit session: expanded another ~3.3-3.7x from the 2026-07-13
# sizes of ~2100-2460, which measurably exhausted at ~fight #7500 of a
# 50-sim-year seed-42 run -- dagestan_sambo first, since the academy-
# reputation feedback loop skews generation volume toward it).
# Uniqueness is enforced per-region via _used_names: regional_name() retries on
# collision and raises if the pool is somehow exhausted. Call reset_name_registry()
# at the start of each fresh simulation (generate_all_tiers / generate_population
# do this automatically).
# The former "names are never recycled" ceiling is now fixed at the root:
# see the name-recycling block below (_retired_names / release_name /
# recycle_names) -- a removed fighter's name returns to the pool after a
# cooldown once nothing active references it, so steady-state usage is
# bounded by the ACTIVE population plus a referenced-names tail rather than
# by cumulative all-time generation volume.

_NAMES: dict[str, dict[str, list[str]]] = {
    "dagestan_sambo": {
        "first": [
            "Khabib", "Islam", "Umar", "Magomed", "Abubakar", "Shamil", "Zaur",
            "Akhmat", "Ruslan", "Zelim", "Zurab", "Hasan", "Aslan", "Eldar",
            "Makhach", "Rashid", "Said", "Musa", "Alibek", "Suliman", "Husein",
            "Abdulmanap", "Khasan", "Rizvan", "Kurban", "Harun", "Bilal",
            "Ramzan", "Bekhan", "Daud", "Artur", "Timur",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Adam", "Aslanbek", "Gadzhi", "Ibragim", "Kamil", "Magomedrasul",
            "Nurmagomed", "Omar", "Rustam", "Sultan", "Yusuf", "Ziyavdin",
            "Anzor", "Beslan", "Dzhabrail", "Gazimagomed", "Ibrahim", "Kazbek",
            "Muslim", "Nariman", "Rasul", "Shamsudin", "Vakha", "Zaurbek",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Abdul", "Abdulla", "Adlan", "Akhmed", "Alim", "Amir", "Apti",
            "Arbi", "Arsen", "Askhab", "Ayub", "Baysangur", "Chingiz",
            "Dagir", "Dalgat", "Deni", "Dzhamal", "Elbrus", "Gamzat",
            "Gasan", "Idris", "Ilyas", "Isa", "Iskhak", "Kadi", "Khamzat",
            "Khizir", "Lecha", "Lom-Ali", "Magomedali", "Mairbek", "Mansur",
            "Movsar", "Murad", "Nazhmudin", "Ramazan", "Salman", "Saygid",
            "Shakhban", "Sharap", "Shuayb", "Tagir", "Turpal", "Ubaidula",
            "Umalat", "Uvais", "Yakub", "Yunus", "Zalim", "Zaynulla",
        ],
        "last": [
            "Nurmagomedov", "Makhachev", "Ankalaev", "Khasbulaev", "Ulanbekov",
            "Guseinov", "Abdulvakhidov", "Aliev", "Gadzhiev", "Osmanov",
            "Musaev", "Mamedov", "Saidov", "Khalidov", "Yusupov", "Merabdze",
            "Bakiev", "Gaitaev", "Chimaev", "Dzhitiev", "Magomedov",
            "Kurbanov", "Bazaev", "Gasanov",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Abakarov", "Alikhanov", "Betilgiriev", "Dadaev", "Elmurzaev",
            "Gamzatov", "Idrisov", "Kadyrov", "Labazanov", "Muradov",
            "Nalgiev", "Omarov", "Pashaev", "Ramazanov", "Sadulaev",
            "Tagirov", "Vakhaev", "Zaurbekov",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Abdulaev", "Agaev", "Akaev", "Alkhasov", "Atsaev", "Bagov",
            "Batirov", "Bekov", "Daudov", "Dzhamalov", "Efendiev",
            "Gadzhimuradov", "Gairbekov", "Gimbatov", "Isaev", "Ismailov",
            "Kagirov", "Kerimov", "Khabilov", "Khamidov", "Kuramagomedov",
            "Kurbanaliev", "Magomedaliev", "Makhmudov", "Mutaev",
            "Nurudinov", "Rabadanov", "Salamov", "Shapiev", "Sharipov",
            "Suleymanov", "Taymazov", "Tsarukaev", "Umarov", "Vagabov",
            "Yakubov", "Zubairaev",
        ],
    },
    "american_wrestling": {
        "first": [
            "Dustin", "Justin", "Tony", "Colby", "Gilbert", "Sean", "Cory",
            "Brandon", "Marlon", "Derek", "Marcus", "Tyrone", "Jake", "Ryan",
            "Kyle", "Chad", "Brett", "Jordan", "Mike", "Daniel", "Ben", "Clay",
            "Hunter", "Travis", "Zach", "Blake", "Cody", "Austin", "Deron",
            "Logan", "Nate", "Nick",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Ethan", "Cameron", "Trevor", "Garrett", "Preston", "Wyatt",
            "Bryce", "Colton", "Dalton", "Grant", "Trent", "Shane", "Wade",
            "Tyler", "Cole", "Riley", "Mason", "Landon", "Carson", "Braxton",
            "Dawson", "Gunner", "Maddox", "Beau",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Aaron", "Aiden", "Alec", "Andre", "Anthony", "Ashton",
            "Bobby", "Brad", "Brady", "Brendan", "Brian", "Caleb", "Casey",
            "Chase", "Chris", "Craig", "Dane", "Dante", "Darren", "Devin",
            "Donnie", "Drew", "Dylan", "Eli", "Eric", "Evan", "Gage",
            "Gavin", "Grady", "Heath", "Ian", "Jared", "Jason", "Jesse",
            "Joel", "Johnny", "Josh", "Keith", "Kurt", "Kyler", "Lance",
            "Levi", "Micah", "Nolan", "Owen", "Paul", "Reid", "Scott",
            "Seth", "Tanner", "Todd", "Troy", "Vince", "Weston", "Zane",
        ],
        "last": [
            "Poirier", "Holloway", "Thompson", "Davis", "Allen", "Brown",
            "Carter", "Johnson", "Williams", "Walker", "Hughes", "Taylor",
            "Crawford", "Lewis", "Sandhagen", "Strickland", "Evans", "Jones",
            "Henderson", "Cruz", "Barnett", "Hardy", "Cannonier", "Spencer",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Mitchell", "Foster", "Bishop", "Sanders", "Coleman", "Ferguson",
            "Griffin", "Harmon", "Kessler", "Lawson", "Nelson", "Owens",
            "Parker", "Reeves", "Sawyer", "Turner", "Vance", "Wheeler",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Adams", "Baker", "Bennett", "Blackburn", "Boone", "Bradley",
            "Briggs", "Brooks", "Buchanan", "Burns", "Caldwell", "Campbell",
            "Carroll", "Chandler", "Clark", "Collins", "Conley", "Cooper",
            "Decker", "Dixon", "Donovan", "Doyle", "Duncan", "Ellis",
            "Emerson", "Fisher", "Fleming", "Ford", "Franklin", "Garrison",
            "Gibson", "Graham", "Hayes", "Holt", "Hopkins", "Hudson",
            "Ingram", "Jennings",
        ],
    },
    "brazilian": {
        "first": [
            "Joao", "Carlos", "Anderson", "Rafael", "Felipe", "Lucas", "Mauricio",
            "Gabriel", "Fabricio", "Rodrigo", "Demian", "Gleison", "Wanderlei",
            "Vitor", "Lyoto", "Erick", "Alex", "Thiago", "Robson", "Antonio",
            "Diego", "Paulo", "Leandro", "Marcos", "Ronaldo", "Eduardo",
            "Caio", "Pedro", "Leonardo", "Jonas", "Renato", "Victor",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Bruno", "Marcelo", "Igor", "Rogerio", "Wagner", "Cristiano",
            "Gustavo", "Henrique", "Julio", "Nelson", "Osvaldo", "Ricardo",
            "Sergio", "Tarcisio", "Valdir", "Wallace", "Everton", "Jefferson",
            "Luciano", "Marcio", "Nilton", "Cassio", "Alessandro", "Fernando",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Adriano", "Alan", "Alexandre", "Amaury", "Andre", "Ariel",
            "Arnaldo", "Augusto", "Benicio", "Breno", "Carlinhos", "Celso",
            "Cicero", "Cleber", "Danilo", "Davi", "Denis", "Edson", "Elias",
            "Emerson", "Enzo", "Fabiano", "Fabio", "Flavio", "Geraldo",
            "Gilberto", "Guilherme", "Heitor", "Helio", "Hugo", "Iago",
            "Ivan", "Jackson", "Jair", "Jean", "Joaquim", "Jorge", "Jose",
            "Kaique", "Kleber", "Luan", "Luiz", "Mateus", "Mauro", "Michel",
            "Milton", "Murilo", "Natan", "Otavio", "Vinicius",
        ],
        "last": [
            "Silva", "Santos", "Barboza", "Lopes", "Oliveira", "Nogueira",
            "Aldo", "Maia", "Cavalcante", "Moraes", "Machida", "Belfort",
            "Barroso", "Werdum", "Borrachinha", "de Lima", "Teixeira",
            "Costa", "Ribeiro", "Martins", "Ferreira", "Almeida",
            "Figueiredo", "de Souza", "Neves", "Romero",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Pereira", "Gomes", "Carvalho", "Correia", "Nascimento", "Araujo",
            "Vieira", "Monteiro", "Azevedo", "Cardoso", "Dias", "Fonseca",
            "Guimaraes", "Junqueira", "Lacerda", "Medeiros", "Pinheiro", "Rezende",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Assis", "Barbosa", "Batista", "Bezerra", "Braga", "Brito",
            "Camargo", "Campos", "Castro", "Cunha", "Dantas", "Duarte",
            "Fagundes", "Farias", "Freitas", "Furtado", "Galvao", "Garcia",
            "Goncalves", "Guedes", "Henriques", "Lemos", "Lima", "Macedo",
            "Magalhaes", "Mendes", "Mesquita", "Miranda", "Moreira", "Mota",
            "Nunes", "Pacheco", "Paiva", "Pires", "Prado", "Queiroz",
            "Ramos", "Rocha", "Sales", "Sampaio", "Santana", "Siqueira",
            "Soares", "Tavares", "Torres", "Valente", "Vasconcelos", "Xavier",
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
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite) --
            # this region has the smallest combo count of the 5, so it needed
            # the expansion most.
            "Petchboonchu", "Attachai", "Kongsak", "Manasak", "Chatchai",
            "Wanheng", "Kaewsamrit", "Denkiri", "Thongchai", "Nontachai",
            "Silachai", "Kritsada", "Panomrunglek", "Wichannoi", "Kongfah",
            "Sittichok", "Adisak", "Yodphupha", "Petpanomrung", "Sakchainoi",
            "Chartchai", "Rittidet", "Wisanulek", "Thanonchai",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Apidej", "Boonlai", "Chanchai", "Chatri", "Dechapol",
            "Detnarong", "Ekapol", "Jakkrit", "Jirasak", "Kamon",
            "Kiatisak", "Kittipong", "Komsan", "Krit", "Kulabdam",
            "Mongkol", "Narong", "Nattawut", "Nopparat", "Payak",
            "Petchmorakot", "Phanuwat", "Piyapong", "Pongsiri", "Prakasit",
            "Pravit", "Preecha", "Rungravee", "Sakmongkol", "Samransak",
            "Sarawut", "Seksan", "Somluck", "Songchai", "Sorasak",
            "Suchart", "Sudsakorn", "Suriya", "Teerapong", "Thakoon",
            "Thanet", "Veeraphol", "Wichan", "Worapon", "Yodlekpet",
            "Yodsaenklai", "Yuttana",
        ],
        "last": [
            "Jitmuangnon", "Banchamek", "Kaiyanghadaow", "Lookboonmee",
            "Muangthong", "Sitmonchai", "Sawsing", "Yeesan", "Prakaipetch",
            "Petchyindee", "Worapoj", "Kiatphontip", "Dejnapa", "Ratanachai",
            "Suriyanbancherd", "Ruenroeng", "Fairtex", "Sor Singyu",
            "Lukjaomaesaiwaree", "Sitjaroenroj", "Rungsri", "Sitthichai",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Sitboonmee", "Wanmaneechot", "Chuwattana", "Kaewjadok", "Sorjor",
            "Rungrueang", "Kiatbusaba", "Aekwiboonwut", "Yodkhunpon",
            "Petchnamnak", "Thongpradit", "Chumphonburi", "Jaosuayai",
            "Sittipatthana", "Wongchai", "Rungnapa",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Chormeechai", "Dejrat", "Kaewpadung", "Kiatsongrit",
            "Lukbanyai", "Lukmingkwan", "Mahasarakham", "Nakhonthong",
            "Petchrungruang", "Phetsimuang", "Pinyo", "Rachanon",
            "Sakhomsin", "Silapathai", "Singpatong", "Sitkaew", "Sitniwat",
            "Sitpholek", "Sitsongpeenong", "Sorjortong", "Srisaket",
            "Teeded", "Yodying", "Kietpetch", "Lukprakhon", "Sitjemam",
            "Wor Wanchai", "Petchkiatpetch", "Singklongsi", "Lukchaomaesai",
        ],
    },
    "sea_mixed": {
        "first": [
            "Eduard", "Kevin", "Mark", "Geje", "Bibiano", "Martin", "Christian",
            "Jeremy", "Danny", "Pacio", "Honorio", "Marat", "Rich", "Joshua",
            "Lester", "Romeo", "Rodolfo", "Rene", "Fariz", "Azlan", "Akbar",
            "Thanh", "Minh", "Duc", "Amir", "Garry", "Shinya", "Yushin",
            "Ahmad", "Brandon", "Kang", "Nguyen",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Jerome", "Alvin", "Renato", "Reynald", "Gerald", "Dennis",
            "Wilfredo", "Arjan", "Danilo", "Roldan", "Herbert", "Rex",
            "Randy", "Elmer", "Nestor", "Ramon", "Boyet", "Alfie", "Rico",
            "Wesley", "Junard", "Toshio", "Hiroshi", "Kenji",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Adrian", "Aldrin", "Angelo", "Arnel", "Bien", "Carlo",
            "Cedric", "Chico", "Efren", "Emman", "Ernesto", "Ferdinand",
            "Francis", "Froilan", "Gerardo", "Gilbert", "Harold", "Ismael",
            "Janrey", "Jayson", "Joenel", "Jomar", "Julius", "Junrey",
            "Lito", "Manny", "Marcelino", "Michael", "Noel", "Onyok",
            "Paolo", "Patricio", "Renz", "Rey", "Ricky", "Rodel",
            "Rogelio", "Rolando", "Ronnie", "Roque", "Sonny", "Teodoro",
            "Vic", "Virgilio", "Zaldy", "Binh", "Cuong", "Dat", "Hieu",
            "Khanh", "Long", "Phuc", "Quan", "Tuan", "Vinh",
        ],
        "last": [
            "Folayang", "Striegl", "Sangiao", "Fernandes", "Nguyen", "Tran",
            "Loman", "Cruz", "Antonio", "Yusoff", "Akhbar", "Rahman",
            "Togashi", "Okamoto", "Lee", "Kim", "Moraes", "Simon",
            "Soriano", "Phan", "Do", "Huynh", "Ang", "Masvidal",
            # 2026-07-13 name-pool expansion (org roster rescale co-requisite)
            "Aldeguer", "Bulacan", "Catalan", "Dagplas", "Estrada", "Fabroa",
            "Gascon", "Hernandez", "Ilagan", "Jamora", "Kadena", "Lacson",
            "Manalo", "Navarro", "Ochoa", "Pineda",
            # matchmaking-audit session expansion (recycling co-requisite)
            "Abad", "Alcantara", "Bautista", "Belingon", "Cagas", "Canete",
            "Casimero", "Concepcion", "Dela Cruz", "Diaz", "Donaire",
            "Eustaquio", "Gaballo", "Gonzales", "Jaafar", "Kingad", "Lausa",
            "Magsayo", "Malinao", "Mangubat", "Nietes", "Olivarez",
            "Pacatiw", "Paculba", "Pagara", "Ramirez", "Reyes", "Salvador",
            "Sanchez", "Santiago", "Taduran", "Tolentino", "Velasco",
            "Vergara", "Villanueva", "Yodbua",
        ],
    },
}


# Per-region seen-name sets -- populated by regional_name(), cleared by reset_name_registry().
_used_names: dict[str, set[str]] = {t: set() for t in _NAMES}

# ── Name recycling (matchmaking-audit session) ───────────────────────────────
# Root-cause fix for pool exhaustion (the pool exhausted at ~fight #7500 of a
# 50-sim-year run even AFTER the 2026-07-13 3x expansion): a retired/cut
# fighter's name returns to the available pool once BOTH hold:
#   1. NAME_RECYCLE_COOLDOWN_DAYS have passed since the fighter was removed;
#   2. no ACTIVE record still references the name -- i.e. it is not the name
#      of any active fighter and does not appear as opponent_name in any
#      active fighter's fight_history. (Post-sim archival logs -- title
#      history, cut log -- keep their historical rows; a name reappearing
#      across ERAS there is normal sports reality, not a collision. The
#      collision that matters is two contemporaneous fighters, or a live
#      fight-history link resolving to the wrong person -- several systems
#      match opponents BY NAME, so recycling a still-referenced name would
#      silently graft the old fighter's pairing/quality history onto the new
#      one. The referenced-names check makes that impossible by construction.)
#
# release_name() is called by cuts.execute_removal (the single shared
# removal path for cuts AND retirements); recycle_names() is swept quarterly
# by career/replenishment.py's backstop scan, which is the one caller with
# the whole active population in hand to build the referenced-names set.

NAME_RECYCLE_COOLDOWN_DAYS: int = 365

_retired_names: dict[str, list[tuple[str, int]]] = {t: [] for t in _NAMES}


def release_name(template_name: str, name: str, sim_day: int) -> None:
    """Queue a removed fighter's name for eventual recycling. Safe no-op for
    unknown templates/names (e.g. test fixtures that never registered)."""
    if template_name in _retired_names and name in _used_names.get(template_name, set()):
        _retired_names[template_name].append((name, sim_day))


def recycle_names(referenced_names: set[str], current_day: int) -> int:
    """Return queued names to the available pool once cooled down and no
    longer referenced by any active record (see block comment above).
    Returns how many names were recycled this sweep."""
    recycled = 0
    for template, queue in _retired_names.items():
        keep: list[tuple[str, int]] = []
        for name, day in queue:
            if current_day - day < NAME_RECYCLE_COOLDOWN_DAYS or name in referenced_names:
                keep.append((name, day))
            else:
                _used_names[template].discard(name)
                recycled += 1
        queue[:] = keep
    return recycled


def reset_name_registry() -> None:
    """Clear all per-region seen-name sets and recycle queues. Call at the
    start of each new simulation."""
    for s in _used_names.values():
        s.clear()
    for q in _retired_names.values():
        q.clear()


def regional_name(template_name: str) -> str:
    """Returns a name unique within the region, drawn from its cultural pool.

    Retries on collision. Raises RuntimeError if the pool is exhausted (shouldn't
    happen with ~700+ combos and ~120-150 fighters per region, but catches runaway cases).
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
