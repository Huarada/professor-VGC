"""Vulture false positives (``vulture src vulture_whitelist.py``).

Each name is used, just not in a way vulture can see.
"""

BaseLlm  # only in quoted annotations under TYPE_CHECKING
mtime_ns  # theme._image_data_uri: part of the st.cache_data key
