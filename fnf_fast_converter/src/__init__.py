"""
fnf_fast_converter - High-Speed Native Rock Band CON to Clone Hero Converter
"""

try:
    from .stfs import STFSPackage, STFSError, STFSEntry, STFSHeader
except ImportError:
    pass

try:
    from .mogg import (
        MOGGDemuxer,
        MOGGHeader,
        AudioStem,
        parse_mogg_header,
        decode_mogg_pcm,
        mix_and_slice_channels,
        encode_vorbis_stem,
        canonical_stem_name,
    )
except ImportError:
    pass

try:
    from .dta import parse_dta, map_difficulty_rank, extract_dta_metadata
except ImportError:
    pass

try:
    from .image import decode_png_xbox, make_png_raw
except ImportError:
    pass

try:
    from .ini import generate_song_ini
except ImportError:
    pass

try:
    from .pipeline import (
        convert_con_to_song_folder,
        is_valid_converted_song,
        sanitize_folder_name,
        BatchConverter,
        BatchConversionProgress,
        BatchConversionStats,
    )
except ImportError:
    pass
