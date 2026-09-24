"""
Converts the Python-style flat-dict translation cache
({"arabic": "english", ...}) into the array-of-records format
ADF's Data Flow expects ([{"arabic_text": "...", "english_text": "..."}, ...]).

Usage:
    python3 convert_cache.py path/to/local/translation_cache.json path/to/output/translation_cache_for_adf.json
"""
import json
import sys

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 convert_cache.py <input_flat_dict.json> <output_array.json>")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]

    with open(input_path, "r", encoding="utf-8") as f:
        flat_cache = json.load(f)

    if not isinstance(flat_cache, dict):
        print("ERROR: input file is not a flat dict — check you pointed at the right file.")
        sys.exit(1)

    records = [
        {"arabic_text": arabic, "english_text": english}
        for arabic, english in flat_cache.items()
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(records)} entries.")
    print(f"Wrote: {output_path}")

if __name__ == "__main__":
    main()
