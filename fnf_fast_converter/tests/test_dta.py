"""
Unit tests for DTA parser, lexer, AST engine, and difficulty mapper.
"""

import pytest
import time
from pathlib import Path
from fnf_fast_converter.src.dta import (
    tokenize_dta,
    parse_s_expressions,
    parse_dta,
    extract_dta_metadata,
    map_difficulty_rank,
    normalize_genre,
    DIFF_CUTOFFS,
)

SAMPLE_BUDDY_HOLLY_DTA = """
; Buddy Holly DTA
(buddyhollyfnf
   (name "Buddy Holly")
   (artist "Weezer")
   (master TRUE)
   (context 104)
   (song
      (name songs/buddyhollyfnf/buddyhollyfnf)
      (tracks
         (
            (drum (0 1))
            (bass (2 3))
            (guitar (4 5))
            (vocals (6 7))
         )
      )
      (pans (-1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0))
      (vols (0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0))
      (cores (-1 -1 -1 -1 1 1 -1 -1 -1 -1))
      (drum_solo (seqs (kick.cue snare.cue tom1.cue tom2.cue crash.cue)))
      (drum_freestyle (seqs (kick.cue snare.cue hat.cue ride.cue crash.cue)))
      (midi_file songs/buddyhollyfnf/buddyhollyfnf.mid)
      (vocal_parts 1)
   )
   (song_scroll_speed 2100)
   (bank sfx/tambourine_bank.milo)
   (anim_tempo kTempoMedium)
   (preview 83313 111920)
   (rank
      (band 215)
      (guitar 139)
      (drum 124)
      (bass 135)
      (vocals 218)
   )
   (song_id 4209530)
   (song_length 162727)
   (solo (guitar))
   (format 4)
   (game_origin fnfestival)
   (rating 1)
   (year_released 1994)
   (album_art 1)
   (album_name "Weezer (The Blue Album)")
   (author "Harmonix, Rhythm Authors")
   (album_track_number 4)
   (genre alternative)
   (decade the90s)
   (downloaded TRUE)
   (version 0)
   (vocal_gender male)
)
"""

SAMPLE_MOVE_DTA = """
(moveadapted
   (name "Move (Adapted)")
   (artist "1K Phew, Lecrae")
   (album_name "No Church In A While")
   (master 1)
   (song
      (name songs/moveadapted/moveadapted)
      (tracks
         (
            (drum (0 1))
            (bass (2 3))
            (guitar (4 5))
            (vocals (6 7))
         )
      )
      (pans (-1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0 -1.0 1.0))
      (vols (0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0))
      (cores (-1 -1 -1 -1 1 1 -1 -1 -1 -1))
      (vocal_parts 1)
      (drum_solo (seqs (kick.cue snare.cue tom1.cue tom2.cue crash.cue)))
      (drum_freestyle (seqs (kick.cue snare.cue hat.cue ride.cue crash.cue)))
      (midi_file songs/moveadapted/moveadapted.mid)
   )
   (song_scroll_speed 2300)
   (bank "sfx/tambourine_bank.milo")
   (anim_tempo kTempoMedium)
   (song_length 142153)
   (preview 10476 40476)
   (rank
      (band 1)
      (guitar 1)
      (drum 151)
      (bass 1)
      (vocals 1)
   )
   (genre hiphoprap)
   (vocal_gender male)
   (version 0)
   (format 4)
   (encoding utf8)
   (album_art 1)
   (year_released 2021)
   (rating 1)
   (song_id 4210129)
   (tuning_offset_cents 0.0)
)
"""

COMPLEX_EXPR_DTA = """
#include song_extra.dta
; Leading comment with special characters: !@#$%^&*()
(complex_song
   (name "Song \\"With\\" Quotes \\\\ and \\n Escapes")
   (artist "Complex Artist")
   (fake {== $SONG_VERSION 0})
   (context {if_else $TEST 100 200})
   (nested_block {outer {inner 42}})
   (tracks
      (
         (drum (0 1))
         (bass (2))
         (guitar (3 4 5))
         (keys (6 7))
      )
   )
   (crowd_channels 8 9)
   (pans (-1.0 1.0 0.0 -0.8 0.0 0.8 -1.0 1.0 -1.0 1.0))
   (vols (0.0 0.0 -1.5 0.5 -0.5 0.5 0.0 0.0 -3.0 -3.0))
   (cores (-1 -1 -1 -1 -1 -1 1 1 -1 -1))
   (vocal_parts 3)
   (rank
      (band 350)
      (guitar 415)
      (drum 450)
      (bass 440)
      (vocals 430)
      (keys 300)
      (real_guitar 250)
      (real_bass 200)
      (real_keys 300)
   )
   (song_length 240000)
   (preview 60000 90000)
   (genre popdanceelectronic)
   (year_released 2024)
   (album_name "Complex Electronic Beats")
   (author "Test Charter")
   (album_track_number 7)
)
"""


