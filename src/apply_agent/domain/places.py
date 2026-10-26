"""Which country a job's place is in, from the way job boards write places (D68).

Boards write "Madrid, Spain", "Zurich", "Tres Cantos", "Remote - Europe" or
several places at once ("Amsterdam, Netherlands; London, United Kingdom").
A country is found by name, in the languages lists are written in
("Alemania", "Deutschland", "Países Bajos"), or else by a city. The cities
listed are those of the user's markets that their offers name; a place not
listed has no country, and is shown as written.
"""

from typing import Final

from apply_agent.domain.keys import role_key

# Country (in English) -> names and cities that place something in it.
_PLACES: Final[dict[str, tuple[str, ...]]] = {
    "Netherlands": (
        "netherlands", "the netherlands", "nederland", "paises bajos", "holland", "holanda",
        "pays bas", "niederlande", "randstad", "amsterdam", "utrecht", "rotterdam",
        "the hague", "den haag", "eindhoven", "delft", "leiden", "groningen", "amersfoort",
        "leusden", "nieuwegein", "veenendaal", "zeist", "woerden", "de meern", "bunnik",
        "baarn", "mijdrecht", "driebergen rijsenburg", "veldhoven", "haarlem", "hilversum",
        "arnhem", "nijmegen", "breda", "tilburg", "maastricht", "enschede", "zwolle",
    ),
    "Spain": (
        "spain", "espana", "spanien", "espagne", "madrid", "barcelona", "valencia",
        "sevilla", "seville", "bilbao", "malaga", "zaragoza", "community of madrid",
        "comunidad de madrid", "tres cantos", "alcobendas", "getafe", "pozuelo de alarcon",
        "san sebastian de los reyes", "rivas vaciamadrid", "boadilla del monte",
        "san fernando de henares", "humanes de madrid", "navalcarnero", "las rozas",
        "majadahonda", "leganes", "alcala de henares", "torrejon de ardoz", "sant cugat",
    ),
    "Germany": (
        "germany", "deutschland", "alemania", "allemagne", "berlin", "munich", "munchen",
        "hamburg", "frankfurt", "stuttgart", "cologne", "koln", "dusseldorf", "erlangen",
        "nuremberg", "nurnberg", "karlsruhe", "darmstadt", "heidelberg", "dresden", "leipzig",
    ),
    "Switzerland": (
        "switzerland", "schweiz", "suiza", "suisse", "svizzera", "zurich", "zurich metropolitan",
        "geneva", "geneve", "ginebra", "lausanne", "basel", "bern", "berne", "zug", "lugano",
        "winterthur", "st gallen", "lucerne", "luzern", "fribourg", "biel", "baar", "baden",
        "solothurn", "chiasso", "kreuzlingen", "wetzikon", "dubendorf", "mendrisio",
        "wittenbach", "risch rotkreuz", "rotkreuz", "villigen", "rapperswil jona",
        "marin epagnier", "stallikon", "fully", "brugg", "paudex", "stans", "grabs", "eysins",
        "witterswil", "wallisellen", "kriegstetten", "grenchen", "gland", "niederbipp",
        "bioggio", "kloten", "kaiseraugst", "steinhausen", "beringen", "villars sur glane",
        "vaud", "kirchberg",
    ),
    "United Kingdom": (
        "united kingdom", "uk", "england", "scotland", "wales", "great britain",
        "reino unido", "royaume uni", "london", "londres", "cambridge", "oxford",
        "manchester", "edinburgh", "bristol", "cardiff", "leeds", "birmingham", "glasgow",
    ),
    "France": ("france", "francia", "frankreich", "paris", "lyon", "toulouse", "grenoble"),
    "Ireland": ("ireland", "irlanda", "dublin", "cork", "galway"),
    "Sweden": (
        "sweden", "sverige", "suecia", "stockholm", "gothenburg", "goteborg", "malmo",
        "solna", "lund", "uppsala", "linkoping", "vasteras", "vaxjo", "molndal", "lulea",
        "skovde", "jonkoping", "mjolby", "kalmar", "jarfalla", "arboga", "karlskrona",
        "hassleholm", "huskvarna", "ostergotland",
    ),
    "Belgium": ("belgium", "belgica", "belgie", "belgique", "brussels", "antwerp", "ghent"),
    "Denmark": ("denmark", "danmark", "dinamarca", "copenhagen", "aarhus"),
    "Portugal": ("portugal", "lisbon", "lisboa", "porto"),
    "Italy": ("italy", "italia", "milan", "milano", "rome", "roma", "turin", "torino"),
    "Poland": ("poland", "polska", "polonia", "warsaw", "warszawa", "krakow", "wroclaw"),
    "Czech Republic": ("czech republic", "czechia", "prague", "praha", "brno"),
    "Austria": ("austria", "osterreich", "vienna", "wien", "graz", "linz"),
    "Serbia": ("serbia", "belgrade"),
    "Israel": ("israel", "tel aviv", "haifa"),
    "India": ("india", "bengaluru", "bangalore", "mumbai", "hyderabad", "pune", "chennai"),
    "Canada": ("canada", "toronto", "vancouver", "montreal"),
    "Japan": ("japan", "tokyo"),
    "South Korea": ("south korea", "korea", "seoul"),
    "Singapore": ("singapore",),
    "Australia": ("australia", "sydney", "melbourne"),
    "Brazil": ("brazil", "brasil", "sao paulo", "sao jose dos campos"),
    "Costa Rica": ("costa rica",),
    "United States": (
        "united states", "usa", "us", "usca", "estados unidos", "california", "new york",
        "washington", "texas", "florida", "illinois", "massachusetts", "pennsylvania",
        "ohio", "michigan", "arizona", "nevada", "alabama", "north carolina",
        "south carolina", "maryland", "virginia", "seattle", "san francisco",
        "mountain view", "palo alto", "chicago", "boston", "brooklyn", "west coast",
        "ca", "ny", "tx", "fl", "il", "pa", "az", "nv", "sc", "nc", "wa",
    ),
}  # fmt: skip
# Regions that are not a country but say where a remote job is.
_REGIONS: Final = {"europe": "Europe", "emea": "Europe", "eu": "Europe", "worldwide": "Anywhere"}

# Longer names first, so "new york" wins over "york" and "south korea" over "korea".
_ALIASES: Final = sorted(
    ((role_key(alias), country) for country, names in _PLACES.items() for alias in names),
    key=lambda pair: -len(pair[0]),
)


def country_of(place: str | None) -> str | None:
    """The country (or remote region) one place is in, or None if it cannot tell."""
    if not place:
        return None
    padded = f" {role_key(place)} "
    for alias, country in _ALIASES:
        if alias and f" {alias} " in padded:
            return country
    for word, region in _REGIONS.items():
        if f" {word} " in padded:
            return region
    return None


def places_of(location: str | None) -> list[tuple[str, str | None]]:
    """Each place of a location, with its country: 'A; B' is two places."""
    if not location:
        return []
    parts = [part.strip() for part in location.split(";") if part.strip()]
    return [(part, country_of(part)) for part in parts]
