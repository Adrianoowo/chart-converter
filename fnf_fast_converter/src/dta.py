"""
dta.py - Harmonix LISP songs.dta AST Parser & Difficulty Tier Mapper.

Parses Harmonix S-expression metadata, handles comments, quotes, nested lists,
curly brace expressions, and maps Rock Band ranks (0-500) to Clone Hero difficulty tiers (0-6).
"""

from __future__ import annotations
import re
from typing import Any, List, Dict, Tuple, Union, Optional

# Difficulty cutoff arrays for Clone Hero tier 0..6 mapping
# Tier(rank) = 0 if rank < T1, 1 if T1 <= rank < T2, ..., 6 if rank >= T6
# Ranks <= 0 (or missing) map to -1 (except rank=1 -> Tier 0)
DIFF_CUTOFFS: Dict[str, List[int]] = {
    "guitar": [139, 176, 221, 267, 333, 409],
    "bass": [135, 181, 228, 293, 364, 436],
    "drum": [124, 151, 178, 242, 345, 448],
    "drums": [124, 151, 178, 242, 345, 448],
    "vocals": [132, 175, 218, 279, 353, 427],
    "vocal": [132, 175, 218, 279, 353, 427],
    "band": [163, 215, 243, 267, 292, 345],
    "keys": [153, 211, 269, 327, 385, 443],
    "real_guitar": [150, 205, 264, 323, 382, 442],
    "real_bass": [150, 205, 264, 323, 382, 442],
    "real_keys": [153, 211, 269, 327, 385, 443],
}

# Standard Genre Normalization
GENRE_MAP: Dict[str, str] = {
    "alternative": "Alternative",
    "blues": "Blues",
    "classicrock": "Classic Rock",
    "classic_rock": "Classic Rock",
    "classical": "Classical",
    "country": "Country",
    "emo": "Emo",
    "glam": "Glam",
    "grunge": "Grunge",
    "hiphoprap": "Hip-Hop/Rap",
    "hiphop": "Hip-Hop/Rap",
    "rap": "Hip-Hop/Rap",
    "urban": "Hip-Hop/Rap",
    "indierock": "Indie Rock",
    "indie_rock": "Indie Rock",
    "indie": "Indie Rock",
    "jrock": "J-Rock",
    "j_rock": "J-Rock",
    "jazz": "Jazz",
    "latin": "Latin",
    "metal": "Metal",
    "new_wave": "New Wave",
    "newwave": "New Wave",
    "novelty": "Novelty",
    "numetal": "Nu-Metal",
    "nu_metal": "Nu-Metal",
    "poprock": "Pop-Rock",
    "pop_rock": "Pop-Rock",
    "popdanceelectronic": "Pop/Dance/Electronic",
    "dance": "Pop/Dance/Electronic",
    "electronic": "Pop/Dance/Electronic",
    "prog": "Prog",
    "punk": "Punk",
    "rbsoulfunk": "R&B/Soul/Funk",
    "rb": "R&B/Soul/Funk",
    "soul": "R&B/Soul/Funk",
    "funk": "R&B/Soul/Funk",
    "reggaeska": "Reggae/Ska",
    "reggae": "Reggae/Ska",
    "ska": "Reggae/Ska",
    "rock": "Rock",
    "southernrock": "Southern Rock",
    "southern_rock": "Southern Rock",
}


def normalize_genre(genre_sym: Optional[str]) -> str:
    """Normalizes DTA genre symbol or string to Clone Hero display genre."""
    if not genre_sym:
        return "Rock"
    cleaned = str(genre_sym).strip("'\" ")
    if not cleaned:
        return "Rock"
    key = cleaned.lower().replace("-", "").replace(" ", "_")
    if key in GENRE_MAP:
        return GENRE_MAP[key]
    # Check original key in map
    orig_key = cleaned.lower()
    if orig_key in GENRE_MAP:
        return GENRE_MAP[orig_key]
    # Format fallback: Title Case with spaces replacing underscores
    return cleaned.replace("_", " ").title()