class TestDTALexerAndParser:
    def test_tokenize_basic(self):
        text = '(name "Test Song") (year 2020)'
        tokens = tokenize_dta(text)
        assert tokens == [
            ('LPAREN', '('),
            ('SYMBOL', 'name'),
            ('STRING', 'Test Song'),
            ('RPAREN', ')'),
            ('LPAREN', '('),
            ('SYMBOL', 'year'),
            ('INT', 2020),
            ('RPAREN', ')'),
        ]

    def test_tokenize_escapes_and_comments(self):
        text = '; comment line\n(name "Hello \\"World\\" \\\\ \n newline") ; inline comment'
        tokens = tokenize_dta(text)
        assert len(tokens) == 4
        assert tokens[0] == ('LPAREN', '(')
        assert tokens[1] == ('SYMBOL', 'name')
        assert tokens[2] == ('STRING', 'Hello "World" \\ \n newline')
        assert tokens[3] == ('RPAREN', ')')

    def test_tokenize_blocks_and_directives(self):
        text = '#include foo.dta\n(context {== $SONG_VERSION 0})'
        tokens = tokenize_dta(text)
        assert tokens == [
            ('LPAREN', '('),
            ('SYMBOL', 'context'),
            ('BLOCK', '{== $SONG_VERSION 0}'),
            ('RPAREN', ')'),
        ]

    def test_utf8_bom_handling(self):
        text_with_bom = '\ufeff(name "BOM Song")'
        tokens = tokenize_dta(text_with_bom)
        assert tokens[1] == ('SYMBOL', 'name')
        assert tokens[2] == ('STRING', 'BOM Song')

    def test_number_types(self):
        text = '(int -42) (float +3.1415) (hex 0xFF) (scientific 1e-4)'
        tokens = tokenize_dta(text)
        assert ('INT', -42) in tokens
        assert ('FLOAT', 3.1415) in tokens
        assert ('INT', 255) in tokens
        assert ('FLOAT', 1e-4) in tokens

    def test_parse_s_expressions(self):
        text = '(song (name "Test") (ranks (guitar 100) (bass 120)))'
        tokens = tokenize_dta(text)
        ast = parse_s_expressions(tokens)
        assert isinstance(ast, list)
        assert len(ast) == 1
        inner = ast[0]
        assert inner[0] == 'song'
        assert inner[1] == ['name', 'Test']
        assert inner[2] == ['ranks', ['guitar', 100], ['bass', 120]]


class TestDifficultyMapper:
    def test_guitar_cutoffs(self):
        cutoffs = DIFF_CUTOFFS["guitar"]  # [139, 176, 221, 267, 333, 409]
        assert map_difficulty_rank("guitar", None) == -1
        assert map_difficulty_rank("guitar", 0) == -1
        assert map_difficulty_rank("guitar", -5) == -1
        assert map_difficulty_rank("guitar", 1) == 0
        assert map_difficulty_rank("guitar", 138) == 0
        assert map_difficulty_rank("guitar", 139) == 1
        assert map_difficulty_rank("guitar", 175) == 1
        assert map_difficulty_rank("guitar", 176) == 2
        assert map_difficulty_rank("guitar", 220) == 2
        assert map_difficulty_rank("guitar", 221) == 3
        assert map_difficulty_rank("guitar", 266) == 3
        assert map_difficulty_rank("guitar", 267) == 4
        assert map_difficulty_rank("guitar", 332) == 4
        assert map_difficulty_rank("guitar", 333) == 5
        assert map_difficulty_rank("guitar", 408) == 5
        assert map_difficulty_rank("guitar", 409) == 6
        assert map_difficulty_rank("guitar", 500) == 6

    def test_all_instruments(self):
        # Bass: [135, 181, 228, 293, 364, 436]
        assert map_difficulty_rank("bass", 135) == 1
        assert map_difficulty_rank("bass", 134) == 0

        # Drum: [124, 151, 178, 242, 345, 448]
        assert map_difficulty_rank("drum", 124) == 1
        assert map_difficulty_rank("drums", 151) == 2
        assert map_difficulty_rank("drum", 175) == 2

        # Vocals: [132, 175, 218, 279, 353, 427]
        assert map_difficulty_rank("vocals", 218) == 3
        assert map_difficulty_rank("vocal", 1) == 0

        # Band: [163, 215, 243, 267, 292, 345]
        assert map_difficulty_rank("band", 215) == 2
        assert map_difficulty_rank("band", 1) == 0


