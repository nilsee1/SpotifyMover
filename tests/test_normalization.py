"""Unit tests for title and artist normalization."""

import pytest
from src.matcher import normalize_artist, normalize_title, strip_diacritics


def test_strip_diacritics():
    assert strip_diacritics("Café") == "Cafe"
    assert strip_diacritics("Hélène") == "Helene"
    assert strip_diacritics("Björk") == "Bjork"
    assert strip_diacritics("Sigur Rós") == "Sigur Ros"


@pytest.mark.parametrize(
    "raw_title,expected",
    [
        # Remasters in parentheses / brackets
        ("Bohemian Rhapsody (2011 Remaster)", "bohemian rhapsody"),
        ("Comfortably Numb [2011 - Remaster]", "comfortably numb"),
        ("Hotel California (Remastered)", "hotel california"),
        ("Stairway to Heaven (Remaster)", "stairway to heaven"),
        ("Heroes (2017 Remastered Version)", "heroes"),
        # Remasters after hyphens
        ("Bohemian Rhapsody - 2011 Remaster", "bohemian rhapsody"),
        ("Dream On - 2012 Remastered", "dream on"),
        ("Sultans Of Swing - Remastered 1996", "sultans of swing"),
        # Featured artists in parentheses / brackets
        ("Get Lucky (feat. Pharrell Williams & Nile Rodgers)", "get lucky"),
        ("Levitating [feat. DaBaby]", "levitating"),
        ("Industry Baby (ft. Jack Harlow)", "industry baby"),
        # Standalone featuring clauses
        ("Levitating ft. DaBaby", "levitating"),
        ("Song Name feat. Drake", "song name"),
        ("Good Feeling featuring Flo Rida", "good feeling"),
        # Common noise tags
        ("Lose Yourself - Explicit", "lose yourself"),
        ("Blinding Lights (Single Version)", "blinding lights"),
        ("Wonderwall - Radio Edit", "wonderwall"),
        ("In The End [Deluxe Edition]", "in the end"),
        ("Time - 50th Anniversary Edition", "time"),
        # Preserved versions (live, acoustic, remix) must NOT be stripped
        ("Song (Acoustic Version)", "song acoustic version"),
        ("Song (Live at Red Rocks)", "song live at red rocks"),
        ("Song (Club Remix)", "song club remix"),
        ("Song - Live", "song live"),
        ("Song - Acoustic Version", "song acoustic version"),
        ("Hotel California (Live On MTV, 1994)", "hotel california live on mtv 1994"),
        # Diacritics + Punctuation + Apostrophe handling
        ("Hélène", "helene"),
        ("Rock & Roll", "rock roll"),
        ("Don't Stop Me Now", "dont stop me now"),
        ("Don't Stop Believin'", "dont stop believin"),
        ("Where Is My Mind?", "where is my mind"),
        ("Rock 'n' Roll", "rock n roll"),
        ("It's A Long Way To The Top", "its a long way to the top"),
    ],
)
def test_normalize_title(raw_title, expected):
    assert normalize_title(raw_title) == expected


@pytest.mark.parametrize(
    "raw_artist,expected",
    [
        ("AC/DC", "ac dc"),
        ("Beyoncé", "beyonce"),
        ("Jay-Z", "jay z"),
        ("Guns N' Roses", "guns n roses"),
        ("Simon & Garfunkel", "simon garfunkel"),
        ("Daft Punk feat. Pharrell Williams", "daft punk"),
        ("Don't Stop", "dont stop"),
    ],
)
def test_normalize_artist(raw_artist, expected):
    assert normalize_artist(raw_artist) == expected
