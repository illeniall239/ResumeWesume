"""The header block: name, title, and how to reach someone.

No model call here, and that is a decision rather than an omission. An email
address, a phone number and a LinkedIn URL have exact shapes, so a regex gets
them right every time; a 14B model gets them right most of the time, and the
times it does not are a transposed digit in a phone number that nobody proof-
reads because it looks fine. The same argument covers skills, which are a
delimited list. Between them that is two of the six section calls removed, and
with them roughly half the wall clock of an import on a local model.

Only the name and title are genuinely ambiguous, and they are positional: the
name is what a resume opens with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from studio.ingest.pdf import Line

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# A phone number is matched loosely here and then validated by digit count
# below, rather than by one pattern trying to encode every national convention
# at once. What came before required a separator after the area code -- true of
# "(512) 555-0148", false of "+441632960123" -- so an ordinary international
# number, written the way most of the world writes it, matched nothing at all.
_PHONE_CANDIDATE = re.compile(r"\+?\(?\d[\d\s().\-]{7,20}\d")
_MIN_LOCAL_DIGITS = 10  # without a country code, fewer than this is a year range
_MIN_INTL_DIGITS = 8
_MAX_PHONE_DIGITS = 15  # E.164

_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[\w\-/%.]+", re.I)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.(?:com|io)/[\w\-/%.]+", re.I)

# Personal sites are not all .com, and an allow-list of nine suffixes quietly
# drops anyone whose domain ends in .tech, .dev or their own country's code. A
# bare domain still needs a recognised suffix, because "Node.js" and "socket.io"
# would otherwise read as websites; an explicit scheme or "www." is evidence
# enough on its own and accepts any suffix.
_TLDS = (
    "com|net|org|io|dev|me|co|ai|app|xyz|tech|site|page|blog|info|biz|pro|"
    "design|studio|works|space|online|live|life|world|cloud|digital|"
    "uk|us|ca|de|fr|nl|se|no|dk|fi|ie|es|it|ch|be|at|pl|pt|gr|cz|ro|hu|"
    "in|pk|bd|lk|np|au|nz|sg|hk|jp|kr|cn|my|th|vn|ph|id|"
    "ae|sa|qa|il|tr|ru|ua|br|mx|ar|cl|za|ng|ke|eg"
)
_PATH = r"(?:/[\w\-/%.~?=&#]*)?"
_URL = re.compile(
    r"(?:https?://[\w-]+(?:\.[\w-]+)+" + _PATH + r""
    r"|www\.[\w-]+(?:\.[\w-]+)+" + _PATH + r""
    r"|[\w-]+(?:\.[\w-]+)*\.(?:" + _TLDS + r")" + _PATH + r")",
    re.I,
)
# "Austin, TX" / "Seattle, WA" / "London, United Kingdom".
_LOCATION = re.compile(
    r"\b([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+)*,\s*"
    r"(?:[A-Z]{2}|[A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)*))\b"
)

_MAX_NAME_WORDS = 5
# A line carrying any of these is contact detail or a date, not a person's name.
_NOT_A_NAME = re.compile(r"[@0-9|]")


@dataclass(frozen=True)
class Contact:
    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None

    def as_personal_info(self) -> dict[str, object]:
        """The ``personalInfo`` half of a legacy ResumeData payload."""
        return {
            "name": self.name,
            "title": self.title,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "linkedin": self.linkedin,
            "github": self.github,
            "website": self.website,
        }


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0).strip().rstrip(".,;") if match else ""


def _find_phone(text: str) -> str:
    """The first run of digits that can only be a phone number.

    Validating by digit count rather than by shape is what lets the pattern
    stay loose enough for every separator convention. Ten digits are required
    when there is no country code, and that floor is what stops "2014 - 2018"
    -- eight digits and a dash, sitting in the header of a great many resumes
    -- from being read as somebody's phone number.
    """
    for match in _PHONE_CANDIDATE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        floor = _MIN_INTL_DIGITS if raw.startswith("+") else _MIN_LOCAL_DIGITS
        if floor <= len(digits) <= _MAX_PHONE_DIGITS:
            return raw
    return ""


def _looks_like_a_name(text: str) -> bool:
    if not text or _NOT_A_NAME.search(text):
        return False
    words = text.split()
    return 1 <= len(words) <= _MAX_NAME_WORDS


def parse_contact(lines: list[Line]) -> Contact:
    """Pull the header block apart.

    ``lines`` is the contact segment in document order, so position carries
    real information: the name is the first thing that could be a name, and a
    title is what sits directly beneath it.
    """
    body = [line for line in lines if line.text]
    text = "\n".join(line.text for line in body)

    email = _first(_EMAIL, text)
    linkedin = _first(_LINKEDIN, text)
    github = _first(_GITHUB, text)

    # Look for a personal site only in what is left, so the email's domain and
    # the profile URLs cannot be mistaken for one.
    remainder = text
    for claimed in (email, linkedin, github):
        if claimed:
            remainder = remainder.replace(claimed, " ")
    website = _first(_URL, remainder)

    name = ""
    name_index = -1
    for index, line in enumerate(body):
        if _looks_like_a_name(line.text):
            name, name_index = line.text, index
            break

    # A title sits immediately under the name and is prose, not contact detail.
    title = ""
    if 0 <= name_index < len(body) - 1:
        candidate = body[name_index + 1].text
        if (
            _looks_like_a_name(candidate)
            and "," not in candidate
            and not _LOCATION.match(candidate)
        ):
            title = candidate

    # Search for a location after the name so that a two-word surname followed
    # by a comma cannot be read as a city.
    location_source = "\n".join(line.text for line in body[name_index + 1 :])
    location = _first(_LOCATION, location_source)

    return Contact(
        name=name,
        title=title,
        email=email,
        phone=_find_phone(text),
        location=location,
        linkedin=linkedin or None,
        github=github or None,
        website=website or None,
    )
