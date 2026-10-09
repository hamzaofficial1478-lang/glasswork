"""
countries.py — what the program needs to know about any country.

"Any small business, anywhere, from launch." The agency was written for one
country: every phone number was assumed to start +971, every map search ended
"United Arab Emirates", and every email was written under the UAE's rules. This
is the one place a country's facts live, so a website in Manchester or Munich
gets its own.

PHONE NUMBERS come from Google's numbering library (phonenumbers), which knows
every country's formats — "0161 496 0000" in the UK and "04 385 1122" in the
UAE are both read correctly, and the same number written two ways is one
number. Without the library a short built-in table keeps the common countries
working.

EMAIL MARKETING RULES differ by country and are the business's responsibility,
not ours. The table below says only what is well settled; everywhere else the
agency asks the owner to confirm before it writes to anybody:

  allowed   writing to BUSINESSES is permitted with the sender identified and
            an opt-out in every message (UAE, UK, US). The UK's rule covers
            companies; a sole trader there counts as a private person.
  consent   prior consent is needed even between businesses (Germany, Austria),
            so the agency drafts and never sends.
  confirm   anywhere else: the owner reads his country's rule and says yes on
            Setup before the first message goes.
"""
import re

try:
    import phonenumbers as _pn
except Exception:                                   # pragma: no cover — optional
    _pn = None

# The place-name tables are most of the library's weight (a quarter of a
# second here, far more on a Windows laptop), and nearly every program that
# imports this file never asks for a country's name. So they load on first use.
_GEO = []


def _geo():
    if not _GEO:
        try:
            from phonenumbers import geocoder
            _GEO.append(geocoder)
        except Exception:                           # pragma: no cover — optional
            _GEO.append(None)
    return _GEO[0]

# The common ones, for a computer without the library. (name, dialling code)
_FALLBACK = {
    "AE": ("United Arab Emirates", "971"), "SA": ("Saudi Arabia", "966"),
    "QA": ("Qatar", "974"), "KW": ("Kuwait", "965"), "BH": ("Bahrain", "973"),
    "OM": ("Oman", "968"), "GB": ("United Kingdom", "44"), "IE": ("Ireland", "353"),
    "US": ("United States", "1"), "CA": ("Canada", "1"), "AU": ("Australia", "61"),
    "NZ": ("New Zealand", "64"), "IN": ("India", "91"), "PK": ("Pakistan", "92"),
    "DE": ("Germany", "49"), "AT": ("Austria", "43"), "FR": ("France", "33"),
    "ES": ("Spain", "34"), "IT": ("Italy", "39"), "NL": ("Netherlands", "31"),
    "ZA": ("South Africa", "27"), "EG": ("Egypt", "20"), "TR": ("Turkey", "90"),
}

# Countries that read as "the …" in a sentence.
_THE = {"AE": "the UAE", "GB": "the UK", "US": "the US", "NL": "the Netherlands",
        "PH": "the Philippines", "CZ": "the Czech Republic", "DO": "the Dominican Republic",
        "BS": "the Bahamas", "MV": "the Maldives", "GM": "the Gambia"}

# What people write instead of the country's name.
_ALIASES = {
    "AE": ("uae", "u.a.e", "emirates", "united arab emirates"),
    "GB": ("uk", "u.k.", "united kingdom", "great britain", "britain", "england",
           "scotland", "wales", "northern ireland"),
    "US": ("usa", "u.s.a", "u.s.", "united states", "america"),
    "SA": ("ksa", "saudi", "saudi arabia"),
    "NL": ("holland", "netherlands"),
}

# Web addresses businesses there use, most common first.
_TLDS = {"AE": ("ae", "co.ae", "com"), "GB": ("co.uk", "uk", "com"),
         "AU": ("com.au", "au", "com"), "NZ": ("co.nz", "nz", "com"),
         "IN": ("in", "co.in", "com"), "PK": ("pk", "com.pk", "com"),
         "ZA": ("co.za", "za", "com"), "US": ("com", "us"), "SA": ("sa", "com.sa", "com")}

# The language a business there most likely writes in, when it has not said.
_LANG = {"GB": "en-GB", "IE": "en-IE", "US": "en-US", "CA": "en-CA", "AU": "en-AU",
         "NZ": "en-NZ", "IN": "en-IN", "ZA": "en-ZA", "DE": "de-DE", "AT": "de-AT",
         "FR": "fr-FR", "ES": "es-ES", "IT": "it-IT", "NL": "nl-NL"}