class TestGenreNormalization:
    def test_known_genres(self):
        assert normalize_genre("alternative") == "Alternative"
        assert normalize_genre("hiphoprap") == "Hip-Hop/Rap"
        assert normalize_genre("classicrock") == "Classic Rock"
        assert normalize_genre("popdanceelectronic") == "Pop/Dance/Electronic"
        assert normalize_genre("numetal") == "Nu-Metal"
        assert normalize_genre("rbsoulfunk") == "R&B/Soul/Funk"

    def test_fallback_genres(self):
        assert normalize_genre("synth_pop") == "Synth Pop"
        assert normalize_genre(None) == "Rock"
        assert normalize_genre("") == "Rock"


class TestMetadataExtraction:
    def test_buddy_holly_metadata(self):
        meta = parse_dta(SAMPLE_BUDDY_HOLLY_DTA)
        assert meta["id"] == "buddyhollyfnf"
        assert meta["name"] == "Buddy Holly"
        assert meta["artist"] == "Weezer"
        assert meta["album"] == "Weezer (The Blue Album)"
        assert meta["year"] == 1994
        assert meta["genre"] == "Alternative"
        assert meta["charter"] == "Harmonix, Rhythm Authors"
        assert meta["song_length"] == 162727
        assert meta["preview_start_time"] == 83313
        assert meta["preview_end_time"] == 111920
        assert meta["album_track_number"] == 4
        assert meta["vocal_parts"] == 1

        # Tracks routing
        assert meta["tracks"]["drum"] == [0, 1]
        assert meta["tracks"]["bass"] == [2, 3]
        assert meta["tracks"]["guitar"] == [4, 5]
        assert meta["tracks"]["vocals"] == [6, 7]

        # Difficulty tiers
        assert meta["diff_tiers"]["band"] == 2
        assert meta["diff_tiers"]["guitar"] == 1
        assert meta["diff_tiers"]["bass"] == 1
        assert meta["diff_tiers"]["drums"] == 1
        assert meta["diff_tiers"]["drums_real"] == 1
        assert meta["diff_tiers"]["vocals"] == 3
        assert meta["diff_tiers"]["vocals_harm"] == -1
        assert meta["diff_tiers"]["keys"] == -1

    def test_move_metadata(self):
        meta = parse_dta(SAMPLE_MOVE_DTA)
        assert meta["id"] == "moveadapted"
        assert meta["name"] == "Move (Adapted)"
        assert meta["artist"] == "1K Phew, Lecrae"
        assert meta["album"] == "No Church In A While"
        assert meta["year"] == 2021
        assert meta["genre"] == "Hip-Hop/Rap"
        assert meta["song_length"] == 142153
        assert meta["preview_start_time"] == 10476
        assert meta["preview_end_time"] == 40476
        assert meta["album_track_number"] is None

        # Difficulty tiers
        assert meta["diff_tiers"]["band"] == 0
        assert meta["diff_tiers"]["guitar"] == 0
        assert meta["diff_tiers"]["bass"] == 0
        assert meta["diff_tiers"]["drums"] == 2
        assert meta["diff_tiers"]["drums_real"] == 2
        assert meta["diff_tiers"]["vocals"] == 0

    def test_complex_expressions_and_harmonies(self):
        meta = parse_dta(COMPLEX_EXPR_DTA)
        assert meta["id"] == "complex_song"
        assert meta["name"] == 'Song "With" Quotes \\ and \n Escapes'
        assert meta["genre"] == "Pop/Dance/Electronic"
        assert meta["vocal_parts"] == 3
        assert meta["crowd_channels"] == [8, 9]

        # Ranks with Devil Tiers (6) and Harmonies
        assert meta["diff_tiers"]["band"] == 6  # 350 >= 345
        assert meta["diff_tiers"]["guitar"] == 6  # 415 >= 409
        assert meta["diff_tiers"]["drums"] == 6  # 450 >= 448
        assert meta["diff_tiers"]["bass"] == 6  # 440 >= 436
        assert meta["diff_tiers"]["vocals"] == 6  # 430 >= 427
        assert meta["diff_tiers"]["vocals_harm"] == 6  # vocal_parts=3 > 1 -> matches vocals tier!
        assert meta["diff_tiers"]["keys"] == 3  # 300 in [269, 327]

    def test_bytes_and_encoding_fallbacks(self):
        # UTF-8 with BOM bytes
        dta_bytes = ('\ufeff' + SAMPLE_BUDDY_HOLLY_DTA).encode('utf-8')
        meta = parse_dta(dta_bytes)
        assert meta["name"] == "Buddy Holly"

        # Latin-1 bytes
        latin1_dta = '(song_id (name "Café con Leche") (artist "José") (rank (guitar 150)))'.encode('iso-8859-1')
        meta_lat = parse_dta(latin1_dta)
        assert meta_lat["name"] == "Café con Leche"
        assert meta_lat["artist"] == "José"

    def test_parsing_performance(self):
        t0 = time.perf_counter()
        iters = 500
        for _ in range(iters):
            parse_dta(SAMPLE_BUDDY_HOLLY_DTA)
        t1 = time.perf_counter()
        avg_ms = (t1 - t0) / iters * 1000
        print(f"Average DTA parse time: {avg_ms:.3f} ms")
        assert avg_ms < 1.0  # Must be sub-millisecond