def map_difficulty_rank(instrument: str, rank: Optional[int]) -> int:
    """
    Maps a Rock Band difficulty rank (0-500 scale) to Clone Hero difficulty tier (0-6).
    Returns -1 if rank is missing or <= 0 (where rank=0 means part is absent).
    """
    if rank is None or rank <= 0:
        return -1
    cutoffs = DIFF_CUTOFFS.get(instrument.lower())
    if not cutoffs:
        return -1
    for tier, cutoff in enumerate(cutoffs):
        if rank < cutoff:
            return tier
    return 6


class DTASyntaxError(Exception):
    """Raised when DTA S-expression syntax is malformed."""
    pass


# Compiled regular expressions for lexing performance and robust boundary detection
RE_UNCLOSED_PAREN = re.compile(r"\)\s*(?:\(|\r|\n|$|;)")
RE_UNCLOSED_NEWLINE = re.compile(r"(?:\r?\n)+\s*\([a-zA-Z_#'{]")


def tokenize_dta(text: str) -> List[Tuple[str, Any]]:
    """
    Tokenizes Harmonix LISP DTA text into a list of (token_type, value) tuples.
    Handles comments (;), blocks ({...}), strings (\"...\"), parentheses, numbers, and symbols.
    """
    tokens: List[Tuple[str, Any]] = []
    i = 0
    n = len(text)
    
    # Strip UTF-8 BOM if present
    if text.startswith("\ufeff"):
        text = text[1:]
        n = len(text)

    while i < n:
        c = text[i]

        # Whitespace
        if c in " \t\r\n\x0c":
            i += 1
            continue

        # Comment (';' until end of line)
        if c == ";":
            while i < n and text[i] != "\n":
                i += 1
            continue

        # Directive ('#include', etc.)
        if c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue

        # Left Parenthesis
        if c == "(":
            tokens.append(("LPAREN", "("))
            i += 1
            continue

        # Right Parenthesis
        if c == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
            continue

        # Code / Expression Block: { ... }
        if c == "{":
            start = i
            depth = 1
            i += 1
            while i < n and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            block_content = text[start:i]
            tokens.append(("BLOCK", block_content))
            continue

        # Quoted String: " ... " with backslash escapes
        if c == '"':
            i += 1
            chars: List[str] = []
            while i < n:
                curr = text[i]
                if curr == "\\" and i + 1 < n:
                    next_char = text[i + 1]
                    if next_char == '"':
                        chars.append('"')
                    elif next_char == "\\":
                        chars.append("\\")
                    elif next_char == "n":
                        chars.append("\n")
                    elif next_char == "t":
                        chars.append("\t")
                    elif next_char == "r":
                        chars.append("\r")
                    else:
                        chars.append(next_char)
                    i += 2
                elif curr == '"':
                    i += 1
                    break
                elif curr == ")" and RE_UNCLOSED_PAREN.match(text, i):
                    # Gracefully terminate unclosed string before closing paren / next node
                    break
                elif curr in "\r\n" and RE_UNCLOSED_NEWLINE.match(text, i):
                    # Gracefully terminate unclosed string before next S-expression on new line
                    break
                else:
                    chars.append(curr)
                    i += 1
            tokens.append(("STRING", "".join(chars)))
            continue

        # Single-Quoted Symbol / String: ' ... '
        if c == "'":
            i += 1
            chars: List[str] = []
            while i < n:
                curr = text[i]
                if curr == "\\" and i + 1 < n:
                    next_char = text[i + 1]
                    if next_char == "'":
                        chars.append("'")
                    elif next_char == "\\":
                        chars.append("\\")
                    elif next_char == "n":
                        chars.append("\n")
                    elif next_char == "t":
                        chars.append("\t")
                    elif next_char == "r":
                        chars.append("\r")
                    else:
                        chars.append(next_char)
                    i += 2
                elif curr == "'":
                    i += 1
                    break
                elif curr == ")" and RE_UNCLOSED_PAREN.match(text, i):
                    # Gracefully terminate unclosed single-quoted string before closing paren / next node
                    break
                elif curr in "\r\n" and RE_UNCLOSED_NEWLINE.match(text, i):
                    # Gracefully terminate unclosed single-quoted string before next S-expression on new line
                    break
                else:
                    chars.append(curr)
                    i += 1
            tokens.append(("SYMBOL", "".join(chars)))
            continue

        # Unquoted atom: Symbol, Number, Boolean, or Identifier
        start = i
        while i < n and text[i] not in " \t\r\n\x0c();\"{}":
            i += 1
        raw_val = text[start:i]

        if not raw_val:
            continue

        # Check integer (decimal or hex)
        try:
            if raw_val.startswith(("0x", "0X")):
                tokens.append(("INT", int(raw_val, 16)))
            else:
                tokens.append(("INT", int(raw_val)))
            continue
        except ValueError:
            pass

        # Check float
        if "." in raw_val or "e" in raw_val or "E" in raw_val:
            try:
                tokens.append(("FLOAT", float(raw_val)))
                continue
            except ValueError:
                pass

        # Check boolean / special symbols
        if raw_val in ("TRUE", "true"):
            tokens.append(("BOOL", True))
        elif raw_val in ("FALSE", "false"):
            tokens.append(("BOOL", False))
        elif raw_val == "kDataUnhandled":
            tokens.append(("BOOL", "kDataUnhandled"))
        else:
            tokens.append(("SYMBOL", raw_val.strip("'\"")))

    return tokens