ALLOWED, CONSENT, CONFIRM = "allowed", "consent", "confirm"
_RULES = {
    "AE": (ALLOWED, "UAE PDPL and the TDRA's marketing rules: role inboxes, the sender "
                    "named, an opt-out in every message."),
    "GB": (ALLOWED, "UK PECR and UK GDPR: allowed to companies with the sender named and "
                    "an opt-out in every message. Sole traders and partnerships count as "
                    "private people, who need consent."),
    "US": (ALLOWED, "CAN-SPAM: allowed with an honest subject, the sender's postal "
                    "address and an opt-out honoured within ten days."),
    "DE": (CONSENT, "German competition law (UWG §7) needs the recipient's prior consent "
                    "for marketing email, between businesses too."),
    "AT": (CONSENT, "Austrian telecoms law (TKG) needs prior consent for marketing email, "
                    "between businesses too."),
}


def name(cc) -> str:
    cc = (cc or "").upper()
    # The common ones are named the same here as in the library (a test holds
    # them to it), and answering from this list spares loading its tables.
    if cc in _FALLBACK:
        return _FALLBACK[cc][0]
    if _pn:
        ex = _pn.example_number(cc)
        if ex and _geo():
            n = _geo().country_name_for_number(ex, "en")
            if n:
                return n
    return _FALLBACK.get(cc, ("", ""))[0]


def all_countries() -> list:
    """[(code, name)] for every country the program can work in, by name."""
    codes = sorted(_pn.SUPPORTED_REGIONS) if _pn else sorted(_FALLBACK)
    out = [(c, name(c)) for c in codes]
    return sorted([x for x in out if x[1] and x[0] != "001"], key=lambda x: x[1])


def calling_code(cc) -> str:
    cc = (cc or "").upper()
    if _pn:
        code = _pn.country_code_for_region(cc)
        if code:
            return str(code)
    return _FALLBACK.get(cc, ("", ""))[1]


def in_a_sentence(cc) -> str:
    """'the UAE', 'the UK', 'Germany'."""
    cc = (cc or "").upper()
    return _THE.get(cc) or name(cc) or "your country"


def aliases(cc) -> tuple:
    cc = (cc or "").upper()
    return tuple(dict.fromkeys(_ALIASES.get(cc, ()) + ((name(cc) or "").lower(),)))


def tlds(cc) -> tuple:
    cc = (cc or "").upper()
    return _TLDS.get(cc) or ((cc.lower(), "com") if cc else ("com",))


def language(cc) -> str:
    return _LANG.get((cc or "").upper(), "en-GB")


def outreach_rule(cc) -> dict:
    """{rule, law} — what this country allows before the agency writes to a business."""
    rule, law = _RULES.get((cc or "").upper(), (CONFIRM, ""))
    if rule == CONFIRM:
        law = (f"Email marketing rules in {in_a_sentence(cc)} are not something the agency "
               "decides for you. Check what your country allows when writing to businesses, "
               "then confirm it on Setup before the first message goes.")
    return {"rule": rule, "law": law}


# ---------------------------------------------------------------------------
# phone numbers
# ---------------------------------------------------------------------------

def normalise_phone(raw, cc) -> str:
    """
    A number in one shape, +<code><number>, or "" when it is not a real number
    for this country (or an international one written with its +code).
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if _pn:
        try:
            n = _pn.parse(raw, (cc or "").upper() or None)
        except Exception:
            return ""
        return _pn.format_number(n, _pn.PhoneNumberFormat.E164) if _pn.is_possible_number(n) else ""
    code = calling_code(cc)
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else ""
    if digits.startswith("00"):
        return "+" + digits[2:] if 8 <= len(digits) - 2 <= 15 else ""
    if code and digits.startswith(code) and len(digits) > len(code) + 6:
        return "+" + digits
    if digits.startswith("0"):
        digits = digits[1:]
    return "+" + code + digits if code and 6 <= len(digits) <= 12 else ""


def phone_key(raw, cc) -> str:
    """The part of a number that identifies it: no +code, no trunk 0, digits only."""
    e164 = normalise_phone(raw, cc)
    if not e164:
        digits = re.sub(r"\D", "", raw or "")
        return digits.lstrip("0")[-9:] if len(digits) >= 7 else ""
    if _pn:
        try:
            return str(_pn.parse(e164, None).national_number)
        except Exception:
            pass
    code = calling_code(cc)
    d = e164[1:]
    return d[len(code):] if code and d.startswith(code) else d