class TestDTARobustnessRemediation:
    """Dedicated test cases for M5 DTA lexer & parser hardening."""

    def test_unclosed_string_quote_recovery_multiline(self):
        """Verify unclosed double quote on a line does not eat subsequent nodes across lines."""
        dta = """
        (song_id
            (name "Unclosed Title Line)
            (artist "Legit Artist")
            (album_name "Legit Album")
            (year_released 1999)
            (tracks ((drum (0 1))))
        )
        """
        meta = parse_dta(dta)
        assert meta["name"] == "Unclosed Title Line"
        assert meta["artist"] == "Legit Artist"
        assert meta["album"] == "Legit Album"
        assert meta["year"] == 1999
        assert meta["tracks"]["drum"] == [0, 1]

    def test_unclosed_string_quote_recovery_single_line(self):
        """Verify unclosed double quote on a single line recovers before next S-expression."""
        dta = '(song (name "Single Line Unclosed) (artist "Real Artist") (year_released 2005))'
        meta = parse_dta(dta)
        assert meta["name"] == "Single Line Unclosed"
        assert meta["artist"] == "Real Artist"
        assert meta["year"] == 2005

    def test_single_quoted_symbols_and_keys(self):
        """Verify single-quoted identifiers ('name', 'artist', 'genre') match clean dictionary keys."""
        dta = "('custom_song' ('name' 'Rock Hero') ('artist' 'Super Band') ('album' 'First Album') ('year' 2008) ('genre' 'classicrock'))"
        meta = parse_dta(dta)
        assert meta["id"] == "custom_song"
        assert meta["name"] == "Rock Hero"
        assert meta["artist"] == "Super Band"
        assert meta["album"] == "First Album"
        assert meta["year"] == 2008
        assert meta["genre"] == "Classic Rock"

    def test_negative_channel_filtering(self):
        """Verify negative channels (-1, -100) are filtered out from tracks and crowd channels."""
        dta = """
        (test_channels
            (name "Channel Test")
            (tracks (
                (drum (-1 0 1 -5 2))
                (bass (-1 3))
                (guitar (-10))
                (vocals (4 5))
            ))
            (crowd_channels -1 6 7 -2)
        )
        """
        meta = parse_dta(dta)
        assert meta["tracks"]["drum"] == [0, 1, 2]
        assert meta["tracks"]["bass"] == [3]
        assert meta["tracks"]["guitar"] == []
        assert meta["tracks"]["vocals"] == [4, 5]
        assert meta["crowd_channels"] == [6, 7]

    def test_single_quoted_escapes_and_unicode(self):
        """Verify single-quoted symbols with escapes and unicode are parsed properly."""
        dta = "('song_test' ('name' 'Don\\'t Stop \\'Til You Get Enough') ('artist' 'Michael Jackson') ('rank' (('guitar' 200) ('bass' 150))))"
        meta = parse_dta(dta)
        assert meta["name"] == "Don't Stop 'Til You Get Enough"
        assert meta["artist"] == "Michael Jackson"
        assert meta["ranks"]["guitar"] == 200
        assert meta["ranks"]["bass"] == 150