def parse_s_expressions(tokens: List[Tuple[str, Any]]) -> List[Any]:
    """
    Parses a token list into nested Python lists representing LISP S-expressions.
    """
    stack: List[List[Any]] = [[]]
    
    for tok_type, val in tokens:
        if tok_type == "LPAREN":
            new_list: List[Any] = []
            stack[-1].append(new_list)
            stack.append(new_list)
        elif tok_type == "RPAREN":
            if len(stack) > 1:
                stack.pop()
            else:
                # Extra closing paren, tolerate or ignore
                pass
        else:
            # Atom
            stack[-1].append(val)

    return stack[0]


def _find_field_in_tree(tree: Any, field_name: str) -> Optional[Any]:
    """Recursively searches for (field_name val...) in S-expression AST."""
    if not isinstance(tree, list):
        return None
    if len(tree) >= 2 and isinstance(tree[0], str):
        key = str(tree[0]).strip("'\"").lower()
        if key == field_name.lower():
            if len(tree) == 2:
                val = tree[1]
                if isinstance(val, str):
                    return val.strip("'\"")
                return val
            return tree[1:]
    for item in tree:
        if isinstance(item, list):
            res = _find_field_in_tree(item, field_name)
            if res is not None:
                return res
    return None


def extract_dta_metadata(dta_content: Union[str, bytes, List[Any]]) -> Dict[str, Any]:
    """
    Parses DTA text or AST and extracts a normalized metadata dictionary.
    
    Returns:
        dict containing:
            - 'id': str
            - 'name': str
            - 'artist': str
            - 'album': str
            - 'album_track_number': Optional[int]
            - 'year': int
            - 'genre': str
            - 'charter': str
            - 'song_length': int (ms)
            - 'preview': Tuple[int, int]
            - 'preview_start_time': int (ms)
            - 'preview_end_time': int (ms)
            - 'tracks': Dict[str, List[int]]
            - 'pans': List[float]
            - 'vols': List[float]
            - 'cores': List[int]
            - 'crowd_channels': List[int]
            - 'vocal_parts': int
            - 'ranks': Dict[str, int]
            - 'diff_tiers': Dict[str, int]
            - 'raw_ast': List[Any]
    """
    # 1. Decode text if bytes
    if isinstance(dta_content, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(dta_content)
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin1", errors="replace")
        tokens = tokenize_dta(text)
        ast = parse_s_expressions(tokens)
    elif isinstance(dta_content, str):
        tokens = tokenize_dta(dta_content)
        ast = parse_s_expressions(tokens)
    elif isinstance(dta_content, list):
        ast = dta_content
    else:
        raise TypeError(f"Unsupported DTA content type: {type(dta_content)}")

    # 2. Locate song root node
    # Usually ast is [ [song_id, [name, ...], ...] ] or [song_id, [name, ...], ...]
    song_node: List[Any] = []
    if len(ast) == 1 and isinstance(ast[0], list):
        song_node = ast[0]
    elif len(ast) > 1 and isinstance(ast[0], str):
        song_node = ast
    elif len(ast) > 0 and isinstance(ast[0], list):
        song_node = ast[0]
    else:
        song_node = ast

    # Song ID
    song_id = ""
    if song_node and isinstance(song_node[0], str):
        song_id = str(song_node[0]).strip("'\"")

    # Helper to lookup first-level or nested key
    def get_val(key: str, default: Any = None) -> Any:
        res = _find_field_in_tree(song_node, key)
        return res if res is not None else default

    # Basic fields
    name = str(get_val("name", song_id)).strip("'\"")
    artist = str(get_val("artist", "Unknown Artist")).strip("'\"")
    album = str(get_val("album_name", get_val("album", ""))).strip("'\"")
    
    # Year
    year_val = get_val("year_released", get_val("year", 0))
    try:
        year = int(year_val)
    except (ValueError, TypeError):
        year = 0

    # Album track number
    track_val = get_val("album_track_number", get_val("track_number", get_val("track", None)))
    track_num: Optional[int] = None
    if track_val is not None:
        try:
            track_num = int(track_val)
        except (ValueError, TypeError):
            track_num = None

    # Genre
    raw_genre = get_val("genre", "rock")
    if isinstance(raw_genre, list) and len(raw_genre) > 0:
        raw_genre = raw_genre[0]
    genre = normalize_genre(str(raw_genre) if raw_genre else "rock")

    # Author / Charter
    charter_val = get_val("author", get_val("charter", None))
    charter = str(charter_val).strip("'\"") if charter_val is not None else None

    # Song length (ms)
    len_val = get_val("song_length", 0)
    try:
        song_length = int(len_val)
    except (ValueError, TypeError):
        song_length = 0

    # Preview start/end
    preview_val = get_val("preview", [0, 0])
    preview_start = 0
    preview_end = 0
    if isinstance(preview_val, list) and len(preview_val) >= 2:
        try:
            preview_start = int(preview_val[0])
            preview_end = int(preview_val[1])
        except (ValueError, TypeError):
            pass
    elif isinstance(preview_val, (int, float)):
        preview_start = int(preview_val)
        preview_end = preview_start + 30000

    # Vocal parts
    vocal_parts_val = get_val("vocal_parts", 1)
    try:
        vocal_parts = int(vocal_parts_val)
    except (ValueError, TypeError):
        vocal_parts = 1

    # Audio Routing: tracks, pans, vols, cores, crowd_channels
    tracks: Dict[str, List[int]] = {}
    tracks_node = get_val("tracks", [])
    if isinstance(tracks_node, list) and len(tracks_node) > 0:
        # Normalize stem definitions list
        curr = tracks_node
        while (
            isinstance(curr, list)
            and len(curr) == 1
            and isinstance(curr[0], list)
            and len(curr[0]) > 0
            and isinstance(curr[0][0], list)
        ):
            curr = curr[0]

        if isinstance(curr, list) and len(curr) >= 2 and isinstance(curr[0], str):
            stem_items = [curr]
        elif isinstance(curr, list):
            stem_items = curr
        else:
            stem_items = []
        
        for item in stem_items:
            if isinstance(item, list) and len(item) >= 2:
                stem_name = str(item[0]).strip("'\"").lower()
                # Normalize stem name: drum -> drums/drum, bass, guitar, vocals, keys
                chans_node = item[1]
                channels: List[int] = []
                if isinstance(chans_node, list):
                    for ch in chans_node:
                        if isinstance(ch, (int, float)) and int(ch) >= 0:
                            channels.append(int(ch))
                elif isinstance(chans_node, (int, float)) and int(chans_node) >= 0:
                    channels.append(int(chans_node))
                tracks[stem_name] = channels

    # Pans
    pans_val = get_val("pans", [])
    pans: List[float] = []
    if isinstance(pans_val, list):
        for p in pans_val:
            if isinstance(p, (int, float)):
                pans.append(float(p))

    # Vols
    vols_val = get_val("vols", [])
    vols: List[float] = []
    if isinstance(vols_val, list):
        for v in vols_val:
            if isinstance(v, (int, float)):
                vols.append(float(v))

    # Cores
    cores_val = get_val("cores", [])
    cores: List[int] = []
    if isinstance(cores_val, list):
        for c in cores_val:
            if isinstance(c, (int, float)):
                cores.append(int(c))

    # Crowd channels
    crowd_val = get_val("crowd_channels", [])
    crowd_channels: List[int] = []
    if isinstance(crowd_val, list):
        for c in crowd_val:
            if isinstance(c, (int, float)) and int(c) >= 0:
                crowd_channels.append(int(c))
    elif isinstance(crowd_val, (int, float)) and int(crowd_val) >= 0:
        crowd_channels.append(int(crowd_val))

    # Ranks
    ranks: Dict[str, int] = {}
    rank_node = get_val("rank", [])
    if isinstance(rank_node, list):
        for item in rank_node:
            if isinstance(item, list) and len(item) >= 2:
                inst_name = str(item[0]).strip("'\"").lower()
                try:
                    ranks[inst_name] = int(item[1])
                except (ValueError, TypeError):
                    pass

    # Clone Hero Difficulty Tiers (0..6 or -1)
    diff_band = map_difficulty_rank("band", ranks.get("band"))
    diff_guitar = map_difficulty_rank("guitar", ranks.get("guitar"))
    diff_bass = map_difficulty_rank("bass", ranks.get("bass"))
    diff_drums = map_difficulty_rank("drum", ranks.get("drum", ranks.get("drums")))
    diff_drums_real = diff_drums
    diff_keys = map_difficulty_rank("keys", ranks.get("keys"))
    diff_keys_real = map_difficulty_rank("real_keys", ranks.get("real_keys", ranks.get("keys")))
    diff_vocals = map_difficulty_rank("vocals", ranks.get("vocals", ranks.get("vocal")))
    diff_vocals_harm = diff_vocals if vocal_parts > 1 else -1
    diff_guitar_real = map_difficulty_rank("real_guitar", ranks.get("real_guitar"))
    diff_bass_real = map_difficulty_rank("real_bass", ranks.get("real_bass"))

    diff_tiers: Dict[str, int] = {
        "band": diff_band,
        "guitar": diff_guitar,
        "guitarghl": -1,
        "bass": diff_bass,
        "bassghl": -1,
        "drums": diff_drums,
        "drums_real": diff_drums_real,
        "keys": diff_keys,
        "keys_real": diff_keys_real,
        "vocals": diff_vocals,
        "vocals_harm": diff_vocals_harm,
        "dance": -1,
        "bass_real": diff_bass_real,
        "guitar_real": diff_guitar_real,
        "guitar_coop": -1,
        "rhythm": -1,
        "drums_real_ps": -1,
        "keys_real_ps": -1,
        "guitar_pad": -1,
        "bass_pad": -1,
        "drums_pad": -1,
        "vocals_pad": -1,
        "keys_pad": -1,
    }

    return {
        "id": song_id,
        "name": name,
        "artist": artist,
        "album": album,
        "album_track_number": track_num,
        "year": year,
        "genre": genre,
        "charter": charter,
        "song_length": song_length,
        "preview": (preview_start, preview_end),
        "preview_start_time": preview_start,
        "preview_end_time": preview_end,
        "tracks": tracks,
        "pans": pans,
        "vols": vols,
        "cores": cores,
        "crowd_channels": crowd_channels,
        "vocal_parts": vocal_parts,
        "ranks": ranks,
        "diff_tiers": diff_tiers,
        "raw_ast": ast,
    }


def parse_dta(dta_content: Union[str, bytes]) -> Dict[str, Any]:
    """
    Main entry point for DTA parsing. Returns normalized song metadata dictionary.
    """
    return extract_dta_metadata(dta_content)
